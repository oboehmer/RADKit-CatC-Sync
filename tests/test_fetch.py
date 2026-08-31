"""Tests for fetch_radkit_devices and fetch_fresh_inventory."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import catc_sync

# ---------------------------------------------------------------------------
# fetch_radkit_devices
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
# fetch_fresh_inventory
# ---------------------------------------------------------------------------


class TestFetchFreshInventory:
    def test_normal_device_collected(self, make_device: Any) -> None:
        device = make_device("router1.example.com", "10.0.0.1")
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        catc_sync.compile_filters()
        stats = catc_sync.Stats()

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com"], "user", "pass", stats, verify_tls=True
            )

        assert "router1" in fresh_dict
        fresh_device, catc_hostname = fresh_dict["router1"]
        assert fresh_device.hostname == "router1.example.com"
        assert catc_hostname == "catc1.example.com"

    def test_hostname_none_skipped(self, make_device: Any) -> None:
        device = make_device(hostname="")
        device.hostname = None  # Override after creation
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        stats = catc_sync.Stats()

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com"], "user", "pass", stats, verify_tls=True
            )

        assert fresh_dict == {}
        assert stats.skipped == 1

    def test_filtered_device_skipped(self, make_device: Any) -> None:
        device = make_device("sw-lab-01.example.com", "10.0.0.1")
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        stats = catc_sync.Stats()

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com"], "user", "pass", stats, verify_tls=True
            )

        assert "sw-lab-01" not in fresh_dict
        assert stats.skipped == 1

    def test_duplicate_same_cluster_warned(self, make_device: Any) -> None:
        device1 = make_device("router1.example.com", "10.0.0.1")
        device2 = make_device("router1.example.com", "10.0.0.2")
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        catc_sync.compile_filters()
        stats = catc_sync.Stats()

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=[device1, device2]),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com"], "user", "pass", stats, verify_tls=True
            )

        # Only one should be in fresh_dict
        assert len(fresh_dict) == 1
        assert stats.skipped == 1
        assert any("Duplicate" in w for w in stats.warnings)

    def test_normalisation_collision_warned(self, make_device: Any) -> None:
        device1 = make_device("sw_core.example.com", "10.0.0.1")
        device2 = make_device("sw-core.example.com", "10.0.0.2")
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com"]
        catc_sync.compile_filters()
        stats = catc_sync.Stats()

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=[device1, device2]),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com"], "user", "pass", stats, verify_tls=True
            )

        # Both normalise to "sw-core"
        assert len(fresh_dict) == 1
        assert any("collision" in w for w in stats.warnings)

    def test_cross_cluster_collision_warned(self, make_device: Any) -> None:
        device1 = make_device("router1.a.example.com", "10.0.0.1")
        device2 = make_device("router1.b.example.com", "10.0.0.2")
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com", "https://catc2.example.com"]
        catc_sync.compile_filters()
        stats = catc_sync.Stats()

        with (
            patch.object(catc_sync.CatCClient, "authenticate"),
            patch.object(
                catc_sync.CatCClient,
                "get_devices",
                side_effect=[[device1], [device2]],
            ),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com", "https://catc2.example.com"],
                "user",
                "pass",
                stats,
                verify_tls=True,
            )

        # Both normalise to "router1"
        assert len(fresh_dict) == 1
        assert any("collision" in w and "catc" in w.lower() for w in stats.warnings)

    def test_cluster_fetch_failure_continues(self, make_device: Any) -> None:
        device = make_device("router2.example.com", "10.0.0.2")
        catc_sync.CATC_CLUSTERS = ["https://catc1.example.com", "https://catc2.example.com"]
        catc_sync.compile_filters()
        stats = catc_sync.Stats()

        def auth_side_effect(self: Any) -> None:
            if "catc1" in self.base_url:
                raise RuntimeError("Connection failed")
            # catc2 succeeds

        with (
            patch.object(
                catc_sync.CatCClient,
                "authenticate",
                side_effect=auth_side_effect,
                autospec=True,
            ),
            patch.object(catc_sync.CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = catc_sync.fetch_fresh_inventory(
                ["https://catc1.example.com", "https://catc2.example.com"],
                "user",
                "pass",
                stats,
                verify_tls=True,
            )

        # catc2 should still succeed
        assert "router2" in fresh_dict
        assert stats.errors == 1
        assert any("catc1" in w.lower() for w in stats.warnings)
