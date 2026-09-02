# tests/test_scenarios.py — parametrized integration scenario tests
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from radkit_catc_sync.builders import build_metadata, get_device_type
from radkit_catc_sync.catc_client import CatCClient
from radkit_catc_sync.config import AppConfig
from radkit_catc_sync.models import CatCDevice, StoredRadkitDevice
from radkit_catc_sync.sync import normalise_name, run_sync

# ---------------------------------------------------------------------------
# Test scenario dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """One end-to-end sync scenario."""

    id: str

    # CatC inventory: (hostname_or_None, ip) tuples
    catc: tuple[tuple[str | None, str], ...] = ()

    # RADKit managed devices: (name, catc_source) — UUIDs auto-generated
    managed: tuple[tuple[str, str], ...] = ()

    # RADKit unmanaged device names
    unmanaged: tuple[str, ...] = ()

    # Filters
    whitelist: tuple[str, ...] = ()
    blacklist: tuple[str, ...] = ()

    # Flags
    adopt: bool = False
    dry_run: bool = False
    update_pw: bool = False

    # When True, build managed entries with matching host/device_type/catc_source/metadata
    # so run_sync considers them "unchanged"
    steady_state: bool = False

    # Expected stat counters
    exp_added: int = 0
    exp_updated: int = 0
    exp_deleted: int = 0
    exp_adopted: int = 0
    exp_unchanged: int = 0
    exp_skipped: int = 0
    exp_errors: int = 0

    # Expected API calls (dry_run scenarios should have 0 for all)
    exp_create_calls: int = 0
    exp_update_calls: int = 0
    exp_delete_calls: int = 0

    # Optional content verification — when set (not None), verify API call arguments.
    # Names are normalised RADKit device names (e.g. "router1", not "router1.example.com").
    exp_created_names: tuple[str, ...] | None = None  # names of devices passed to create_device
    exp_deleted_names: tuple[str, ...] | None = (
        None  # managed names whose UUIDs were passed to delete_device
    )
    exp_adopted_names: tuple[str, ...] | None = (
        None  # unmanaged names whose UUIDs were passed to update_device (adopts)
    )
    exp_updated_names: tuple[str, ...] | None = (
        None  # managed names whose UUIDs were passed to update_device (regular updates)
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

_CLUSTER = "https://catc1.example.com"
_CATC_HOSTNAME = "catc1.example.com"  # urlparse(_CLUSTER).hostname


def _build_catc_devices(
    specs: tuple[tuple[str | None, str], ...],
) -> list[CatCDevice]:
    """Build CatCDevice list from (hostname, ip) specs."""
    return [
        CatCDevice(
            hostname=h,
            management_ip=ip,
            software_type="IOS-XE",
            series=None,
            raw={"hostname": h, "managementIpAddress": ip},
        )
        for h, ip in specs
    ]


def _build_managed(
    specs: tuple[tuple[str, str], ...],
    catc_devices: list[CatCDevice] | None = None,
    steady_state: bool = False,
) -> dict[str, StoredRadkitDevice]:
    """Build managed dict.

    If steady_state=True and catc_devices provided, build entries with matching
    host, device_type, catc_source, and metadata so run_sync sees no changes.
    """
    result: dict[str, StoredRadkitDevice] = {}
    # Build lookup from normalised name → CatCDevice for steady_state
    catc_lookup: dict[str, CatCDevice] = {}
    if steady_state and catc_devices:
        for dev in catc_devices:
            if dev.hostname:
                catc_lookup[normalise_name(dev.hostname)] = dev

    _default = AppConfig()
    for name, source in specs:
        if steady_state and name in catc_lookup:
            dev = catc_lookup[name]
            device_type = str(get_device_type(dev.software_type, dev.series))
            meta = {
                m.key: m.value
                for m in build_metadata(
                    dev, source, _default.metadata_fields, _default.meta_source_key
                )
            }
            result[name] = StoredRadkitDevice(
                name=name,
                uuid=str(uuid4()),
                catc_source=source,
                host=dev.management_ip,
                device_type=device_type,
                metadata=meta,
            )
        else:
            result[name] = StoredRadkitDevice(
                name=name,
                uuid=str(uuid4()),
                catc_source=source,
                host="",
                device_type="",
                metadata={},
            )
    return result


def _build_unmanaged(names: tuple[str, ...]) -> dict[str, StoredRadkitDevice]:
    return {
        name: StoredRadkitDevice(
            name=name,
            uuid=str(uuid4()),
            catc_source="",
            host="",
            device_type="",
            metadata={},
        )
        for name in names
    }


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

SCENARIOS = [
    # --- Baseline ---
    Scenario(id="empty-sync"),
    # --- Add ---
    Scenario(
        id="add-three-new",
        catc=(
            ("r1.example.com", "10.0.0.1"),
            ("r2.example.com", "10.0.0.2"),
            ("r3.example.com", "10.0.0.3"),
        ),
        exp_added=3,
        exp_create_calls=3,
        exp_created_names=("r1", "r2", "r3"),
    ),
    # --- Steady state (unchanged) ---
    Scenario(
        id="steady-state-unchanged",
        catc=(("r1.example.com", "10.0.0.1"),),
        managed=(("r1", "catc1.example.com"),),
        steady_state=True,
        exp_unchanged=1,
    ),
    # --- Update (managed device has stale data) ---
    Scenario(
        id="update-changed-ip",
        catc=(("r1.example.com", "10.0.0.99"),),
        managed=(("r1", "catc1.example.com"),),  # non-steady-state: empty host/type triggers update
        exp_updated=1,
        exp_update_calls=1,
        exp_updated_names=("r1",),
    ),
    # --- Delete (managed device no longer in CatC) ---
    Scenario(
        id="device-removed",
        managed=(("old-router", "catc1.example.com"),),
        exp_deleted=1,
        exp_delete_calls=1,
        exp_deleted_names=("old-router",),
    ),
    # --- Add + delete in same run ---
    Scenario(
        id="add-and-remove",
        catc=(("new-router.example.com", "10.0.0.1"),),
        managed=(("gone-router", "catc1.example.com"),),
        exp_added=1,
        exp_create_calls=1,
        exp_deleted=1,
        exp_delete_calls=1,
        exp_created_names=("new-router",),
        exp_deleted_names=("gone-router",),
    ),
    # --- Hostname None skipped ---
    Scenario(
        id="hostname-none-skipped",
        catc=((None, "10.0.0.1"),),
        exp_skipped=1,
    ),
    # --- Whitelist: only matching device added ---
    Scenario(
        id="whitelist-allows-one",
        catc=(
            ("router-1.example.com", "10.0.0.1"),
            ("switch-1.example.com", "10.0.0.2"),
            ("switch-2.example.com", "10.0.0.3"),
        ),
        whitelist=("^router-",),
        exp_added=1,
        exp_create_calls=1,
        exp_skipped=2,
        exp_created_names=("router-1",),
    ),
    # --- Blacklist: one device excluded ---
    Scenario(
        id="blacklist-excludes-one",
        catc=(
            ("router-1.example.com", "10.0.0.1"),
            ("router-2.example.com", "10.0.0.2"),
            ("router-lab.lab.example.com", "10.0.0.3"),
        ),
        blacklist=(r"\.lab\.",),
        exp_added=2,
        exp_create_calls=2,
        exp_skipped=1,
        exp_created_names=("router-1", "router-2"),
    ),
    # --- Blacklist overrides whitelist ---
    Scenario(
        id="blacklist-overrides-whitelist",
        catc=(("router-lab.lab.example.com", "10.0.0.1"),),
        whitelist=("^router-",),
        blacklist=(r"\.lab\.",),
        exp_skipped=1,
    ),
    # --- Unmanaged conflict: no adopt ---
    Scenario(
        id="unmanaged-skip-no-adopt",
        catc=(("switch-1.example.com", "10.0.0.1"),),
        unmanaged=("switch-1",),
        adopt=False,
        exp_skipped=1,
    ),
    # --- Unmanaged conflict: adopt ---
    Scenario(
        id="unmanaged-adopted",
        catc=(("switch-1.example.com", "10.0.0.1"),),
        unmanaged=("switch-1",),
        adopt=True,
        exp_adopted=1,
        exp_update_calls=1,
        exp_adopted_names=("switch-1",),
    ),
    # --- Filtered managed: deleted ---
    Scenario(
        id="filtered-managed-deleted",
        catc=(("ap-1.example.com", "10.0.0.1"),),
        managed=(("ap-1", "catc1.example.com"),),
        blacklist=("^ap-",),
        exp_skipped=1,
        exp_deleted=1,
        exp_delete_calls=1,
        exp_deleted_names=("ap-1",),
    ),
    # --- Filtered unmanaged with adopt enabled ---
    Scenario(
        id="filtered-unmanaged-with-adopt",
        catc=(("ap-2.example.com", "10.0.0.1"),),
        unmanaged=("ap-2",),
        blacklist=("^ap-",),
        adopt=True,
        exp_skipped=1,
    ),
    # --- Dry-run: add + delete (stats counted, no API calls) ---
    Scenario(
        id="dry-run-add-and-delete",
        catc=(("new-router.example.com", "10.0.0.1"),),
        managed=(("gone-router", "catc1.example.com"),),
        dry_run=True,
        exp_added=1,
        exp_deleted=1,
        exp_create_calls=0,
        exp_update_calls=0,
        exp_delete_calls=0,
    ),
    # --- Dry-run adopt ---
    Scenario(
        id="dry-run-adopt",
        catc=(("switch-1.example.com", "10.0.0.1"),),
        unmanaged=("switch-1",),
        adopt=True,
        dry_run=True,
        exp_adopted=1,
        exp_create_calls=0,
        exp_update_calls=0,
        exp_delete_calls=0,
    ),
    # --- Mixed complex scenario ---
    Scenario(
        id="mixed-add-update-delete-filter-skip",
        catc=(
            ("new-router.example.com", "10.0.0.1"),  # new → add
            ("existing-router.example.com", "10.0.0.2"),  # managed, data stale → update
            ("ap-filtered.example.com", "10.0.0.3"),  # blacklisted → skipped
            ("conflict-sw.example.com", "10.0.0.4"),  # unmanaged conflict, no adopt → skipped
        ),
        managed=(
            ("existing-router", "catc1.example.com"),  # will be updated
            ("gone-device", "catc1.example.com"),  # not in CatC → deleted
            ("ap-filtered", "catc1.example.com"),  # blacklisted → skipped + managed → deleted
        ),
        unmanaged=("conflict-sw",),
        blacklist=("^ap-",),
        adopt=False,
        exp_added=1,
        exp_create_calls=1,
        exp_updated=1,
        exp_update_calls=1,
        exp_deleted=2,
        exp_delete_calls=2,
        exp_skipped=2,  # 1 filtered + 1 unmanaged conflict
        exp_created_names=("new-router",),
        exp_updated_names=("existing-router",),
        exp_deleted_names=("gone-device", "ap-filtered"),
    ),
    # --- All CatC devices filtered out, managed deleted ---
    Scenario(
        id="all-filtered-managed-deleted",
        catc=(
            ("ap-1.example.com", "10.0.0.1"),
            ("ap-2.example.com", "10.0.0.2"),
        ),
        managed=(
            ("ap-1", "catc1.example.com"),
            ("ap-2", "catc1.example.com"),
        ),
        blacklist=("^ap-",),
        exp_skipped=2,
        exp_deleted=2,
        exp_delete_calls=2,
        exp_deleted_names=("ap-1", "ap-2"),
    ),
    # --- Adopt + add in same run ---
    Scenario(
        id="adopt-plus-add",
        catc=(
            ("switch-1.example.com", "10.0.0.1"),
            ("router-1.example.com", "10.0.0.2"),
        ),
        unmanaged=("switch-1",),
        adopt=True,
        exp_adopted=1,
        exp_update_calls=1,
        exp_added=1,
        exp_create_calls=1,
        exp_created_names=("router-1",),
        exp_adopted_names=("switch-1",),
    ),
    # --- Password update triggers update even when unchanged ---
    Scenario(
        id="password-refresh-forces-update",
        catc=(("r1.example.com", "10.0.0.1"),),
        managed=(("r1", "catc1.example.com"),),
        steady_state=True,
        update_pw=True,
        exp_updated=1,
        exp_update_calls=1,
        exp_updated_names=("r1",),
    ),
    # --- Managed device from different cluster not deleted ---
    Scenario(
        id="other-cluster-not-deleted",
        managed=(("other-cluster-device", "catc2.example.com"),),  # different source
        exp_deleted=0,  # not scoped to catc2
    ),
]


# ---------------------------------------------------------------------------
# The parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_sync_scenario(scenario: Scenario, mock_controlapi: MagicMock) -> None:
    """Unified integration test driven by scenario parameters."""
    # Build immutable config from scenario
    config = AppConfig(
        catc_clusters=[_CLUSTER],
        device_whitelist=list(scenario.whitelist),
        device_blacklist=list(scenario.blacklist),
        adopt_existing=scenario.adopt,
    )

    # Build test data
    catc_devices = _build_catc_devices(scenario.catc)
    managed = _build_managed(scenario.managed, catc_devices, scenario.steady_state)
    unmanaged = _build_unmanaged(scenario.unmanaged)

    # Patch CatC client + fetch_radkit_devices, let fetch_fresh_inventory run naturally
    with (
        patch.object(CatCClient, "authenticate"),
        patch.object(CatCClient, "get_devices", return_value=catc_devices),
        patch("radkit_catc_sync.sync.fetch_radkit_devices", return_value=(managed, unmanaged)),
    ):
        stats = run_sync(
            config=config,
            dry_run=scenario.dry_run,
            update_passwords=scenario.update_pw,
            catc_user="testuser",
            catc_password="testpassword",  # noqa: S106
            radkit_admin_user="testadmin",
            radkit_admin_password="testpassword",  # noqa: S106
            ssh_user="testnetops",
            ssh_password="testpassword",  # noqa: S106
        )

    # Assert stat counters
    assert stats.added == scenario.exp_added, f"added: {stats.added} != {scenario.exp_added}"
    assert (
        stats.updated == scenario.exp_updated
    ), f"updated: {stats.updated} != {scenario.exp_updated}"
    assert (
        stats.deleted == scenario.exp_deleted
    ), f"deleted: {stats.deleted} != {scenario.exp_deleted}"
    assert (
        stats.adopted == scenario.exp_adopted
    ), f"adopted: {stats.adopted} != {scenario.exp_adopted}"
    assert (
        stats.unchanged == scenario.exp_unchanged
    ), f"unchanged: {stats.unchanged} != {scenario.exp_unchanged}"
    assert (
        stats.skipped == scenario.exp_skipped
    ), f"skipped: {stats.skipped} != {scenario.exp_skipped}"
    assert stats.errors == scenario.exp_errors, f"errors: {stats.errors} != {scenario.exp_errors}"

    # Assert API call counts
    assert mock_controlapi.create_device.call_count == scenario.exp_create_calls, (
        f"create_device calls: {mock_controlapi.create_device.call_count} "
        f"!= {scenario.exp_create_calls}"
    )
    assert mock_controlapi.update_device.call_count == scenario.exp_update_calls, (
        f"update_device calls: {mock_controlapi.update_device.call_count} "
        f"!= {scenario.exp_update_calls}"
    )
    assert mock_controlapi.delete_device.call_count == scenario.exp_delete_calls, (
        f"delete_device calls: {mock_controlapi.delete_device.call_count} "
        f"!= {scenario.exp_delete_calls}"
    )

    # --- Content verification ---

    # Verify created devices
    if scenario.exp_created_names is not None:
        created_devs = [call[0][0] for call in mock_controlapi.create_device.call_args_list]
        actual_names = sorted(d.name for d in created_devs)
        assert actual_names == sorted(
            scenario.exp_created_names
        ), f"created names: {actual_names} != {sorted(scenario.exp_created_names)}"
        # All created devices must be enabled with correct description
        for d in created_devs:
            assert d.enabled is True, f"created device '{d.name}' not enabled"
            assert (
                "Imported from CatC:" in d.description
            ), f"created device '{d.name}' missing description prefix"

    # Verify deleted devices (match UUIDs back to managed dict)
    if scenario.exp_deleted_names is not None:
        deleted_uuids = {call[0][0] for call in mock_controlapi.delete_device.call_args_list}
        expected_uuids = {managed[n].uuid for n in scenario.exp_deleted_names}
        assert (
            deleted_uuids == expected_uuids
        ), f"deleted UUIDs don't match expected managed entries: {scenario.exp_deleted_names}"

    # Verify updated/adopted devices (both use update_device)
    if scenario.exp_updated_names is not None or scenario.exp_adopted_names is not None:
        updated_devs = [call[0][0] for call in mock_controlapi.update_device.call_args_list]
        updated_uuids = {str(u.uuid) for u in updated_devs}

        if scenario.exp_updated_names is not None:
            for name in scenario.exp_updated_names:
                assert (
                    managed[name].uuid in updated_uuids
                ), f"managed device '{name}' UUID not found in update_device calls"

        if scenario.exp_adopted_names is not None:
            for name in scenario.exp_adopted_names:
                assert (
                    unmanaged[name].uuid in updated_uuids
                ), f"unmanaged device '{name}' UUID not found in update_device calls (adopt)"
