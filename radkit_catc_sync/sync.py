"""Core sync logic for reconciling CatC inventory with RADKit."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from radkit_service.control_api import APIResult, ControlAPI

from .builders import build_metadata, build_new_device, build_update_device, get_device_type
from .catc_client import CatCClient
from .config import AppConfig
from .filters import FilterSet
from .models import CatCDevice, StoredRadkitDevice
from .stats import Stats

logger = logging.getLogger(__name__)


class CatCInventoryError(RuntimeError):
    """Raised when CatC inventory retrieval fails (auth, connection, or HTTP error).

    Aborts the sync so that a partial/empty inventory never triggers deletion of
    managed RADKit devices that are merely unreachable.
    """


def normalise_name(fqdn: str) -> str:
    """
    Normalize a hostname to RADKit naming requirements.

    RADKit device names must contain only lower-case letters, digits and
    dashes and must not start or end with a dash.

    Args:
        fqdn: Fully-qualified domain name or hostname.

    Returns:
        Normalized name suitable for RADKit.
    """
    import re

    short = fqdn.split(".")[0].lower()
    # Replace any character that isn't a-z, 0-9 or dash with a dash
    sanitised = re.sub(r"[^a-z0-9-]", "-", short)
    # Collapse consecutive dashes and strip leading/trailing dashes
    sanitised = re.sub(r"-{2,}", "-", sanitised).strip("-")
    return sanitised


def require_api_result_ok(result: APIResult, action: str) -> Any:
    """Return the typed API result payload or raise on RADKit API errors."""
    if APIResult.is_error(result):
        raise RuntimeError(
            f"RADKit ControlAPI failed to {action}: {result.root.message} ({result.root.detail})"
        )
    return result.result


def fetch_radkit_devices(
    api: ControlAPI,
    config: AppConfig,
) -> tuple[dict[str, StoredRadkitDevice], dict[str, StoredRadkitDevice]]:
    """
    Fetch all RADKit devices and split into managed and unmanaged.

    Args:
        api: RADKit ControlAPI instance.
        config: Application config (for meta_source_key).

    Returns:
        Tuple of (managed_devices, unmanaged_devices) dicts keyed by device name.
        Managed = devices with non-empty meta_source_key metadata.
    """
    devices = require_api_result_ok(api.list_devices(), "list devices")
    managed: dict[str, StoredRadkitDevice] = {}
    unmanaged: dict[str, StoredRadkitDevice] = {}

    for dev in devices or []:
        meta = {m.key: m.value for m in (dev.meta_data or [])}
        stored = StoredRadkitDevice(
            name=dev.name,
            uuid=str(dev.uuid),
            host=getattr(dev, "host", ""),
            device_type=str(getattr(dev, "device_type", "")),
            catc_source=meta.get(config.meta_source_key, ""),
            metadata=meta,
        )
        if meta.get(config.meta_source_key):
            managed[dev.name] = stored
        else:
            unmanaged[dev.name] = stored

    return managed, unmanaged


def fetch_fresh_inventory(
    config: AppConfig,
    filters: FilterSet,
    catc_user: str,
    catc_password: str,
    stats: Stats,
) -> dict[str, tuple[CatCDevice, str]]:
    """
    Fetch devices from all CatC clusters.

    Args:
        config: Application config.
        filters: Filter set for device name matching.
        catc_user: Catalyst Center username.
        catc_password: Catalyst Center password.
        stats: Stats tracker.

    Returns:
        Dictionary {radkit_name: (device, catc_hostname)}.
        Cross-cluster hostname collisions log warnings; first cluster wins.
    """
    fresh: dict[str, tuple[CatCDevice, str]] = {}

    for cluster_url in config.catc_clusters:
        client = CatCClient(
            base_url=cluster_url,
            username=catc_user,
            password=catc_password,
            verify_tls=config.catc_verify_tls,
        )
        catc_hostname = client.hostname
        try:
            client.authenticate()
            devices = client.get_devices()
        except Exception as exc:
            # Abort the whole sync on any cluster fetch failure (auth, connection,
            # HTTP error). Proceeding with a partial/empty inventory would cause
            # Step 5 to delete managed devices that are merely unreachable, not gone.
            msg = f"Failed to fetch inventory from {catc_hostname}: {exc}"
            logger.error(msg)
            raise CatCInventoryError(msg) from exc

        logger.info("Fetched %d devices from CatC %s", len(devices), catc_hostname)

        for device in devices:
            if device.hostname is None:
                logger.info(
                    "Skipping device without hostname at %s",
                    device.management_ip,
                )
                stats.skipped += 1
                continue

            # Filter on raw FQDN before normalisation
            if not filters.should_import(device.hostname):
                logger.debug("Filtered out device %s", device.hostname)
                stats.skipped += 1
                continue

            radkit_name = normalise_name(device.hostname)

            if radkit_name in fresh:
                existing_dev, existing_source = fresh[radkit_name]
                if existing_source == catc_hostname and device.hostname == existing_dev.hostname:
                    msg = (
                        f"Duplicate device '{device.hostname}' returned by "
                        f"{catc_hostname} — skipping duplicate entry."
                    )
                elif existing_source == catc_hostname:
                    msg = (
                        f"Hostname collision: '{device.hostname}' and "
                        f"'{existing_dev.hostname}' both normalise to "
                        f"'{radkit_name}' on {catc_hostname}. Keeping first."
                    )
                else:
                    msg = (
                        f"Hostname collision: '{radkit_name}' exists in both "
                        f"'{existing_source}' and '{catc_hostname}'. Keeping first."
                    )
                logger.warning(msg)
                stats.warnings.append(msg)
                stats.skipped += 1
                continue

            fresh[radkit_name] = (device, catc_hostname)

    return fresh


def run_sync(
    config: AppConfig,
    dry_run: bool,
    update_passwords: bool,
    catc_user: str,
    catc_password: str,
    radkit_admin_user: str,
    radkit_admin_password: str,
    ssh_user: str,
    ssh_password: str,
) -> Stats:
    """
    Execute the sync operation.

    Args:
        config: Application config.
        dry_run: If True, don't modify RADKit.
        update_passwords: If True, overwrite SSH passwords on existing devices.
        catc_user: Catalyst Center username.
        catc_password: Catalyst Center password.
        radkit_admin_user: RADKit admin username.
        radkit_admin_password: RADKit admin password.
        ssh_user: SSH username for imported devices.
        ssh_password: SSH password for imported devices.

    Returns:
        Stats object with operation results.

    Raises:
        ValueError: If config is invalid (e.g., no clusters configured).
    """
    stats = Stats()

    # Build filter set
    filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)

    if not config.catc_clusters:
        raise ValueError("CATC_CLUSTERS is empty — add at least one cluster URL to the config.")

    synced_hostnames = {urlparse(u).hostname for u in config.catc_clusters}

    logger.info("Connecting to RADKit ControlAPI at %s", config.radkit_base_url)
    with ControlAPI.create(
        base_url=config.radkit_base_url,
        admin_name=radkit_admin_user,
        admin_password=radkit_admin_password,
    ) as api:
        # Step 1: get all devices from RADKit, split into managed / unmanaged
        logger.info("Fetching existing devices from RADKit...")
        managed, unmanaged = fetch_radkit_devices(api, config)
        logger.info(
            "Found %d catc-managed and %d unmanaged devices in RADKit",
            len(managed),
            len(unmanaged),
        )

        # Step 2: fetch fresh inventory from all clusters
        logger.info("Fetching inventory from %d CatC cluster(s)...", len(config.catc_clusters))
        fresh = fetch_fresh_inventory(config, filters, catc_user, catc_password, stats)
        logger.info("Total importable devices across all clusters: %d", len(fresh))

        # Step 3: add new devices / adopt unmanaged conflicts
        to_add = [name for name in fresh if name not in managed]
        logger.info("Devices to add (or adopt): %d", len(to_add))
        for name in to_add:
            device, catc_hostname = fresh[name]

            if name in unmanaged:
                if not config.adopt_existing:
                    msg = (
                        f"Skipping '{name}': already exists in RADKit as an unmanaged "
                        f"device. Use --adopt-existing (-A) to take ownership."
                    )
                    logger.warning(msg)
                    stats.warnings.append(msg)
                    stats.skipped += 1
                    continue
                existing_uuid = unmanaged[name].uuid
                upd = build_update_device(
                    device=device,
                    catc_hostname=catc_hostname,
                    existing_uuid=existing_uuid,
                    update_passwords=update_passwords,
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
                    metadata_fields=config.metadata_fields,
                    meta_source_key=config.meta_source_key,
                )
                if dry_run:
                    logger.info(
                        "[DRY-RUN] Would adopt unmanaged device '%s' (IP: %s, type: %s)",
                        name,
                        device.management_ip,
                        upd.device_type,
                    )
                    stats.adopted += 1
                else:
                    try:
                        require_api_result_ok(api.update_device(upd), f"adopt device '{name}'")
                        logger.info(
                            "Adopted unmanaged device '%s' (IP: %s)",
                            name,
                            device.management_ip,
                        )
                        stats.adopted += 1
                    except Exception as exc:
                        logger.error("Failed to adopt device '%s': %s", name, exc)
                        stats.errors += 1
                continue

            new_dev = build_new_device(
                device=device,
                catc_hostname=catc_hostname,
                ssh_user=ssh_user,
                ssh_password=ssh_password,
                radkit_name=name,
                metadata_fields=config.metadata_fields,
                meta_source_key=config.meta_source_key,
            )
            if dry_run:
                logger.info(
                    "[DRY-RUN] Would add device '%s' (IP: %s, type: %s)",
                    name,
                    device.management_ip,
                    new_dev.device_type,
                )
                stats.added += 1
            else:
                try:
                    require_api_result_ok(api.create_device(new_dev), f"create device '{name}'")
                    logger.info("Added device '%s' (IP: %s)", name, device.management_ip)
                    stats.added += 1
                except Exception as exc:
                    logger.error("Failed to add device '%s': %s", name, exc)
                    stats.errors += 1

        # Step 4: update existing managed devices (only if something changed)
        to_update = [name for name in fresh if name in managed]
        logger.info("Devices to check for updates: %d", len(to_update))
        for name in to_update:
            device, catc_hostname = fresh[name]
            existing = managed[name]
            existing_uuid = existing.uuid

            # Compute what would change
            reasons: list[str] = []
            new_ip = device.management_ip
            new_type = str(
                get_device_type(software_type=device.software_type, series=device.series)
            )
            if existing.host and existing.host != new_ip:
                reasons.append(f"IP {existing.host} -> {new_ip}")
            if existing.device_type and existing.device_type != new_type:
                reasons.append(f"type {existing.device_type} -> {new_type}")
            if existing.catc_source != catc_hostname:
                reasons.append(f"source {existing.catc_source} -> {catc_hostname}")
            if update_passwords:
                reasons.append("password refresh")

            # Compare metadata
            fresh_meta = {
                m.key: m.value
                for m in build_metadata(
                    device, catc_hostname, config.metadata_fields, config.meta_source_key
                )
            }
            existing_meta: dict[str, str] = existing.metadata
            if fresh_meta != existing_meta:
                changed_keys = sorted(
                    k
                    for k in fresh_meta.keys() | existing_meta.keys()
                    if fresh_meta.get(k) != existing_meta.get(k)
                )
                reasons.append(f"metadata ({', '.join(changed_keys)})")

            if not reasons:
                logger.debug("No changes for device '%s' — skipping update", name)
                stats.unchanged += 1
                continue

            upd = build_update_device(
                device=device,
                catc_hostname=catc_hostname,
                existing_uuid=existing_uuid,
                update_passwords=update_passwords,
                ssh_user=ssh_user,
                ssh_password=ssh_password,
                metadata_fields=config.metadata_fields,
                meta_source_key=config.meta_source_key,
            )

            if dry_run:
                logger.info(
                    "[DRY-RUN] Would update device '%s' (IP: %s, type: %s) — %s",
                    name,
                    device.management_ip,
                    upd.device_type,
                    "; ".join(reasons),
                )
                stats.updated += 1
            else:
                try:
                    require_api_result_ok(api.update_device(upd), f"update device '{name}'")
                    logger.debug("Updated device '%s'", name)
                    stats.updated += 1
                except Exception as exc:
                    logger.error("Failed to update device '%s': %s", name, exc)
                    stats.errors += 1

        # Step 5: delete managed devices no longer present in CatC
        # Scoped to clusters that were actually synced this run.
        # This includes devices removed from CatC AND devices excluded
        # by whitelist/blacklist filters — narrowing filters removes
        # previously-synced devices from RADKit.
        gone = [
            name
            for name, info in managed.items()
            if name not in fresh and info.catc_source in synced_hostnames
        ]

        logger.info("Devices to delete: %d", len(gone))
        for name in gone:
            existing_uuid = managed[name].uuid
            catc_source = managed[name].catc_source
            if dry_run:
                logger.info("[DRY-RUN] Would delete device '%s' (source: %s)", name, catc_source)
                stats.deleted += 1
            else:
                try:
                    require_api_result_ok(
                        api.delete_device(existing_uuid), f"delete device '{name}'"
                    )
                    logger.info("Deleted device '%s' (source: %s)", name, catc_source)
                    stats.deleted += 1
                except Exception as exc:
                    logger.error("Failed to delete device '%s': %s", name, exc)
                    stats.errors += 1

    return stats
