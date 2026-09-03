"""Configuration management for radkit-catc-sync."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration."""

    # Catalyst Center
    catc_clusters: list[str] = field(default_factory=list)
    catc_verify_tls: bool = True

    # RADKit
    radkit_base_url: str = "https://localhost:8081/api/v1"

    # Device filters (raw patterns, not compiled regexes)
    device_whitelist: list[str] = field(default_factory=list)
    device_blacklist: list[str] = field(default_factory=list)

    # Metadata
    meta_source_key: str = "catc_source"
    metadata_fields: frozenset[str] = field(
        default_factory=lambda: frozenset(
            [
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
            ]
        )
    )

    # Labels
    device_labels: list[str] = field(default_factory=list)

    # Sync behavior
    adopt_existing: bool = False
    batch_size: int = 500

    # Non-sensitive usernames that can come from config
    # (passwords must always come from env vars)
    catc_user: str | None = None
    radkit_admin_user: str | None = None
    radkit_ssh_user: str | None = None


_REQUIRED_ENV_VARS = [
    ("CATC_USER", "Catalyst Center username"),
    ("CATC_PASSWORD", "Catalyst Center password"),
    ("RADKIT_ADMIN_USER", "RADKit ControlAPI admin username"),
    ("RADKIT_ADMIN_PASSWORD", "RADKit ControlAPI admin password"),
    ("RADKIT_SSH_USER", "SSH username for imported devices"),
    ("RADKIT_SSH_PASSWORD", "SSH password for imported devices"),
]


def load_config(config_path: Path | None) -> AppConfig:
    """
    Load configuration from TOML file.

    Search order if config_path is None:
    1. CATC_SYNC_CONFIG environment variable
    2. ./catc_sync.toml (project-local)
    3. defaults (built into AppConfig)

    Args:
        config_path: Explicit path to TOML config file, or None to search.

    Returns:
        AppConfig object with loaded settings.

    Raises:
        FileNotFoundError: If explicit config_path is provided but doesn't exist.
    """
    # Determine which config file to use
    if config_path is None:
        # Check env var first
        env_config = os.environ.get("CATC_SYNC_CONFIG")
        if env_config:
            config_path = Path(env_config)
        else:
            # Check cwd for project-local config
            cwd_config = Path.cwd() / "catc_sync.toml"
            if cwd_config.exists():
                config_path = cwd_config

    # If we found a config file, load it
    if config_path is None:
        logger.debug("No config file found; using defaults")
        return AppConfig()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    logger.info("Loading config from %s", config_path)
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    # Parse [catc] section
    catc = cfg.get("catc", {})
    catc_clusters = catc.get("clusters", [])
    catc_verify_tls = catc.get("verify_tls", True)
    catc_user = catc.get("user")

    # Parse [radkit] section
    radkit = cfg.get("radkit", {})
    radkit_base_url = radkit.get("base_url", "https://localhost:8081/api/v1")
    radkit_admin_user = radkit.get("admin_user")
    radkit_ssh_user = radkit.get("ssh_user")

    # Parse [filters] section
    filters = cfg.get("filters", {})
    device_whitelist = filters.get("whitelist", [])
    device_blacklist = filters.get("blacklist", [])

    # Parse [metadata] section
    meta = cfg.get("metadata", {})
    meta_source_key = meta.get("source_key", "catc_source")
    metadata_fields_list = meta.get("fields")
    metadata_fields = (
        frozenset(metadata_fields_list) if metadata_fields_list else AppConfig().metadata_fields
    )

    # Parse [labels] section
    labels = cfg.get("labels", {})
    device_labels = labels.get("names", [])

    # Parse [sync] section
    sync = cfg.get("sync", {})
    adopt_existing = sync.get("adopt_existing", False)
    batch_size = sync.get("batch_size", 500)

    return AppConfig(
        catc_clusters=catc_clusters,
        catc_verify_tls=catc_verify_tls,
        radkit_base_url=radkit_base_url,
        device_whitelist=device_whitelist,
        device_blacklist=device_blacklist,
        meta_source_key=meta_source_key,
        metadata_fields=metadata_fields,
        device_labels=device_labels,
        adopt_existing=adopt_existing,
        batch_size=batch_size,
        catc_user=catc_user,
        radkit_admin_user=radkit_admin_user,
        radkit_ssh_user=radkit_ssh_user,
    )


def load_env_vars(config: AppConfig) -> dict[str, str]:
    """
    Read all required environment variables.

    Falls back to non-sensitive config values (usernames) when env vars are missing.
    Passwords must always come from environment.

    Args:
        config: AppConfig to use for fallback values.

    Returns:
        Dictionary of {var_name: value}.

    Raises:
        SystemExit: If any required variable is missing or empty.
    """
    values: dict[str, str] = {}
    missing: list[tuple[str, str]] = []

    # Build fallback dict from config
    fallbacks = {}
    if config.catc_user:
        fallbacks["CATC_USER"] = config.catc_user
    if config.radkit_admin_user:
        fallbacks["RADKIT_ADMIN_USER"] = config.radkit_admin_user
    if config.radkit_ssh_user:
        fallbacks["RADKIT_SSH_USER"] = config.radkit_ssh_user

    for key, description in _REQUIRED_ENV_VARS:
        value = (os.environ.get(key) or fallbacks.get(key, "")).strip()
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


def load_env_file(search_dirs: list[Path] | None = None) -> None:
    """
    Load environment variables from .env files.

    Args:
        search_dirs: Directories to search for .env file (highest priority first).
                     If None, searches current working directory.
    """
    if search_dirs is None:
        search_dirs = [Path.cwd()]

    # Deduplicate while preserving order
    seen: set[Path] = set()
    for d in search_dirs:
        if d not in seen:
            seen.add(d)
            env_file = d / ".env"
            if env_file.exists():
                logger.debug("Loading .env from %s", env_file)
                load_dotenv(env_file, override=False)
