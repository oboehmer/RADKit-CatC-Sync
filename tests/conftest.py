# tests/conftest.py — shared fixtures for radkit_catc_sync test suite
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from radkit_catc_sync import AppConfig, CatCDevice, Stats
from radkit_catc_sync.catc_client import CatCClient
from radkit_catc_sync.models import StoredRadkitDevice

# ---------------------------------------------------------------------------
# CatCDevice factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_device() -> Callable[..., CatCDevice]:
    """Factory for CatCDevice instances with sensible defaults."""

    def _make(
        hostname: str = "r1.example.com",
        ip: str = "10.0.0.1",
        software_type: str | None = "IOS-XE",
        series: str | None = None,
        raw_extra: dict[str, Any] | None = None,
    ) -> CatCDevice:
        raw: dict[str, Any] = {"hostname": hostname, "managementIpAddress": ip}
        if raw_extra:
            raw.update(raw_extra)
        return CatCDevice(
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
    """Patch ControlAPI + APIResult and return the mock api object.

    The bulk device methods (create_devices/update_devices/delete_devices) are
    wired to return a fake BulkResult that reports every submitted item as a
    success. Tests that want to simulate failures override the relevant method's
    ``side_effect`` (e.g. raise) after receiving this fixture.
    """
    with (
        patch("radkit_catc_sync.sync.ControlAPI") as mock_cls,
        patch("radkit_catc_sync.sync.APIResult") as mock_result,
    ):
        mock_result.is_error.return_value = False
        api = MagicMock()
        api.__enter__ = MagicMock(return_value=api)
        api.__exit__ = MagicMock(return_value=False)
        api.create_devices.side_effect = _fake_bulk
        api.update_devices.side_effect = _fake_bulk
        api.delete_devices.side_effect = _fake_bulk
        mock_cls.create.return_value = api
        yield api


def _fake_bulk(items: Any) -> MagicMock:
    """Build a fake BulkResult where every submitted item succeeded."""
    n = len(list(items))
    result = MagicMock()
    result.success_count = n
    result.error_count = 0
    result.enumerate_all_errors.return_value = []
    result.successful_results.return_value = []
    return result


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
def run_sync(mock_controlapi: MagicMock) -> Callable[..., tuple[Stats, MagicMock]]:
    """Return a helper that runs run_sync with mocked APIs.

    Patches CatCClient (authenticate + get_devices) and fetch_radkit_devices.
    Returns (Stats, mock_api).
    """

    def _run(
        catc_devices: list[CatCDevice] | None = None,
        radkit_devices: tuple[dict[str, Any], dict[str, Any]] | None = None,
        *,
        adopt: bool = False,
        dry_run: bool = False,
        update_pw: bool = False,
    ) -> tuple[Stats, MagicMock]:
        if catc_devices is None:
            catc_devices = []
        if radkit_devices is None:
            radkit_devices = ({}, {})

        config = AppConfig(
            catc_clusters=["https://catc1.example.com"],
            device_whitelist=[],
            device_blacklist=[],
            adopt_existing=adopt,
        )

        # Convert dict-based test fixtures into StoredRadkitDevice objects.
        # Tolerates both the full managed shape (host/device_type/metadata)
        # and the minimal unmanaged shape ({uuid, catc_source}).
        def _to_stored(name: str, data: dict[str, Any]) -> StoredRadkitDevice:
            return StoredRadkitDevice(
                name=name,
                uuid=data["uuid"],
                host=data.get("host", ""),
                device_type=data.get("device_type", ""),
                catc_source=data.get("catc_source", ""),
                metadata=data.get("metadata", {}),
            )

        managed_dict = {n: _to_stored(n, d) for n, d in radkit_devices[0].items()}
        unmanaged_dict = {n: _to_stored(n, d) for n, d in radkit_devices[1].items()}

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=catc_devices),
            patch(
                "radkit_catc_sync.sync.fetch_radkit_devices",
                return_value=(managed_dict, unmanaged_dict),
            ),
        ):
            from radkit_catc_sync.sync import run_sync as run_sync_impl

            stats = run_sync_impl(
                config=config,
                dry_run=dry_run,
                update_credentials=update_pw,
                catc_user=_SYNC_CREDS["catc_user"],
                catc_password=_SYNC_CREDS["catc_password"],
                radkit_admin_user=_SYNC_CREDS["radkit_admin_user"],
                radkit_admin_password=_SYNC_CREDS["radkit_admin_password"],
                ssh_user=_SYNC_CREDS["ssh_user"],
                ssh_password=_SYNC_CREDS["ssh_password"],
            )
            return stats, mock_controlapi

    return _run
