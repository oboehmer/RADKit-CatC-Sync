"""Tests for run_sync integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from radkit_common.types import DeviceType

from radkit_catc_sync.config import AppConfig
from radkit_catc_sync.stats import Stats
from radkit_catc_sync.sync import run_sync as run_sync_impl

# ---------------------------------------------------------------------------
# TestRunSyncIntegration — comprehensive run_sync with content verification
# ---------------------------------------------------------------------------


class TestRunSyncIntegration:
    def test_add_verifies_new_device_content(
        self, run_sync: Any, make_device: Any, mock_controlapi: MagicMock
    ) -> None:
        stats, api = run_sync(catc_devices=[make_device("router1.example.com", "192.168.1.1")])
        assert stats.added == 1
        api.create_devices.assert_called_once()
        call_args = api.create_devices.call_args
        new_dev = call_args[0][0][0]
        assert new_dev.name == "router1"
        assert new_dev.host == "192.168.1.1"
        assert new_dev.device_type == DeviceType.IOS_XE
        assert "Imported from CatC:" in new_dev.description
        assert new_dev.enabled is True

    def test_update_verifies_update_device_content(
        self, run_sync: Any, make_device: Any, mock_controlapi: MagicMock
    ) -> None:
        uid = str(uuid4())
        managed = {
            "router1": {
                "uuid": uid,
                "catc_source": "catc1.example.com",
                "host": "192.168.1.100",
                "device_type": str(DeviceType.IOS_XE),
                "metadata": {},
            }
        }
        stats, api = run_sync(
            catc_devices=[make_device("router1.example.com", "192.168.1.1")],
            radkit_devices=(managed, {}),
        )
        assert stats.updated == 1
        api.update_devices.assert_called_once()
        call_args = api.update_devices.call_args
        upd_dev = call_args[0][0][0]
        assert str(upd_dev.uuid) == uid
        assert upd_dev.host == "192.168.1.1"
        assert upd_dev.device_type == DeviceType.IOS_XE

    def test_summary_dry_run_header(self) -> None:
        stats = Stats()
        summary_dry = stats.summary(dry_run=True)
        summary_live = stats.summary(dry_run=False)
        assert "DRY-RUN" in summary_dry
        assert "DRY-RUN" not in summary_live

    def test_summary_truncates_long_warnings(self) -> None:
        stats = Stats()
        long_warning = "x" * 250
        stats.warnings.append(long_warning)
        summary = stats.summary()
        # Check that the warning is truncated
        assert "..." in summary
        # Verify it's actually truncated (not the full 250 chars)
        lines = summary.split("\n")
        warning_line = next(line for line in lines if "xxx" in line)
        assert len(warning_line) < 250


# ---------------------------------------------------------------------------
# TestRunSyncErrorHandling — error handling and additional coverage
# ---------------------------------------------------------------------------


class TestRunSyncErrorHandling:
    def test_empty_clusters_raises(self, mock_controlapi: MagicMock) -> None:
        config = AppConfig(catc_clusters=[])
        with pytest.raises(ValueError, match="CATC_CLUSTERS is empty"):
            run_sync_impl(
                config=config,
                dry_run=False,
                update_passwords=False,
                catc_user="testuser",
                catc_password="testpassword",  # noqa: S106
                radkit_admin_user="testadmin",
                radkit_admin_password="testpassword",  # noqa: S106
                ssh_user="testnetops",
                ssh_password="testpassword",  # noqa: S106
            )

    def test_add_device_error_counted(
        self, run_sync: Any, mock_controlapi: MagicMock, make_device: Any
    ) -> None:
        mock_controlapi.create_devices.side_effect = RuntimeError("boom")
        stats, _ = run_sync(catc_devices=[make_device("r1.example.com")])
        assert stats.errors == 1
        assert stats.added == 0

    def test_adopt_error_counted(
        self, run_sync: Any, mock_controlapi: MagicMock, make_device: Any
    ) -> None:
        mock_controlapi.update_devices.side_effect = RuntimeError("boom")
        unmanaged = {"router1": {"uuid": str(uuid4()), "catc_source": ""}}
        stats, _ = run_sync(
            catc_devices=[make_device("router1.example.com")],
            radkit_devices=({}, unmanaged),
            adopt=True,
        )
        assert stats.errors == 1
        assert stats.adopted == 0

    def test_update_error_counted(
        self, run_sync: Any, mock_controlapi: MagicMock, make_device: Any
    ) -> None:
        mock_controlapi.update_devices.side_effect = RuntimeError("boom")
        uid = str(uuid4())
        managed = {
            "router1": {
                "uuid": uid,
                "catc_source": "catc1.example.com",
                "host": "192.168.1.100",  # Different IP to trigger update
            }
        }
        stats, _ = run_sync(
            catc_devices=[make_device("router1.example.com", "192.168.1.1")],
            radkit_devices=(managed, {}),
        )
        assert stats.errors == 1
        assert stats.updated == 0

    def test_delete_error_counted(self, run_sync: Any, mock_controlapi: MagicMock) -> None:
        mock_controlapi.delete_devices.side_effect = RuntimeError("boom")
        managed = {"old-router": {"uuid": str(uuid4()), "catc_source": "catc1.example.com"}}
        stats, _ = run_sync(catc_devices=[], radkit_devices=(managed, {}))
        assert stats.errors == 1
        assert stats.deleted == 0

    def test_dry_run_update(
        self, run_sync: Any, make_device: Any, mock_controlapi: MagicMock
    ) -> None:
        uid = str(uuid4())
        managed = {
            "router1": {
                "uuid": uid,
                "catc_source": "catc1.example.com",
                "host": "192.168.1.100",  # Different IP
            }
        }
        stats, api = run_sync(
            catc_devices=[make_device("router1.example.com", "192.168.1.1")],
            radkit_devices=(managed, {}),
            dry_run=True,
        )
        assert stats.updated == 1
        api.update_device.assert_not_called()

    def test_summary_all_counters(self) -> None:
        stats = Stats()
        stats.added = 1
        stats.updated = 2
        stats.deleted = 3
        stats.adopted = 4
        stats.skipped = 5
        stats.unchanged = 6
        stats.errors = 7
        summary = stats.summary()
        # Verify all counter labels present
        assert "Added:" in summary
        assert "Updated:" in summary
        assert "Deleted:" in summary
        assert "Adopted:" in summary
        assert "Skipped:" in summary
        assert "Unchanged:" in summary
        assert "Errors:" in summary
