"""Core sync logic for reconciling CatC inventory with RADKit."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from radkit_service.control_api import APIError, AuthenticationError, ControlAPI

from .apiutils import require_api_result_ok
from .builders import build_metadata, build_new_device, build_update_device, get_device_type
from .catc_client import CatCClient
from .config import AppConfig
from .filters import FilterDecision, FilterSet
from .labels import compute_labels_to_add, ensure_labels_exist
from .models import CatCDevice, StoredRadkitDevice
from .naming import NameNormaliser
from .stats import SkipReason, Stats

logger = logging.getLogger(__name__)


class RenameGuardError(RuntimeError):
    """Raised when a run would rename more managed devices than ``rename_limit``.

    Renames are applied in place via ``UpdateDevice.name``, so the device UUID
    and everything attached to it survive. They are still worth gating: a mass
    rename is almost always an accidental change to ``[sync.naming]`` rather
    than a genuine bulk hostname change in Catalyst Center, and anything that
    refers to these devices by name (scripts, saved sessions, runbooks) will
    need updating. The run is aborted before anything is applied.
    """


class CatCInventoryError(RuntimeError):
    """Raised when CatC inventory retrieval fails (auth, connection, or HTTP error).

    Aborts the sync so that a partial/empty inventory never triggers deletion of
    managed RADKit devices that are merely unreachable.
    """


class RadkitInventoryError(RuntimeError):
    """Raised when the initial RADKit inventory fetch fails.

    Covers a bad admin credential, an unreachable service, and API errors
    raised before any change has been applied. Aborts the sync so that a
    missing inventory is never mistaken for an empty one.
    """


def _describe_radkit_failure(exc: BaseException, base_url: str) -> str:
    """Turn a RADKit client exception into something a user can act on."""
    if isinstance(exc, AuthenticationError):
        return (
            f"RADKit rejected the admin credentials for {base_url} ({exc}). "
            f"Check RADKIT_ADMIN_USER and RADKIT_ADMIN_PASSWORD."
        )
    if isinstance(exc, ConnectionError):
        return (
            f"Could not reach the RADKit service at {base_url} ({exc}). "
            f"Check that the service is running and that radkit.base_url is correct."
        )
    return f"RADKit API error while reading the device inventory from {base_url}: {exc}"


def _identity_of_stored(device: StoredRadkitDevice) -> str | None:
    """Best available stable identity for a device already in RADKit.

    Prefers the Catalyst Center device id carried in metadata; falls back to
    the management IP when ``id`` is not among the synced metadata fields.
    """
    return device.metadata.get("id") or device.host or None


def _identity_of_catc(device: CatCDevice) -> str | None:
    """Best available stable identity for a device fetched from Catalyst Center."""
    return device.device_id or device.management_ip or None


def detect_renames(
    managed: dict[str, StoredRadkitDevice],
    fresh: dict[str, tuple[CatCDevice, str]],
    gone: list[str],
    to_add: list[str],
) -> list[tuple[str, str]]:
    """
    Identify delete/add pairs that are really the same device under a new name.

    A device that disappears from the managed set while the *same physical
    device* reappears under a different name is a rename, not a genuine
    delete plus a genuine add. Devices are matched on their Catalyst Center
    id (or management IP as a fallback). Callers apply these as in-place
    updates rather than a delete followed by a create.

    Args:
        managed: Devices currently owned by this tool, keyed by RADKit name.
        fresh: Importable CatC devices, keyed by their new RADKit name.
        gone: Managed names scheduled for deletion.
        to_add: New names scheduled for creation.

    Returns:
        List of (old_name, new_name) pairs, sorted by old name.
    """
    added_by_identity: dict[str, str] = {}
    for name in to_add:
        identity = _identity_of_catc(fresh[name][0])
        if identity:
            added_by_identity[identity] = name

    renames: list[tuple[str, str]] = []
    for old_name in gone:
        identity = _identity_of_stored(managed[old_name])
        if identity and identity in added_by_identity:
            renames.append((old_name, added_by_identity[identity]))

    return sorted(renames)


def _apply_bulk(
    api_call: Callable[[list[Any]], Any],
    items: list[Any],
    names: list[str],
    action: str,
    batch_size: int,
) -> tuple[int, int]:
    """
    Apply a bulk ControlAPI operation in chunks and tally results.

    Each chunk is a single ControlAPI round-trip returning a ``BulkResult`` whose
    items are ordered 1:1 with the request, so per-item errors are mapped back to
    device names for logging.

    Args:
        api_call: Bulk ControlAPI callable, e.g. ``api.create_devices``.
        items: Request models (NewDevice / UpdateDevice / UUID) to send.
        names: Device names aligned 1:1 with ``items`` (for logging).
        action: Verb for log messages ("add", "update", "adopt", "delete").
        batch_size: Max number of items per ControlAPI call.

    Returns:
        Tuple of (success_count, error_count) across all chunks.
    """
    success = 0
    errors = 0
    step = max(1, batch_size)
    for start in range(0, len(items), step):
        chunk = items[start : start + step]
        chunk_names = names[start : start + step]
        try:
            result = api_call(chunk)
        except Exception as exc:
            # Whole-chunk transport/protocol failure: count every item as an error.
            logger.error("Bulk %s failed for %d device(s): %s", action, len(chunk), exc)
            errors += len(chunk)
            continue

        success += result.success_count
        errors += result.error_count
        for idx, err in result.enumerate_all_errors():
            name = chunk_names[idx] if 0 <= idx < len(chunk_names) else "?"
            logger.error("Failed to %s device '%s': %s (%s)", action, name, err.message, err.detail)
        if result.success_count:
            logger.debug(
                "Bulk %s: %d/%d succeeded in this batch",
                action,
                result.success_count,
                len(chunk),
            )
    return success, errors


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
            labels=set(dev.labels or []),  # label IDs from the device
        )
        if meta.get(config.meta_source_key):
            managed[dev.name] = stored
        else:
            unmanaged[dev.name] = stored

    return managed, unmanaged


def _fetch_one_cluster(
    cluster_url: str,
    config: AppConfig,
    catc_user: str,
    catc_password: str,
) -> tuple[str, list[CatCDevice]]:
    """
    Fetch raw device inventory from a single CatC cluster.

    Pure network unit with no shared state — safe to run in a worker thread.

    Args:
        cluster_url: Base URL of the Catalyst Center cluster.
        config: Application config.
        catc_user: Catalyst Center username.
        catc_password: Catalyst Center password.

    Returns:
        Tuple of (catc_hostname, raw device list).

    Raises:
        CatCInventoryError: On any auth/connection/HTTP failure. Aborting here
        prevents Step 5 from deleting managed devices that are merely unreachable.
    """
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
        msg = f"Failed to fetch inventory from {catc_hostname}: {exc}"
        logger.error(msg)
        raise CatCInventoryError(msg) from exc

    logger.info("Fetched %d devices from CatC %s", len(devices), catc_hostname)
    return catc_hostname, devices


def _merge_cluster_inventories(
    cluster_results: list[tuple[str, list[CatCDevice]]],
    filters: FilterSet,
    normalise_name: NameNormaliser,
    stats: Stats,
) -> dict[str, tuple[CatCDevice, str]]:
    """
    Reconcile per-cluster raw inventories into a single importable device map.

    Runs single-threaded on the main thread after all parallel fetches complete,
    so stats mutation and the deterministic "first cluster wins" collision rule
    are preserved (clusters are processed in configured order).

    Args:
        cluster_results: List of (catc_hostname, raw devices) in cluster order.
        filters: Filter set for device name matching.
        normalise_name: Configured CatC-hostname -> RADKit-name converter.
        stats: Stats tracker (fetched counts + skip reasons recorded here).

    Returns:
        Dictionary {radkit_name: (device, catc_hostname)}.
    """
    fresh: dict[str, tuple[CatCDevice, str]] = {}

    for catc_hostname, devices in cluster_results:
        stats.fetched_per_cluster[catc_hostname] = stats.fetched_per_cluster.get(
            catc_hostname, 0
        ) + len(devices)
        stats.fetched_total += len(devices)

        # Sort by CatC device id so that "first wins" on a name collision is
        # reproducible across runs: the CatC API does not guarantee a stable
        # ordering, and an unstable winner would cause the surviving device's
        # host/metadata to flap on every sync.
        for device in sorted(devices, key=lambda d: (d.device_id is None, d.device_id or "")):
            if device.hostname is None:
                logger.info("Skipping device without hostname at %s", device.management_ip)
                stats.record_skip(SkipReason.NO_HOSTNAME)
                continue

            # Filter on raw FQDN before normalisation
            decision = filters.classify(device.hostname)
            if decision is FilterDecision.BLACKLIST:
                logger.debug("Filtered out (blacklisted) device %s", device.hostname)
                stats.record_skip(SkipReason.BLACKLIST)
                continue
            if decision is FilterDecision.WHITELIST_MISS:
                logger.debug("Filtered out (not whitelisted) device %s", device.hostname)
                stats.record_skip(SkipReason.WHITELIST_MISS)
                continue

            radkit_name = normalise_name(device.hostname)
            if not radkit_name:
                msg = (
                    f"Device '{device.hostname}' on {catc_hostname} normalises to an "
                    f"empty name — skipping."
                )
                logger.warning(msg)
                stats.warnings.append(msg)
                stats.record_skip(SkipReason.UNNAMEABLE)
                continue

            if radkit_name in fresh:
                existing_dev, existing_source = fresh[radkit_name]
                if existing_source == catc_hostname and device.hostname == existing_dev.hostname:
                    reason = SkipReason.DUPLICATE
                    msg = (
                        f"Duplicate device '{device.hostname}' returned by "
                        f"{catc_hostname} — skipping duplicate entry."
                    )
                elif existing_source == catc_hostname:
                    reason = SkipReason.COLLISION
                    msg = (
                        f"Hostname collision: '{device.hostname}' and "
                        f"'{existing_dev.hostname}' both normalise to "
                        f"'{radkit_name}' on {catc_hostname}. Keeping first."
                    )
                else:
                    reason = SkipReason.COLLISION
                    msg = (
                        f"Hostname collision: '{radkit_name}' exists in both "
                        f"'{existing_source}' and '{catc_hostname}'. Keeping first."
                    )
                logger.warning(msg)
                stats.warnings.append(msg)
                stats.record_skip(reason)
                continue

            fresh[radkit_name] = (device, catc_hostname)

    return fresh


def fetch_fresh_inventory(
    config: AppConfig,
    filters: FilterSet,
    catc_user: str,
    catc_password: str,
    stats: Stats,
) -> dict[str, tuple[CatCDevice, str]]:
    """
    Fetch devices from all CatC clusters in parallel and reconcile them.

    Cluster fetches run concurrently in a thread pool (each ``CatCClient`` owns
    its own ``requests.Session``, so this is thread-safe). Results are gathered in
    configured cluster order and merged single-threaded, keeping the deterministic
    "first cluster wins" collision rule intact.

    Args:
        config: Application config.
        filters: Filter set for device name matching.
        catc_user: Catalyst Center username.
        catc_password: Catalyst Center password.
        stats: Stats tracker.

    Returns:
        Dictionary {radkit_name: (device, catc_hostname)}.

    Raises:
        CatCInventoryError: If any cluster fetch fails (aborts the whole sync).
    """
    cluster_urls = config.catc_clusters
    with ThreadPoolExecutor(max_workers=max(1, len(cluster_urls))) as pool:
        futures = [
            pool.submit(_fetch_one_cluster, url, config, catc_user, catc_password)
            for url in cluster_urls
        ]
        # Gather in submission (cluster) order; .result() re-raises → fail-fast.
        cluster_results = [future.result() for future in futures]

    return _merge_cluster_inventories(
        cluster_results, filters, NameNormaliser.from_config(config), stats
    )


def run_sync(
    config: AppConfig,
    dry_run: bool,
    update_credentials: bool,
    allow_renames: bool = False,
    *,
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
        update_credentials: If True, overwrite the SSH username and password on
            existing devices.
        allow_renames: If True, bypass the rename guard for this run.
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
        RenameGuardError: If the run would rename more than ``rename_limit``
            devices and ``allow_renames`` is False.
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
        # Steps 1 & 2: fetch RADKit and CatC inventories concurrently.
        # The CatC fetch (itself parallel across clusters) runs in a worker thread
        # while the single RADKit list_devices call runs on the main thread. Only
        # the worker touches stats during this window, so no locking is needed.
        logger.info(
            "Fetching existing RADKit devices and inventory from %d CatC cluster(s) in parallel...",
            len(config.catc_clusters),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            catc_future = pool.submit(
                fetch_fresh_inventory, config, filters, catc_user, catc_password, stats
            )
            try:
                managed, unmanaged = fetch_radkit_devices(api, config)
            except (AuthenticationError, APIError, ConnectionError) as exc:
                raise RadkitInventoryError(
                    _describe_radkit_failure(exc, config.radkit_base_url)
                ) from exc
            fresh = catc_future.result()  # re-raises CatCInventoryError → fail-fast

        logger.info(
            "Found %d catc-managed and %d unmanaged devices in RADKit",
            len(managed),
            len(unmanaged),
        )
        logger.info("Total importable devices across all clusters: %d", len(fresh))

        # Step 2.5: ensure configured labels exist in RADKit
        logger.info("Ensuring configured labels exist in RADKit...")
        label_config_ids = ensure_labels_exist(api, config, dry_run)
        if label_config_ids:
            logger.info("Configured labels: %s", ", ".join(label_config_ids.keys()))

        # Step 2.75: detect renames. A device whose CatC hostname changed (or
        # whose name changed because [sync.naming] changed) looks like a delete
        # plus an add, but RADKit can rename in place via UpdateDevice.name.
        # Renaming preserves the device UUID and everything attached to it, so
        # these pairs are pulled out of the add/delete sets and applied as
        # updates instead.
        rename_candidates = [name for name in fresh if name not in managed]
        rename_gone = [
            name
            for name, info in managed.items()
            if name not in fresh and info.catc_source in synced_hostnames
        ]
        renames = detect_renames(managed, fresh, rename_gone, rename_candidates)
        renamed_old = {old for old, _ in renames}
        renamed_new = {new for _, new in renames}

        if renames:
            limit = config.rename_limit
            for old_name, new_name in renames:
                logger.info("Renaming device '%s' -> '%s'", old_name, new_name)
            if not allow_renames and limit >= 0 and len(renames) > limit:
                preview = ", ".join(f"{old} -> {new}" for old, new in renames[:5])
                if len(renames) > 5:
                    preview += f", ... ({len(renames) - 5} more)"
                if dry_run:
                    logger.warning(
                        "[DRY-RUN] %d rename(s) exceed rename_limit = %d — a real run "
                        "would abort. Re-run with --allow-renames to apply them.",
                        len(renames),
                        limit,
                    )
                else:
                    raise RenameGuardError(
                        f"Refusing to rename {len(renames)} device(s) in one run "
                        f"(rename_limit = {limit}): {preview}. "
                        f"Devices are renamed in place, so UUIDs and RADKit-side "
                        f"state are preserved — but anything referring to these "
                        f"devices by name will need updating. "
                        f"If [sync.naming] changed unintentionally, revert it. "
                        f"Otherwise review with --dry-run, then re-run with "
                        f"--allow-renames."
                    )

        # Step 3: build add / adopt batches
        to_add = [name for name in fresh if name not in managed and name not in renamed_new]
        logger.info("Devices to add (or adopt): %d", len(to_add))

        new_devices: list[Any] = []
        new_names: list[str] = []
        adopt_devices: list[Any] = []
        adopt_names: list[str] = []

        for name in to_add:
            device, catc_hostname = fresh[name]

            if name in unmanaged:
                if not config.adopt_existing:
                    logger.debug(
                        "Skipping '%s': already exists in RADKit as an unmanaged device. "
                        "Use --adopt-existing (-A) to take ownership.",
                        name,
                    )
                    stats.record_skip(SkipReason.UNMANAGED_NOT_ADOPTED)
                    continue
                # For adopt, add all configured labels (none exist yet on unmanaged device)
                upd = build_update_device(
                    device=device,
                    catc_hostname=catc_hostname,
                    existing_uuid=unmanaged[name].uuid,
                    update_credentials=update_credentials,
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
                    metadata_fields=config.metadata_fields,
                    meta_source_key=config.meta_source_key,
                    labels_to_add=config.device_labels if config.device_labels else None,
                )
                adopt_devices.append(upd)
                adopt_names.append(name)
                continue

            new_dev = build_new_device(
                device=device,
                catc_hostname=catc_hostname,
                ssh_user=ssh_user,
                ssh_password=ssh_password,
                radkit_name=name,
                metadata_fields=config.metadata_fields,
                meta_source_key=config.meta_source_key,
                device_labels=config.device_labels if config.device_labels else None,
            )
            new_devices.append(new_dev)
            new_names.append(name)

        # Step 3.5: build rename batch (identified by the OLD device's UUID)
        rename_devices: list[Any] = []
        rename_names: list[str] = []
        for old_name, new_name in renames:
            device, catc_hostname = fresh[new_name]
            existing = managed[old_name]
            rename_devices.append(
                build_update_device(
                    device=device,
                    catc_hostname=catc_hostname,
                    existing_uuid=existing.uuid,
                    update_credentials=update_credentials,
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
                    metadata_fields=config.metadata_fields,
                    meta_source_key=config.meta_source_key,
                    labels_to_add=compute_labels_to_add(
                        existing, config.device_labels, label_config_ids
                    ),
                    new_name=new_name,
                )
            )
            rename_names.append(f"{old_name} -> {new_name}")

        # Step 4: build update batch for existing managed devices (only if changed)
        to_update = [name for name in fresh if name in managed]
        logger.info("Devices to check for updates: %d", len(to_update))

        update_devices: list[Any] = []
        update_names: list[str] = []

        for name in to_update:
            device, catc_hostname = fresh[name]
            existing = managed[name]

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
            if update_credentials:
                reasons.append("credential refresh (user + password)")

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

            # Compute missing labels to backfill
            labels_to_add = compute_labels_to_add(existing, config.device_labels, label_config_ids)
            if labels_to_add:
                reasons.append(f"labels (+{', +'.join(labels_to_add)})")

            if not reasons:
                logger.debug("No changes for device '%s' — skipping update", name)
                stats.unchanged += 1
                continue

            upd = build_update_device(
                device=device,
                catc_hostname=catc_hostname,
                existing_uuid=existing.uuid,
                update_credentials=update_credentials,
                ssh_user=ssh_user,
                ssh_password=ssh_password,
                metadata_fields=config.metadata_fields,
                meta_source_key=config.meta_source_key,
                labels_to_add=labels_to_add if labels_to_add else None,
            )
            if dry_run:
                logger.info(
                    "[DRY-RUN] Would update device '%s' (IP: %s, type: %s) — %s",
                    name,
                    device.management_ip,
                    upd.device_type,
                    "; ".join(reasons),
                )
            update_devices.append(upd)
            update_names.append(name)

        # Step 5: build delete batch — managed devices no longer present in CatC,
        # scoped to clusters that were actually synced this run. This includes
        # devices removed from CatC AND devices excluded by whitelist/blacklist
        # filters — narrowing filters removes previously-synced devices from RADKit.
        gone = [name for name in rename_gone if name not in renamed_old]
        logger.info("Devices to delete: %d", len(gone))
        delete_uuids: list[Any] = [UUID(managed[name].uuid) for name in gone]
        logger.info("Devices to rename: %d", len(rename_devices))

        # --- Apply all batches ---
        if dry_run:
            for name in new_names:
                device, _ = fresh[name]
                logger.info("[DRY-RUN] Would add device '%s' (IP: %s)", name, device.management_ip)
            stats.added += len(new_devices)

            for name in adopt_names:
                device, _ = fresh[name]
                logger.info(
                    "[DRY-RUN] Would adopt unmanaged device '%s' (IP: %s)",
                    name,
                    device.management_ip,
                )
            stats.adopted += len(adopt_devices)

            for label in rename_names:
                logger.info("[DRY-RUN] Would rename device '%s'", label)
            stats.renamed = len(rename_devices)

            # updates already logged per-device above
            stats.updated += len(update_devices)

            for name in gone:
                logger.info(
                    "[DRY-RUN] Would delete device '%s' (source: %s)",
                    name,
                    managed[name].catc_source,
                )
            stats.deleted += len(delete_uuids)
        else:
            if new_devices:
                added, errors = _apply_bulk(
                    api.create_devices, new_devices, new_names, "add", config.batch_size
                )
                stats.added += added
                stats.errors += errors
            if adopt_devices:
                adopted, errors = _apply_bulk(
                    api.update_devices, adopt_devices, adopt_names, "adopt", config.batch_size
                )
                stats.adopted += adopted
                stats.errors += errors
            if rename_devices:
                renamed, errors = _apply_bulk(
                    api.update_devices, rename_devices, rename_names, "rename", config.batch_size
                )
                stats.renamed = renamed
                stats.errors += errors
            if update_devices:
                updated, errors = _apply_bulk(
                    api.update_devices, update_devices, update_names, "update", config.batch_size
                )
                stats.updated += updated
                stats.errors += errors
            if delete_uuids:
                deleted, errors = _apply_bulk(
                    api.delete_devices, delete_uuids, gone, "delete", config.batch_size
                )
                stats.deleted += deleted
                stats.errors += errors

    return stats
