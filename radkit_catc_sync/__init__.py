"""RADKit-CatC-Sync: Sync Cisco Catalyst Center device inventory into RADKit."""

from __future__ import annotations

__version__ = "0.2.0"

# Export public API for backwards compatibility
from .config import AppConfig, load_config, load_env_file, load_env_vars
from .filters import FilterSet
from .models import CatCDevice, StoredRadkitDevice
from .stats import Stats
from .sync import CatCInventoryError, run_sync

__all__ = [
    "AppConfig",
    "CatCDevice",
    "CatCInventoryError",
    "FilterSet",
    "load_config",
    "load_env_file",
    "load_env_vars",
    "run_sync",
    "Stats",
    "StoredRadkitDevice",
]
