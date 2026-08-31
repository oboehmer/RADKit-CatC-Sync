# tests/conftest.py — shared fixtures for catc_sync test suite
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import catc_sync

# ---------------------------------------------------------------------------
# Module-global reset (autouse) — every test starts with clean defaults
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "CATC_CLUSTERS": [],
    "CATC_VERIFY_TLS": True,
    "DEVICE_WHITELIST": [],
    "DEVICE_BLACKLIST": [],
    "RADKIT_BASE_URL": "https://localhost:8081/api/v1",
    "META_SOURCE": "catc_source",
    "ADOPT_EXISTING": False,
    "CONFIG_ENV_FALLBACKS": {},
}


@pytest.fixture(autouse=True)
def _reset_module_globals() -> Generator[None]:  # noqa: PT004
    """Reset catc_sync module globals to defaults after each test."""
    yield
    for attr, default in _DEFAULTS.items():
        # Use a fresh copy for mutable defaults
        setattr(catc_sync, attr, default.copy() if isinstance(default, (list, dict)) else default)
    catc_sync._whitelist_re = []
    catc_sync._blacklist_re = []


# ---------------------------------------------------------------------------
# CatCDevice factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_device() -> Callable[..., catc_sync.CatCDevice]:
    """Factory for CatCDevice instances with sensible defaults."""

    def _make(
        hostname: str = "r1.example.com",
        ip: str = "10.0.0.1",
        software_type: str | None = "IOS-XE",
        series: str | None = None,
        raw_extra: dict[str, Any] | None = None,
    ) -> catc_sync.CatCDevice:
        raw: dict[str, Any] = {"hostname": hostname, "managementIpAddress": ip}
        if raw_extra:
            raw.update(raw_extra)
        return catc_sync.CatCDevice(
            hostname=hostname,
            management_ip=ip,
            software_type=software_type,
            series=series,
            raw=raw,
        )

    return _make


# ---------------------------------------------------------------------------
# RADKit ControlAPI mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_controlapi() -> Generator[MagicMock]:
    """Patch ControlAPI + APIResult and return the mock api object."""
    with (
        patch("catc_sync.ControlAPI") as mock_cls,
        patch("catc_sync.APIResult") as mock_result,
    ):
        mock_result.is_error.return_value = False
        api = MagicMock()
        api.__enter__ = MagicMock(return_value=api)
        api.__exit__ = MagicMock(return_value=False)
        mock_cls.create.return_value = api
        yield api


# ---------------------------------------------------------------------------
# run_sync convenience wrapper
# ---------------------------------------------------------------------------

# Credentials shared by all run_sync calls — harmless test-only values.
_SYNC_CREDS = {
    "catc_user": "testuser",
    "catc_password": "testpassword",  # noqa: S106
    "radkit_admin_user": "testadmin",
    "radkit_admin_password": "testpassword",  # noqa: S106
    "ssh_user": "testnetops",
    "ssh_password": "testpassword",  # noqa: S106
}


@pytest.fixture
def run_sync(mock_controlapi: MagicMock) -> Callable[..., tuple[catc_sync.Stats, MagicMock]]:
    """Return a helper that runs catc_sync.run_sync with mocked APIs.

    Patches CatCClient (authenticate + get_devices) and fetch_radkit_devices.
    Returns (Stats, mock_api).
    """

    def _run(
        catc_devices: list[catc_sync.CatCDevice] | None = None,
        radkit_devices: tuple[dict[str, Any], dict[str, Any]] | None = None,
        *,
        adopt: bool = False,
        dry_run: bool = False,
        update_pw: bool = False,
    ) -> tuple[catc_sync.Stats, MagicMock]:
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        catc_sync.DEVICE_WHITELIST = []
        catc_sync.DEVICE_BLACKLIST = []

        if catc_devices is None:
            catc_devices = []
        if radkit_devices is None:
            radkit_devices = ({}, {})

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=catc_devices),
            patch("catc_sync.fetch_radkit_devices", return_value=radkit_devices),
        ):
            stats = catc_sync.run_sync(
                dry_run=dry_run,
                update_passwords=update_pw,
                adopt_existing=adopt,
                catc_user=_SYNC_CREDS["catc_user"],
                catc_password=_SYNC_CREDS["catc_password"],
                radkit_admin_user=_SYNC_CREDS["radkit_admin_user"],
                radkit_admin_password=_SYNC_CREDS["radkit_admin_password"],
                ssh_user=_SYNC_CREDS["ssh_user"],
                ssh_password=_SYNC_CREDS["ssh_password"],
            )
            return stats, mock_controlapi

    return _run
