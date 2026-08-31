#!/usr/bin/env python3
# catc_sync.py — Sync Catalyst Center device inventory into RADKit
#
# Usage:
#   python catc_sync.py [-c CONFIG] [--dry-run] [--update-passwords] [--adopt-existing] [-v]
#
# Configuration is loaded from a TOML file (default: catc_sync.toml next to the
# script).  See catc_sync.toml.example for all available options.
#
# Sensitive values (passwords) must be set via environment variables or a .env file:
#   CATC_PASSWORD         — Catalyst Center password
#   RADKIT_ADMIN_PASSWORD — RADKit ControlAPI admin password
#   RADKIT_SSH_PASSWORD   — SSH password for imported devices
#
# Usernames can be set in the TOML config file or via environment variables
# (env vars take precedence):
#   CATC_USER             — Catalyst Center username
#   RADKIT_ADMIN_USER     — RADKit ControlAPI admin username
#   RADKIT_SSH_USER       — SSH username for imported devices

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3
from dotenv import load_dotenv
from radkit_common.types import ConnectionMethod, CustomSecretStr, DeviceType
from radkit_common.utils import b64encode
from radkit_service.control_api import APIResult, ControlAPI
from radkit_service.webserver.connectors.utils import dict_to_metadata
from radkit_service.webserver.models.devices import (
    MetaDataEntry,
    NewDevice,
    NewTerminal,
    UpdateDevice,
    UpdateMetaDataSet,
    UpdateTerminal,
)

# ---------------------------------------------------------------------------
# CONFIGURATION DEFAULTS — override via config file (see catc_sync.toml.example)
# ---------------------------------------------------------------------------

CATC_CLUSTERS: list[str] = []
CATC_VERIFY_TLS: bool = True
DEVICE_WHITELIST: list[str] = []
DEVICE_BLACKLIST: list[str] = []
RADKIT_BASE_URL = "https://localhost:8081/api/v1"
META_SOURCE = "catc_source"
ADOPT_EXISTING: bool = False
DELETE_FILTERED: bool = False
CONFIG_ENV_FALLBACKS: dict[str, str] = {}


def load_config(config_path: Path | None) -> None:
    """
    Load configuration from a TOML file and apply values to module-level
    variables.  Environment variables always take precedence for credentials;
    the TOML file is for non-sensitive settings.
    """
    global \
        CATC_CLUSTERS, \
        CATC_VERIFY_TLS, \
        DEVICE_WHITELIST, \
        DEVICE_BLACKLIST, \
        RADKIT_BASE_URL, \
        META_SOURCE, \
        METADATA_FIELDS, \
        ADOPT_EXISTING, \
        DELETE_FILTERED, \
        CONFIG_ENV_FALLBACKS

    if config_path is None:
        # Search: script directory first, then current working directory
        script_dir = Path(__file__).resolve().parent
        for candidate in [script_dir / "catc_sync.toml", Path.cwd() / "catc_sync.toml"]:
            if candidate.exists():
                config_path = candidate
                break
        if config_path is None:
            return  # no default config found — silently skip

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    logger.info("Loading config from %s", config_path)
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    # [catc]
    catc = cfg.get("catc", {})
    if "clusters" in catc:
        CATC_CLUSTERS = catc["clusters"]
    if "verify_tls" in catc:
        CATC_VERIFY_TLS = catc["verify_tls"]

    # [radkit]
    radkit = cfg.get("radkit", {})
    if "base_url" in radkit:
        RADKIT_BASE_URL = radkit["base_url"]

    # [filters]
    filters = cfg.get("filters", {})
    if "whitelist" in filters:
        DEVICE_WHITELIST = filters["whitelist"]
    if "blacklist" in filters:
        DEVICE_BLACKLIST = filters["blacklist"]

    # [metadata]
    meta = cfg.get("metadata", {})
    if "source_key" in meta:
        META_SOURCE = meta["source_key"]
    if "fields" in meta:
        METADATA_FIELDS = set(meta["fields"])

    # [sync]
    sync = cfg.get("sync", {})
    if "adopt_existing" in sync:
        ADOPT_EXISTING = sync["adopt_existing"]
    if "delete_filtered" in sync:
        DELETE_FILTERED = sync["delete_filtered"]

    # Non-sensitive values that can serve as fallbacks for env vars
    if "user" in catc:
        CONFIG_ENV_FALLBACKS["CATC_USER"] = catc["user"]
    if "admin_user" in radkit:
        CONFIG_ENV_FALLBACKS["RADKIT_ADMIN_USER"] = radkit["admin_user"]
    if "ssh_user" in radkit:
        CONFIG_ENV_FALLBACKS["RADKIT_SSH_USER"] = radkit["ssh_user"]


# ---------------------------------------------------------------------------
# Device type mapping (mirrors the logic in radkit_service connector)
# ---------------------------------------------------------------------------

_SOFTWARE_TYPE_MAP: dict[str, DeviceType] = {
    "NXOS": DeviceType.NX_OS,
    "IOS": DeviceType.IOS_XE,
    "IOS-XE": DeviceType.IOS_XE,
}
_SERIES_TYPE_MAP: dict[str, DeviceType] = {
    "cisco catalyst 9800 series wireless controllers": DeviceType.WLC,
}


def _get_device_type(software_type: str | None, series: str | None) -> DeviceType:
    """Map CatC softwareType / series fields to a RADKit DeviceType."""
    if series is not None:
        matched = _SERIES_TYPE_MAP.get(series.lower())
        if matched is not None:
            return matched
    if software_type is None:
        return DeviceType.GENERIC
    return _SOFTWARE_TYPE_MAP.get(software_type, DeviceType.GENERIC)


# ---------------------------------------------------------------------------
# CatC device representation
# ---------------------------------------------------------------------------


@dataclass
class CatCDevice:
    """
    Lightweight representation of a Catalyst Center network device.
    Parsed directly from the /api/v1/network-device JSON response —
    no dependency on radkit_service internal models.
    """

    hostname: str | None
    management_ip: str
    software_type: str | None
    series: str | None
    raw: dict[str, Any]  # full response payload, used for RADKit metadata

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatCDevice:
        return cls(
            hostname=data.get("hostname"),
            management_ip=data["managementIpAddress"],
            software_type=data.get("softwareType"),
            series=data.get("series"),
            raw=data,
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("catc_sync")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S"))
    logger.setLevel(level)
    logger.addHandler(handler)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# Environment variable validation — collect ALL missing vars at once
# ---------------------------------------------------------------------------

_REQUIRED_ENV_VARS = [
    ("CATC_USER", "Catalyst Center username"),
    ("CATC_PASSWORD", "Catalyst Center password"),
    ("RADKIT_ADMIN_USER", "RADKit ControlAPI admin username"),
    ("RADKIT_ADMIN_PASSWORD", "RADKit ControlAPI admin password"),
    ("RADKIT_SSH_USER", "SSH username for imported devices"),
    ("RADKIT_SSH_PASSWORD", "SSH password for imported devices"),
]


def load_env_vars() -> dict[str, str]:
    """
    Read all required environment variables. If any are missing, raise
    SystemExit with a consolidated error listing every missing variable.
    """
    values: dict[str, str] = {}
    missing: list[tuple[str, str]] = []

    for key, description in _REQUIRED_ENV_VARS:
        value = (os.environ.get(key) or CONFIG_ENV_FALLBACKS.get(key, "")).strip()
        if not value:
            missing.append((key, description))
        else:
            values[key] = value

    if missing:
        lines = ["ERROR: The following required environment variables are not set or empty:\n"]
        for key, description in missing:
            lines.append(f"  {key:<26}  # {description}")
        lines.append(
            "\nSet them in the environment or create a .env file in the current directory."
        )
        raise SystemExit("\n".join(lines))

    return values


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------


def normalise_name(fqdn: str) -> str:
    """Strip domain part, lowercase, replace invalid chars with dashes.

    RADKit device names must contain only lower-case letters, digits and
    dashes and must not start or end with a dash.
    """
    short = fqdn.split(".")[0].lower()
    # Replace any character that isn't a-z, 0-9 or dash with a dash
    sanitised = re.sub(r"[^a-z0-9-]", "-", short)
    # Collapse consecutive dashes and strip leading/trailing dashes
    sanitised = re.sub(r"-{2,}", "-", sanitised).strip("-")
    return sanitised


# ---------------------------------------------------------------------------
# Device name filtering
# ---------------------------------------------------------------------------

_whitelist_re: list[re.Pattern[str]] = []
_blacklist_re: list[re.Pattern[str]] = []


def compile_filters() -> None:
    global _whitelist_re, _blacklist_re
    _whitelist_re = [re.compile(p, re.IGNORECASE) for p in DEVICE_WHITELIST]
    _blacklist_re = [re.compile(p, re.IGNORECASE) for p in DEVICE_BLACKLIST]


def should_import(hostname: str) -> bool:
    """
    Return True if the raw CatC hostname passes whitelist/blacklist filters.

    Matching uses re.search with re.IGNORECASE — patterns match anywhere in
    the string case-insensitively. Use ^ / $ anchors to restrict to start/end.
    DEVICE_BLACKLIST takes precedence over DEVICE_WHITELIST.
    """
    for pat in _blacklist_re:
        if pat.search(hostname):
            return False
    if _whitelist_re:
        return any(pat.search(hostname) for pat in _whitelist_re)
    return True


# ---------------------------------------------------------------------------
# Catalyst Center HTTP client
# ---------------------------------------------------------------------------

_CATC_TOKEN_PATH = "/dna/system/api/v1/auth/token"
_CATC_INVENTORY_PATH = "/api/v1/network-device"


@dataclass
class CatCClient:
    base_url: str
    username: str
    password: str
    verify_tls: bool = True
    session: requests.Session = field(default_factory=requests.Session)
    _token: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.session.verify = self.verify_tls
        if not self.verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @property
    def hostname(self) -> str:
        return urlparse(self.base_url).hostname or self.base_url

    def authenticate(self) -> None:
        """Obtain API bearer token via Basic Auth."""
        credentials = b64encode(f"{self.username}:{self.password}".encode())
        url = self.base_url.rstrip("/") + _CATC_TOKEN_PATH
        logger.debug("Authenticating to CatC %s", self.hostname)
        resp = self.session.post(
            url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"CatC auth failed for {self.hostname}: HTTP {resp.status_code}")
        token = resp.json().get("Token")
        if not token:
            raise RuntimeError(f"CatC auth response for {self.hostname} contained no Token")
        self._token = token
        self.session.headers.update({"x-auth-token": self._token})
        logger.debug("Authenticated to CatC %s", self.hostname)

    def get_devices(self) -> list[CatCDevice]:
        """Fetch full paginated device inventory."""
        devices: list[CatCDevice] = []
        offset = 1
        while True:
            url = self.base_url.rstrip("/") + f"{_CATC_INVENTORY_PATH}?offset={offset}"
            logger.debug("Fetching inventory from %s offset=%d", self.hostname, offset)
            resp = self.session.get(url, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Inventory fetch failed for {self.hostname}: HTTP {resp.status_code}"
                )
            raw = resp.json().get("response", [])
            if not raw:
                break
            page_devices = [CatCDevice.from_dict(d) for d in raw]
            devices.extend(page_devices)
            offset += len(page_devices)
            logger.debug("Got %d devices (total so far: %d)", len(page_devices), len(devices))
        return devices


# ---------------------------------------------------------------------------
# Build RADKit NewDevice / UpdateDevice from CatC data
# ---------------------------------------------------------------------------


# CatC fields to sync as RADKit metadata (stable inventory data only —
# volatile fields like upTime or lastUpdateTime are excluded to avoid
# unnecessary update churn).
METADATA_FIELDS: set[str] = {
    "id",
    "hostname",
    "managementIpAddress",
    "platformId",
    "serialNumber",
    "softwareType",
    "softwareVersion",
    "family",
    "series",
    "type",
    "role",
    "macAddress",
    "snmpLocation",
    "managementState",
}


def _require_api_result_ok(result: APIResult, action: str) -> Any:
    """Return the typed API result payload or raise on RADKit API errors."""
    if APIResult.is_error(result):
        raise RuntimeError(
            f"RADKit ControlAPI failed to {action}: {result.root.message} ({result.root.detail})"
        )
    return result.result


def _build_metadata(device: CatCDevice, catc_hostname: str) -> list[MetaDataEntry]:
    """
    Build RADKit metadata from selected CatC inventory fields plus the
    catc_source ownership marker.
    """
    raw = {
        k: str(v) if v is not None else "" for k, v in device.raw.items() if k in METADATA_FIELDS
    }

    meta: list[MetaDataEntry] = list(dict_to_metadata(raw))

    # Upsert catc_source
    existing_keys = {m.key for m in meta}
    if META_SOURCE in existing_keys:
        meta = [
            MetaDataEntry(key=META_SOURCE, value=catc_hostname) if m.key == META_SOURCE else m
            for m in meta
        ]
    else:
        meta.append(MetaDataEntry(key=META_SOURCE, value=catc_hostname))

    return meta


def build_new_device(
    device: CatCDevice,
    catc_hostname: str,
    ssh_user: str,
    ssh_password: str,
    radkit_name: str,
) -> NewDevice:
    terminal = NewTerminal(
        connection_method=ConnectionMethod.SSH,
        port=22,
        username=ssh_user,
        password=CustomSecretStr(ssh_password),
    )
    return NewDevice(
        name=radkit_name,
        host=device.management_ip,
        device_type=_get_device_type(
            software_type=device.software_type,
            series=device.series,
        ),
        description=f"Imported from CatC: {catc_hostname}",
        meta_data=_build_metadata(device, catc_hostname),
        enabled=True,
        terminal=terminal,
    )


def build_update_device(
    device: CatCDevice,
    catc_hostname: str,
    existing_uuid: str,
    update_passwords: bool,
    ssh_user: str,
    ssh_password: str,
) -> UpdateDevice:
    meta_update = UpdateMetaDataSet(replace=_build_metadata(device, catc_hostname))

    kwargs: dict[str, Any] = {
        "uuid": existing_uuid,
        "host": device.management_ip,
        "device_type": _get_device_type(
            software_type=device.software_type,
            series=device.series,
        ),
        "description": f"Imported from CatC: {catc_hostname}",
        "meta_data_update": meta_update,
    }

    if update_passwords:
        kwargs["terminal"] = UpdateTerminal(
            username=ssh_user,
            password=CustomSecretStr(ssh_password),
        )

    return UpdateDevice(**kwargs)


# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    adopted: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "--- Sync Summary ---",
            f"  Added:     {self.added}",
            f"  Updated:   {self.updated}",
            f"  Unchanged: {self.unchanged}",
            f"  Adopted:   {self.adopted}  (existing unmanaged devices taken over)",
            f"  Deleted:   {self.deleted}",
            f"  Skipped:   {self.skipped}",
            f"  Errors:    {self.errors}",
        ]
        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)}")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------


def fetch_radkit_devices(
    api: ControlAPI,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    Fetch all RADKit devices and split into:
      managed   — {name: info} devices with a non-empty catc_source metadata field
      unmanaged — {name: info} all other devices
    """
    devices = _require_api_result_ok(api.list_devices(), "list devices")
    managed: dict[str, dict[str, Any]] = {}
    unmanaged: dict[str, dict[str, Any]] = {}
    for dev in devices or []:
        meta = {m.key: m.value for m in (dev.meta_data or [])}
        entry: dict[str, Any] = {
            "uuid": str(dev.uuid),
            "catc_source": meta.get(META_SOURCE, ""),
            "host": getattr(dev, "host", ""),
            "device_type": str(getattr(dev, "device_type", "")),
            "metadata": meta,
        }
        if meta.get(META_SOURCE):
            managed[dev.name] = entry
        else:
            unmanaged[dev.name] = entry
    return managed, unmanaged


def fetch_fresh_inventory(
    clusters: list[str],
    catc_user: str,
    catc_password: str,
    stats: Stats,
    *,
    verify_tls: bool = True,
) -> tuple[dict[str, tuple[CatCDevice, str]], set[str]]:
    """
    Fetch devices from all CatC clusters.
    Returns ({radkit_name: (device, catc_hostname)}, filtered_names).
    filtered_names contains normalised names of devices that were present in
    CatC but excluded by whitelist/blacklist filters.
    Cross-cluster hostname collisions are warned about; first cluster wins.
    """
    fresh: dict[str, tuple[CatCDevice, str]] = {}
    filtered: set[str] = set()

    for cluster_url in clusters:
        client = CatCClient(
            base_url=cluster_url,
            username=catc_user,
            password=catc_password,
            verify_tls=verify_tls,
        )
        catc_hostname = client.hostname
        try:
            client.authenticate()
            devices = client.get_devices()
        except Exception as exc:
            msg = f"Failed to fetch from {catc_hostname}: {exc}"
            logger.error(msg)
            stats.errors += 1
            stats.warnings.append(msg)
            continue

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
            if not should_import(device.hostname):
                logger.debug("Filtered out device %s", device.hostname)
                filtered.add(normalise_name(device.hostname))
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

    return fresh, filtered


def run_sync(
    dry_run: bool,
    update_passwords: bool,
    adopt_existing: bool,
    delete_filtered: bool,
    catc_user: str,
    catc_password: str,
    radkit_admin_user: str,
    radkit_admin_password: str,
    ssh_user: str,
    ssh_password: str,
    *,
    verify_tls: bool = True,
) -> Stats:
    stats = Stats()
    compile_filters()

    if not CATC_CLUSTERS:
        raise ValueError("CATC_CLUSTERS is empty — add at least one cluster URL to the script.")

    synced_hostnames = {urlparse(u).hostname for u in CATC_CLUSTERS}

    logger.info("Connecting to RADKit ControlAPI at %s", RADKIT_BASE_URL)
    with ControlAPI.create(
        base_url=RADKIT_BASE_URL,
        admin_name=radkit_admin_user,
        admin_password=radkit_admin_password,
    ) as api:
        # Step 1: get all devices from RADKit, split into managed / unmanaged
        logger.info("Fetching existing devices from RADKit...")
        managed, unmanaged = fetch_radkit_devices(api)
        logger.info(
            "Found %d catc-managed and %d unmanaged devices in RADKit",
            len(managed),
            len(unmanaged),
        )

        # Step 2: fetch fresh inventory from all clusters
        logger.info("Fetching inventory from %d CatC cluster(s)...", len(CATC_CLUSTERS))
        fresh, filtered_names = fetch_fresh_inventory(
            CATC_CLUSTERS,
            catc_user,
            catc_password,
            stats,
            verify_tls=verify_tls,
        )
        logger.info("Total importable devices across all clusters: %d", len(fresh))

        # Step 3: add new devices / adopt unmanaged conflicts
        to_add = [name for name in fresh if name not in managed]
        logger.info("Devices to add (or adopt): %d", len(to_add))
        for name in to_add:
            device, catc_hostname = fresh[name]

            if name in unmanaged:
                if not adopt_existing:
                    msg = (
                        f"Skipping '{name}': already exists in RADKit as an unmanaged "
                        f"device. Use --adopt-existing (-A) to take ownership."
                    )
                    logger.warning(msg)
                    stats.warnings.append(msg)
                    stats.skipped += 1
                    continue
                existing_uuid = unmanaged[name]["uuid"]
                upd = build_update_device(
                    device=device,
                    catc_hostname=catc_hostname,
                    existing_uuid=existing_uuid,
                    update_passwords=update_passwords,
                    ssh_user=ssh_user,
                    ssh_password=ssh_password,
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
                        _require_api_result_ok(api.update_device(upd), f"adopt device '{name}'")
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
                    _require_api_result_ok(api.create_device(new_dev), f"create device '{name}'")
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
            existing_uuid = existing["uuid"]

            # Compute what would change
            reasons: list[str] = []
            new_ip = device.management_ip
            new_type = str(
                _get_device_type(software_type=device.software_type, series=device.series)
            )
            if existing.get("host") and existing["host"] != new_ip:
                reasons.append(f"IP {existing['host']} -> {new_ip}")
            if existing.get("device_type") and existing["device_type"] != new_type:
                reasons.append(f"type {existing['device_type']} -> {new_type}")
            if existing.get("catc_source") != catc_hostname:
                reasons.append(f"source {existing.get('catc_source')} -> {catc_hostname}")
            if update_passwords:
                reasons.append("password refresh")

            # Compare metadata
            fresh_meta = {m.key: m.value for m in _build_metadata(device, catc_hostname)}
            existing_meta: dict[str, str] = existing.get("metadata", {})
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
                    _require_api_result_ok(api.update_device(upd), f"update device '{name}'")
                    logger.debug("Updated device '%s'", name)
                    stats.updated += 1
                except Exception as exc:
                    logger.error("Failed to update device '%s': %s", name, exc)
                    stats.errors += 1

        # Step 5: delete managed devices no longer present in CatC
        # Scoped to clusters that were actually synced this run.
        # Separate "removed from CatC" from "filtered out by whitelist/blacklist".
        gone = []
        filtered_managed = []
        for name, info in managed.items():
            if name in fresh or info["catc_source"] not in synced_hostnames:
                continue
            if name in filtered_names:
                filtered_managed.append(name)
            else:
                gone.append(name)

        # 5a: devices truly removed from CatC — always delete
        logger.info("Devices to delete: %d", len(gone))
        for name in gone:
            existing_uuid = managed[name]["uuid"]
            catc_source = managed[name]["catc_source"]
            if dry_run:
                logger.info("[DRY-RUN] Would delete device '%s' (source: %s)", name, catc_source)
                stats.deleted += 1
            else:
                try:
                    _require_api_result_ok(
                        api.delete_device(existing_uuid), f"delete device '{name}'"
                    )
                    logger.info("Deleted device '%s' (source: %s)", name, catc_source)
                    stats.deleted += 1
                except Exception as exc:
                    logger.error("Failed to delete device '%s': %s", name, exc)
                    stats.errors += 1

        # 5b: devices present in CatC but excluded by filters
        if filtered_managed:
            if delete_filtered:
                logger.info(
                    "Deleting %d managed device(s) excluded by filters: %s",
                    len(filtered_managed),
                    ", ".join(sorted(filtered_managed)),
                )
                for name in filtered_managed:
                    existing_uuid = managed[name]["uuid"]
                    catc_source = managed[name]["catc_source"]
                    if dry_run:
                        logger.info(
                            "[DRY-RUN] Would delete filtered device '%s' (source: %s)",
                            name,
                            catc_source,
                        )
                        stats.deleted += 1
                    else:
                        try:
                            _require_api_result_ok(
                                api.delete_device(existing_uuid),
                                f"delete filtered device '{name}'",
                            )
                            logger.info(
                                "Deleted filtered device '%s' (source: %s)", name, catc_source
                            )
                            stats.deleted += 1
                        except Exception as exc:
                            logger.error("Failed to delete device '%s': %s", name, exc)
                            stats.errors += 1
            else:
                msg = (
                    f"{len(filtered_managed)} managed device(s) excluded by filters "
                    f"but kept in RADKit (set sync.delete_filtered = true to remove): "
                    + ", ".join(sorted(filtered_managed))
                )
                logger.warning(msg)
                stats.warnings.append(msg)

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Catalyst Center device inventory into RADKit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables (can also be set in a .env file):\n"
            "  CATC_USER              Catalyst Center username\n"
            "  CATC_PASSWORD          Catalyst Center password\n"
            "  RADKIT_ADMIN_USER      RADKit ControlAPI admin username\n"
            "  RADKIT_ADMIN_PASSWORD  RADKit ControlAPI admin password\n"
            "  RADKIT_SSH_USER        SSH username for imported devices\n"
            "  RADKIT_SSH_PASSWORD    SSH password for imported devices\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making any modifications.",
    )
    parser.add_argument(
        "--update-passwords",
        action="store_true",
        help="Overwrite SSH password on existing managed devices.",
    )
    parser.add_argument(
        "-A",
        "--adopt-existing",
        action="store_true",
        default=None,
        help=(
            "Adopt unmanaged RADKit devices that share a name with a CatC device. "
            "Without this flag such conflicts are skipped with a warning. "
            "(Can also be set via sync.adopt_existing in config file.)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    parser.add_argument(
        "-k",
        "--no-verify-tls",
        action="store_true",
        default=None,
        help=(
            "Disable TLS certificate verification for Catalyst Center connections. "
            "(Can also be set via catc.verify_tls in config file.)"
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to TOML config file (default: catc_sync.toml next to the script, then cwd).",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    load_config(args.config)

    # Load .env: script directory first, then cwd (does not override already-set
    # environment variables; script-dir values take precedence because they're
    # loaded first).
    env_search_dirs: list[Path] = []
    env_search_dirs.append(Path(__file__).resolve().parent)
    env_search_dirs.append(Path.cwd())
    # Deduplicate while preserving order
    seen: set[Path] = set()
    for d in env_search_dirs:
        if d not in seen:
            seen.add(d)
            load_dotenv(d / ".env", override=False)

    if args.dry_run:
        logger.info("*** DRY-RUN MODE — no changes will be made ***")

    env = load_env_vars()

    # Merge CLI flags with config defaults (CLI wins when explicitly set)
    verify_tls = CATC_VERIFY_TLS if args.no_verify_tls is None else not args.no_verify_tls
    adopt_existing = ADOPT_EXISTING if args.adopt_existing is None else args.adopt_existing

    try:
        stats = run_sync(
            dry_run=args.dry_run,
            update_passwords=args.update_passwords,
            adopt_existing=adopt_existing,
            delete_filtered=DELETE_FILTERED,
            catc_user=env["CATC_USER"],
            catc_password=env["CATC_PASSWORD"],
            radkit_admin_user=env["RADKIT_ADMIN_USER"],
            radkit_admin_password=env["RADKIT_ADMIN_PASSWORD"],
            ssh_user=env["RADKIT_SSH_USER"],
            ssh_password=env["RADKIT_SSH_PASSWORD"],
            verify_tls=verify_tls,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    print()
    print(stats.summary())

    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
