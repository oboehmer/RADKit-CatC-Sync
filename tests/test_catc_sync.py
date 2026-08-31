# RADKit-CatC-Sync — test suite
#
# Tests cover pure-Python logic (name normalisation, filters, sync
# orchestration).  RADKit is installed in the test environment; only the
# network-facing ControlAPI and CatCClient calls are mocked.

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import catc_sync

# ---------------------------------------------------------------------------
# normalise_name
# ---------------------------------------------------------------------------


class TestNormaliseName:
    def test_strips_domain(self) -> None:
        assert catc_sync.normalise_name("router1.dc1.example.com") == "router1"

    def test_no_domain(self) -> None:
        assert catc_sync.normalise_name("router1") == "router1"

    def test_lowercases(self) -> None:
        assert catc_sync.normalise_name("ROUTER1.example.com") == "router1"

    def test_collapses_double_hyphen(self) -> None:
        assert catc_sync.normalise_name("sw--core.example.com") == "sw-core"

    def test_combined(self) -> None:
        assert catc_sync.normalise_name("SW--CORE1.dc.example.com") == "sw-core1"


# ---------------------------------------------------------------------------
# should_import
# ---------------------------------------------------------------------------


class TestShouldImport:
    def setup_method(self) -> None:
        catc_sync.DEVICE_WHITELIST = []
        catc_sync.DEVICE_BLACKLIST = []
        catc_sync.compile_filters()

    def test_no_filters_allows_all(self) -> None:
        assert catc_sync.should_import("router1.dc1.example.com") is True

    def test_blacklist_blocks_on_fqdn(self) -> None:
        catc_sync.DEVICE_BLACKLIST = [r"\.lab\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw01.lab.example.com") is False
        assert catc_sync.should_import("sw01.prod.example.com") is True

    def test_blacklist_blocks_on_shortname_part(self) -> None:
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-lab-01.example.com") is False
        assert catc_sync.should_import("sw-prod-01.example.com") is True

    def test_whitelist_allows_only_matching(self) -> None:
        catc_sync.DEVICE_WHITELIST = [r"^router-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("router-01.example.com") is True
        assert catc_sync.should_import("switch-01.example.com") is False

    def test_blacklist_beats_whitelist(self) -> None:
        catc_sync.DEVICE_WHITELIST = [r"^router-"]
        catc_sync.DEVICE_BLACKLIST = [r"\.lab\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("router-01.lab.example.com") is False

    def test_whitelist_domain_match(self) -> None:
        catc_sync.DEVICE_WHITELIST = [r"\.prod\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-core-01.prod.example.com") is True
        assert catc_sync.should_import("sw-edge-01.dev.example.com") is False

    def test_search_matches_anywhere(self) -> None:
        # re.search — pattern need not be anchored to match
        catc_sync.DEVICE_WHITELIST = [r"core"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-core-01.example.com") is True
        assert catc_sync.should_import("sw-edge-01.example.com") is False

    def test_case_insensitive(self) -> None:
        # re.IGNORECASE — uppercase hostnames match lowercase patterns
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("SW-LAB-01.EXAMPLE.COM") is False
        assert catc_sync.should_import("SW-PROD-01.EXAMPLE.COM") is True


# ---------------------------------------------------------------------------
# fetch_radkit_devices — split logic
# ---------------------------------------------------------------------------


class TestFetchRadkitDevices:
    def _make_stored_device(self, name: str, uuid: str, meta_kv: dict[str, str]) -> MagicMock:
        dev = MagicMock()
        dev.name = name
        dev.uuid = uuid
        dev.meta_data = [MagicMock(key=k, value=v) for k, v in meta_kv.items()]
        return dev

    def _make_api(self, devices: list[MagicMock]) -> MagicMock:
        api = MagicMock()
        result = MagicMock()
        result.is_error.return_value = False
        result.result = devices
        api.list_devices.return_value = result
        return api

    def test_managed_device_has_catc_source(self) -> None:
        uid = str(uuid4())
        dev = self._make_stored_device("router1", uid, {"catc_source": "catc1.example.com"})
        api = self._make_api([dev])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert "router1" in managed
        assert managed["router1"]["catc_source"] == "catc1.example.com"
        assert "router1" not in unmanaged

    def test_unmanaged_device_has_no_catc_source(self) -> None:
        dev = self._make_stored_device("switch1", str(uuid4()), {})
        api = self._make_api([dev])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert "switch1" in unmanaged
        assert "switch1" not in managed

    def test_empty_catc_source_is_unmanaged(self) -> None:
        dev = self._make_stored_device("switch2", str(uuid4()), {"catc_source": ""})
        api = self._make_api([dev])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert "switch2" in unmanaged
        assert "switch2" not in managed

    def test_mixed_devices(self) -> None:
        devices = [
            self._make_stored_device("r1", str(uuid4()), {"catc_source": "catc1.example.com"}),
            self._make_stored_device("r2", str(uuid4()), {}),
            self._make_stored_device("r3", str(uuid4()), {"catc_source": "catc2.example.com"}),
        ]
        api = self._make_api(devices)
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert set(managed.keys()) == {"r1", "r3"}
        assert set(unmanaged.keys()) == {"r2"}

    def test_empty_inventory(self) -> None:
        api = self._make_api([])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert managed == {}
        assert unmanaged == {}


# ---------------------------------------------------------------------------
# run_sync — integration mock (add / update / delete / skip unmanaged)
# ---------------------------------------------------------------------------


class TestRunSync:
    """
    Integration-level tests with both RADKit ControlAPI and CatCClient mocked.
    """

    def _device(self, hostname: str, ip: str = "10.0.0.1") -> catc_sync.CatCDevice:
        return catc_sync.CatCDevice(
            hostname=hostname,
            management_ip=ip,
            software_type="IOS-XE",
            series=None,
            raw={"hostname": hostname, "managementIpAddress": ip},
        )

    def _run(
        self,
        catc_devices: list[catc_sync.CatCDevice],
        radkit_devices: tuple[dict[str, Any], dict[str, Any]],
        adopt: bool = False,
        delete_filtered: bool = False,
        dry_run: bool = False,
        update_pw: bool = False,
    ) -> tuple[catc_sync.Stats, MagicMock]:
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        catc_sync.DEVICE_WHITELIST = []
        catc_sync.DEVICE_BLACKLIST = []

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=catc_devices),
            patch("catc_sync.fetch_radkit_devices", return_value=radkit_devices),
            patch("catc_sync.ControlAPI") as mock_api_cls,
            patch("catc_sync.APIResult") as mock_api_result,
        ):
            mock_api_result.is_error.return_value = False

            mock_api = MagicMock()
            mock_api.__enter__ = MagicMock(return_value=mock_api)
            mock_api.__exit__ = MagicMock(return_value=False)
            mock_api_cls.create.return_value = mock_api

            stats = catc_sync.run_sync(
                dry_run=dry_run,
                update_passwords=update_pw,
                adopt_existing=adopt,
                delete_filtered=delete_filtered,
                catc_user="testuser",
                catc_password="testpassword",  # noqa: S106
                radkit_admin_user="testadmin",
                radkit_admin_password="testpassword",  # noqa: S106
                ssh_user="testnetops",
                ssh_password="testpassword",  # noqa: S106
            )
            return stats, mock_api

    def test_new_device_is_added(self) -> None:
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=({}, {}),
        )
        assert stats.added == 1
        assert stats.errors == 0
        api.create_device.assert_called_once()

    def test_existing_managed_device_is_updated(self) -> None:
        uid = str(uuid4())
        managed = {"router1": {"uuid": uid, "catc_source": "catc1.example.com"}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=(managed, {}),
        )
        assert stats.updated == 1
        assert stats.added == 0
        api.update_device.assert_called_once()

    def test_removed_device_is_deleted(self) -> None:
        uid = str(uuid4())
        managed = {"old-router": {"uuid": uid, "catc_source": "catc1.example.com"}}
        stats, api = self._run(
            catc_devices=[],
            radkit_devices=(managed, {}),
        )
        assert stats.deleted == 1
        api.delete_device.assert_called_once_with(uid)

    def test_unmanaged_conflict_skipped_without_flag(self) -> None:
        unmanaged = {"router1": {"uuid": str(uuid4()), "catc_source": ""}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=({}, unmanaged),
            adopt=False,
        )
        assert stats.skipped == 1
        assert stats.adopted == 0
        api.create_device.assert_not_called()
        api.update_device.assert_not_called()

    def test_unmanaged_conflict_adopted_with_flag(self) -> None:
        unmanaged = {"router1": {"uuid": str(uuid4()), "catc_source": ""}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=({}, unmanaged),
            adopt=True,
        )
        assert stats.adopted == 1
        assert stats.skipped == 0
        api.update_device.assert_called_once()

    def test_dry_run_makes_no_api_calls(self) -> None:
        managed = {"old-router": {"uuid": str(uuid4()), "catc_source": "catc1.example.com"}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=(managed, {}),
            dry_run=True,
        )
        assert stats.added == 1
        assert stats.deleted == 1
        api.create_device.assert_not_called()
        api.delete_device.assert_not_called()
        api.update_device.assert_not_called()

    def test_deletion_not_scoped_to_other_clusters(self) -> None:
        """Devices from a cluster not in CATC_CLUSTERS are not deleted."""
        managed = {
            "router-other": {
                "uuid": str(uuid4()),
                "catc_source": "catc2.example.com",  # different cluster
            }
        }
        stats, api = self._run(
            catc_devices=[],
            radkit_devices=(managed, {}),
        )
        assert stats.deleted == 0
        api.delete_device.assert_not_called()
