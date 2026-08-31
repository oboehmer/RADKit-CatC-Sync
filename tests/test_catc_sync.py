# RADKit-CatC-Sync — test suite
#
# These tests cover pure-Python logic only. All radkit_service imports are
# mocked so the tests run without a RADKit installation.

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub out radkit_service / radkit_common before importing catc_sync
# ---------------------------------------------------------------------------


def _make_mock_modules() -> None:
    """
    Create minimal stubs for every radkit module imported by catc_sync so the
    module can be imported in a plain Python environment.
    """
    # radkit_common.types — DeviceType needed by catc_sync._get_device_type
    rc_types = types.ModuleType("radkit_common.types")
    rc_types.ConnectionMethod = MagicMock()
    rc_types.ConnectionMethod.SSH = "SSH"
    rc_types.CustomSecretStr = str
    rc_types.DeviceType = MagicMock()
    rc_types.DeviceType.NX_OS = "NX_OS"
    rc_types.DeviceType.IOS_XE = "IOS_XE"
    rc_types.DeviceType.WLC = "WLC"
    rc_types.DeviceType.GENERIC = "GENERIC"

    # radkit_common.utils
    rc_utils = types.ModuleType("radkit_common.utils")
    import base64

    rc_utils.b64encode = base64.b64encode

    # radkit_common (parent)
    rc = types.ModuleType("radkit_common")
    rc.types = rc_types
    rc.utils = rc_utils

    # radkit_service stubs
    rs = types.ModuleType("radkit_service")
    rs_control = types.ModuleType("radkit_service.control_api")
    rs_control.ControlAPI = MagicMock()
    rs_control.APIResult = MagicMock()
    rs_control.APIResult.is_error = MagicMock(return_value=False)

    # helpers — no longer needed (catc_sync implements _get_device_type locally)
    rs_helpers_mod = types.ModuleType("radkit_service.webserver.connectors.catalyst_center.helpers")

    # models — only the empty stub is needed now; CatCDevice is defined locally
    rs_models_mod = types.ModuleType("radkit_service.webserver.connectors.catalyst_center.models")

    # connectors.utils
    rs_conn_utils = types.ModuleType("radkit_service.webserver.connectors.utils")

    def _dict_to_metadata(d):
        return [_FakeMetaDataEntry(k, str(v) if v is not None else "") for k, v in d.items()]

    rs_conn_utils.dict_to_metadata = _dict_to_metadata

    # devices models
    rs_dev_models = types.ModuleType("radkit_service.webserver.models.devices")

    class _FakeMetaDataEntry:
        def __init__(self, key, value=""):
            self.key = key
            self.value = value

    class _FakeNewDevice:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeUpdateDevice:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeNewTerminal:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeUpdateTerminal:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeUpdateMetaDataSet:
        def __init__(self, replace=None, add=None, remove=None):
            self.replace = replace
            self.add = add or []
            self.remove = remove or []

    rs_dev_models.MetaDataEntry = _FakeMetaDataEntry
    rs_dev_models.NewDevice = _FakeNewDevice
    rs_dev_models.UpdateDevice = _FakeUpdateDevice
    rs_dev_models.NewTerminal = _FakeNewTerminal
    rs_dev_models.UpdateTerminal = _FakeUpdateTerminal
    rs_dev_models.UpdateMetaDataSet = _FakeUpdateMetaDataSet

    # Register all modules
    sys.modules.update(
        {
            "radkit_common": rc,
            "radkit_common.types": rc_types,
            "radkit_common.utils": rc_utils,
            "radkit_service": rs,
            "radkit_service.control_api": rs_control,
            "radkit_service.webserver": types.ModuleType("radkit_service.webserver"),
            "radkit_service.webserver.connectors": types.ModuleType(
                "radkit_service.webserver.connectors"
            ),
            "radkit_service.webserver.connectors.catalyst_center": types.ModuleType(
                "radkit_service.webserver.connectors.catalyst_center"
            ),
            "radkit_service.webserver.connectors.catalyst_center.helpers": rs_helpers_mod,
            "radkit_service.webserver.connectors.catalyst_center.models": rs_models_mod,
            "radkit_service.webserver.connectors.utils": rs_conn_utils,
            "radkit_service.webserver.models": types.ModuleType("radkit_service.webserver.models"),
            "radkit_service.webserver.models.devices": rs_dev_models,
        }
    )


_make_mock_modules()

# Now safe to import
import catc_sync  # noqa: E402, I001


# ---------------------------------------------------------------------------
# normalise_name
# ---------------------------------------------------------------------------


class TestNormaliseName:
    def test_strips_domain(self):
        assert catc_sync.normalise_name("router1.dc1.example.com") == "router1"

    def test_no_domain(self):
        assert catc_sync.normalise_name("router1") == "router1"

    def test_lowercases(self):
        assert catc_sync.normalise_name("ROUTER1.example.com") == "router1"

    def test_collapses_double_hyphen(self):
        assert catc_sync.normalise_name("sw--core.example.com") == "sw-core"

    def test_combined(self):
        assert catc_sync.normalise_name("SW--CORE1.dc.example.com") == "sw-core1"


# ---------------------------------------------------------------------------
# should_import
# ---------------------------------------------------------------------------


class TestShouldImport:
    def setup_method(self):
        catc_sync.DEVICE_WHITELIST = []
        catc_sync.DEVICE_BLACKLIST = []
        catc_sync.compile_filters()

    def test_no_filters_allows_all(self):
        assert catc_sync.should_import("router1.dc1.example.com") is True

    def test_blacklist_blocks_on_fqdn(self):
        catc_sync.DEVICE_BLACKLIST = [r"\.lab\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw01.lab.example.com") is False
        assert catc_sync.should_import("sw01.prod.example.com") is True

    def test_blacklist_blocks_on_shortname_part(self):
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-lab-01.example.com") is False
        assert catc_sync.should_import("sw-prod-01.example.com") is True

    def test_whitelist_allows_only_matching(self):
        catc_sync.DEVICE_WHITELIST = [r"^router-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("router-01.example.com") is True
        assert catc_sync.should_import("switch-01.example.com") is False

    def test_blacklist_beats_whitelist(self):
        catc_sync.DEVICE_WHITELIST = [r"^router-"]
        catc_sync.DEVICE_BLACKLIST = [r"\.lab\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("router-01.lab.example.com") is False

    def test_whitelist_domain_match(self):
        catc_sync.DEVICE_WHITELIST = [r"\.prod\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-core-01.prod.example.com") is True
        assert catc_sync.should_import("sw-edge-01.dev.example.com") is False

    def test_search_matches_anywhere(self):
        # re.search — pattern need not be anchored to match
        catc_sync.DEVICE_WHITELIST = [r"core"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-core-01.example.com") is True
        assert catc_sync.should_import("sw-edge-01.example.com") is False

    def test_case_insensitive(self):
        # re.IGNORECASE — uppercase hostnames match lowercase patterns
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("SW-LAB-01.EXAMPLE.COM") is False
        assert catc_sync.should_import("SW-PROD-01.EXAMPLE.COM") is True


# ---------------------------------------------------------------------------
# fetch_radkit_devices — split logic
# ---------------------------------------------------------------------------


class TestFetchRadkitDevices:
    def _make_stored_device(self, name, uuid, meta_kv: dict):
        dev = MagicMock()
        dev.name = name
        dev.uuid = uuid
        dev.meta_data = [MagicMock(key=k, value=v) for k, v in meta_kv.items()]
        return dev

    def _make_api(self, devices):
        api = MagicMock()
        result = MagicMock()
        result.result = devices
        api.list_devices.return_value = result
        return api

    def test_managed_device_has_catc_source(self):
        dev = self._make_stored_device("router1", "uuid-1", {"catc_source": "catc1.example.com"})
        api = self._make_api([dev])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert "router1" in managed
        assert managed["router1"]["catc_source"] == "catc1.example.com"
        assert "router1" not in unmanaged

    def test_unmanaged_device_has_no_catc_source(self):
        dev = self._make_stored_device("switch1", "uuid-2", {})
        api = self._make_api([dev])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert "switch1" in unmanaged
        assert "switch1" not in managed

    def test_empty_catc_source_is_unmanaged(self):
        dev = self._make_stored_device("switch2", "uuid-3", {"catc_source": ""})
        api = self._make_api([dev])
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert "switch2" in unmanaged
        assert "switch2" not in managed

    def test_mixed_devices(self):
        devices = [
            self._make_stored_device("r1", "u1", {"catc_source": "catc1.example.com"}),
            self._make_stored_device("r2", "u2", {}),
            self._make_stored_device("r3", "u3", {"catc_source": "catc2.example.com"}),
        ]
        api = self._make_api(devices)
        managed, unmanaged = catc_sync.fetch_radkit_devices(api)
        assert set(managed.keys()) == {"r1", "r3"}
        assert set(unmanaged.keys()) == {"r2"}

    def test_empty_inventory(self):
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
        radkit_devices: tuple[dict, dict],
        adopt: bool = False,
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
        ):
            mock_api = MagicMock()
            mock_api.__enter__ = MagicMock(return_value=mock_api)
            mock_api.__exit__ = MagicMock(return_value=False)
            mock_api_cls.create.return_value = mock_api

            stats = catc_sync.run_sync(
                dry_run=dry_run,
                update_passwords=update_pw,
                adopt_existing=adopt,
                catc_user="user",
                catc_password="pass",
                radkit_admin_user="admin",
                radkit_admin_password="pass",
                ssh_user="netops",
                ssh_password="sshpass",
            )
            return stats, mock_api

    def test_new_device_is_added(self):
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=({}, {}),
        )
        assert stats.added == 1
        assert stats.errors == 0
        api.create_device.assert_called_once()

    def test_existing_managed_device_is_updated(self):
        managed = {"router1": {"uuid": "uuid-1", "catc_source": "catc1.example.com"}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=(managed, {}),
        )
        assert stats.updated == 1
        assert stats.added == 0
        api.update_device.assert_called_once()

    def test_removed_device_is_deleted(self):
        managed = {"old-router": {"uuid": "uuid-old", "catc_source": "catc1.example.com"}}
        stats, api = self._run(
            catc_devices=[],
            radkit_devices=(managed, {}),
        )
        assert stats.deleted == 1
        api.delete_device.assert_called_once_with("uuid-old")

    def test_unmanaged_conflict_skipped_without_flag(self):
        unmanaged = {"router1": {"uuid": "uuid-m", "catc_source": ""}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=({}, unmanaged),
            adopt=False,
        )
        assert stats.skipped == 1
        assert stats.adopted == 0
        api.create_device.assert_not_called()
        api.update_device.assert_not_called()

    def test_unmanaged_conflict_adopted_with_flag(self):
        unmanaged = {"router1": {"uuid": "uuid-m", "catc_source": ""}}
        stats, api = self._run(
            catc_devices=[self._device("router1.example.com")],
            radkit_devices=({}, unmanaged),
            adopt=True,
        )
        assert stats.adopted == 1
        assert stats.skipped == 0
        api.update_device.assert_called_once()

    def test_dry_run_makes_no_api_calls(self):
        managed = {"old-router": {"uuid": "uuid-old", "catc_source": "catc1.example.com"}}
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

    def test_deletion_not_scoped_to_other_clusters(self):
        """Devices from a cluster not in CATC_CLUSTERS are not deleted."""
        managed = {
            "router-other": {
                "uuid": "uuid-other",
                "catc_source": "catc2.example.com",  # different cluster
            }
        }
        stats, api = self._run(
            catc_devices=[],
            radkit_devices=(managed, {}),
        )
        assert stats.deleted == 0
        api.delete_device.assert_not_called()
