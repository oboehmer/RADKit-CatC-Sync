"""Tests for rename detection and the rename guard.

A device whose name changes looks like a delete plus an add. RADKit supports
renaming in place via UpdateDevice.name, so these pairs are detected and
applied as updates — preserving the device UUID and everything attached to it.

The guard still exists because a [sync.naming] change renames the entire
managed inventory at once, and anything referring to those devices by name
needs to know.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from radkit_catc_sync.models import CatCDevice, StoredRadkitDevice
from radkit_catc_sync.sync import RenameGuardError, detect_renames

# ---------------------------------------------------------------------------
# detect_renames
# ---------------------------------------------------------------------------


def _stored(name: str, *, device_id: str = "", host: str = "") -> StoredRadkitDevice:
    return StoredRadkitDevice(
        name=name,
        uuid=str(uuid4()),
        host=host,
        device_type="IOS",
        catc_source="catc1.example.com",
        metadata={"id": device_id} if device_id else {},
    )


class TestDetectRenames:
    def test_matches_on_catc_device_id(self, make_device: Any) -> None:
        device = make_device("r1.example.com", "10.0.0.1", device_id="abc")
        managed = {"r1": _stored("r1", device_id="abc")}
        fresh = {"r1-example-com": (device, "catc1.example.com")}

        renames = detect_renames(managed, fresh, ["r1"], ["r1-example-com"])

        assert renames == [("r1", "r1-example-com")]

    def test_falls_back_to_management_ip(self, make_device: Any) -> None:
        """When 'id' is not a synced metadata field, the IP identifies the device."""
        device = make_device("r1.example.com", "10.0.0.1")
        managed = {"r1": _stored("r1", host="10.0.0.1")}
        fresh = {"r1-example-com": (device, "catc1.example.com")}

        renames = detect_renames(managed, fresh, ["r1"], ["r1-example-com"])

        assert renames == [("r1", "r1-example-com")]

    def test_genuine_delete_and_add_is_not_a_rename(self, make_device: Any) -> None:
        """A decommissioned device plus an unrelated new device is not a rename."""
        device = make_device("new.example.com", "10.0.0.2", device_id="new-id")
        managed = {"old": _stored("old", device_id="old-id", host="10.0.0.1")}
        fresh = {"new-example-com": (device, "catc1.example.com")}

        renames = detect_renames(managed, fresh, ["old"], ["new-example-com"])

        assert renames == []

    def test_unidentifiable_device_is_not_matched(self, make_device: Any) -> None:
        """No id and no host means no reliable identity — do not guess."""
        device = make_device("r1.example.com", "10.0.0.1")
        managed = {"r1": _stored("r1")}  # no id, no host
        fresh = {"r1-example-com": (device, "catc1.example.com")}

        assert detect_renames(managed, fresh, ["r1"], ["r1-example-com"]) == []

    def test_result_is_sorted(self, make_device: Any) -> None:
        devices = {
            f"r{i}-example-com": (
                make_device(f"r{i}.example.com", f"10.0.0.{i}", device_id=f"id{i}"),
                "catc1.example.com",
            )
            for i in (1, 2, 3)
        }
        managed = {f"r{i}": _stored(f"r{i}", device_id=f"id{i}") for i in (1, 2, 3)}

        renames = detect_renames(managed, devices, ["r3", "r1", "r2"], list(devices))

        assert renames == [
            ("r1", "r1-example-com"),
            ("r2", "r2-example-com"),
            ("r3", "r3-example-com"),
        ]


# ---------------------------------------------------------------------------
# Guard behaviour inside run_sync
# ---------------------------------------------------------------------------


def _catc_and_managed(count: int) -> tuple[list[CatCDevice], dict[str, dict[str, Any]]]:
    """Build `count` devices that are managed under short names but fetched as FQDNs."""
    catc_devices = [
        CatCDevice(
            device_id=f"id{i}",
            hostname=f"r{i}.example.com",
            management_ip=f"10.0.0.{i}",
            software_type="IOS-XE",
            series=None,
            raw={
                "id": f"id{i}",
                "hostname": f"r{i}.example.com",
                "managementIpAddress": f"10.0.0.{i}",
            },
        )
        for i in range(1, count + 1)
    ]
    managed = {
        f"r{i}": {
            "uuid": str(uuid4()),
            "host": f"10.0.0.{i}",
            "device_type": "IOS",
            "catc_source": "catc1.example.com",
            "metadata": {"id": f"id{i}"},
        }
        for i in range(1, count + 1)
    }
    return catc_devices, managed


class TestRenameGuardInRunSync:
    def test_naming_mode_change_aborts_before_any_change(
        self, run_sync: Any, mock_controlapi: MagicMock
    ) -> None:
        """The exact regression: flipping mode to fqdn must not silently recreate everything."""
        catc_devices, managed = _catc_and_managed(20)

        with pytest.raises(RenameGuardError, match="Refusing to rename 20 device"):
            run_sync(
                catc_devices,
                (managed, {}),
                name_mode="fqdn",  # devices are managed under short names
            )

        # Nothing was applied at all.
        mock_controlapi.create_devices.assert_not_called()
        mock_controlapi.delete_devices.assert_not_called()
        mock_controlapi.update_devices.assert_not_called()

    def test_allow_renames_applies_them_in_place(self, run_sync: Any) -> None:
        """Renames go through update_devices — never create + delete."""
        catc_devices, managed = _catc_and_managed(20)

        stats, api = run_sync(
            catc_devices,
            (managed, {}),
            name_mode="fqdn",
            allow_renames=True,
        )

        assert stats.renamed == 20
        assert stats.added == 0
        assert stats.deleted == 0
        api.update_devices.assert_called()
        api.create_devices.assert_not_called()
        api.delete_devices.assert_not_called()

    def test_rename_preserves_uuid_and_sets_new_name(self, run_sync: Any) -> None:
        """The whole point: the device keeps its identity across a rename."""
        catc_devices, managed = _catc_and_managed(1)
        original_uuid = managed["r1"]["uuid"]

        _, api = run_sync(catc_devices, (managed, {}), name_mode="fqdn")

        sent = api.update_devices.call_args[0][0]
        assert len(sent) == 1
        assert str(sent[0].uuid) == original_uuid
        assert sent[0].name == "r1-example-com"

    def test_renames_within_limit_are_allowed(self, run_sync: Any) -> None:
        """A handful of genuine CatC hostname changes must not need a flag."""
        catc_devices, managed = _catc_and_managed(3)

        stats, api = run_sync(catc_devices, (managed, {}), name_mode="fqdn")

        assert stats.renamed == 3
        assert stats.added == 0
        assert stats.deleted == 0
        api.delete_devices.assert_not_called()

    def test_dry_run_reports_but_does_not_raise(self, run_sync: Any) -> None:
        """--dry-run must always show the full picture, never abort early."""
        catc_devices, managed = _catc_and_managed(20)

        stats, api = run_sync(catc_devices, (managed, {}), name_mode="fqdn", dry_run=True)

        assert stats.renamed == 20
        api.update_devices.assert_not_called()
        api.create_devices.assert_not_called()
        api.delete_devices.assert_not_called()

    def test_rename_limit_zero_blocks_any_rename(self, run_sync: Any) -> None:
        catc_devices, managed = _catc_and_managed(1)

        with pytest.raises(RenameGuardError, match="Refusing to rename 1 device"):
            run_sync(catc_devices, (managed, {}), name_mode="fqdn", rename_limit=0)

    def test_rename_limit_negative_disables_guard(self, run_sync: Any) -> None:
        catc_devices, managed = _catc_and_managed(50)

        stats, _ = run_sync(catc_devices, (managed, {}), name_mode="fqdn", rename_limit=-1)

        assert stats.renamed == 50

    def test_no_renames_leaves_counter_at_zero(self, run_sync: Any) -> None:
        catc_devices, managed = _catc_and_managed(3)

        stats, _ = run_sync(catc_devices, (managed, {}), name_mode="short")

        assert stats.renamed == 0
        assert stats.deleted == 0
        assert stats.updated == 3  # metadata refresh only, no name change
