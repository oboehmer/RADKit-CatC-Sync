"""Tests for fetch_radkit_devices and fetch_fresh_inventory."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from radkit_catc_sync.catc_client import CatCClient
from radkit_catc_sync.config import AppConfig
from radkit_catc_sync.filters import FilterSet
from radkit_catc_sync.stats import SkipReason, Stats
from radkit_catc_sync.sync import (
    CatCInventoryError,
    fetch_fresh_inventory,
    fetch_radkit_devices,
)

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
        config = AppConfig()
        managed, unmanaged = fetch_radkit_devices(api, config)
        assert "router1" in managed
        assert managed["router1"].catc_source == "catc1.example.com"
        assert "router1" not in unmanaged

    def test_unmanaged_device_has_no_catc_source(self) -> None:
        dev = self._make_stored_device("switch1", str(uuid4()), {})
        api = self._make_api([dev])
        config = AppConfig()
        managed, unmanaged = fetch_radkit_devices(api, config)
        assert "switch1" in unmanaged
        assert "switch1" not in managed

    def test_empty_catc_source_is_unmanaged(self) -> None:
        dev = self._make_stored_device("switch2", str(uuid4()), {"catc_source": ""})
        api = self._make_api([dev])
        config = AppConfig()
        managed, unmanaged = fetch_radkit_devices(api, config)
        assert "switch2" in unmanaged
        assert "switch2" not in managed

    def test_mixed_devices(self) -> None:
        devices = [
            self._make_stored_device("r1", str(uuid4()), {"catc_source": "catc1.example.com"}),
            self._make_stored_device("r2", str(uuid4()), {}),
            self._make_stored_device("r3", str(uuid4()), {"catc_source": "catc2.example.com"}),
        ]
        api = self._make_api(devices)
        config = AppConfig()
        managed, unmanaged = fetch_radkit_devices(api, config)
        assert set(managed.keys()) == {"r1", "r3"}
        assert set(unmanaged.keys()) == {"r2"}

    def test_empty_inventory(self) -> None:
        api = self._make_api([])
        config = AppConfig()
        managed, unmanaged = fetch_radkit_devices(api, config)
        assert managed == {}
        assert unmanaged == {}


# ---------------------------------------------------------------------------
# fetch_fresh_inventory
# ---------------------------------------------------------------------------


class TestFetchFreshInventory:
    def test_normal_device_collected(self, make_device: Any) -> None:
        device = make_device("router1.example.com", "10.0.0.1")
        config = AppConfig(
            catc_clusters=["https://catc1.example.com"],
            device_whitelist=[],
            device_blacklist=[],
        )
        filters = FilterSet.from_lists([], [])
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        assert "router1-example-com" in fresh_dict
        fresh_device, catc_hostname = fresh_dict["router1-example-com"]
        assert fresh_device.hostname == "router1.example.com"
        assert catc_hostname == "catc1.example.com"

    def test_hostname_none_skipped(self, make_device: Any) -> None:
        device = make_device(hostname="")
        device.hostname = None  # Override after creation
        config = AppConfig(catc_clusters=["https://catc1.example.com"])
        filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        assert fresh_dict == {}
        assert stats.skipped == 1

    def test_filtered_device_skipped(self, make_device: Any) -> None:
        device = make_device("sw-lab-01.example.com", "10.0.0.1")
        config = AppConfig(
            catc_clusters=["https://catc1.example.com"],
            device_blacklist=[r"-lab-"],
        )
        filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        assert "sw-lab-01" not in fresh_dict
        assert stats.skipped == 1

    def test_duplicate_same_cluster_warned(self, make_device: Any) -> None:
        device1 = make_device("router1.example.com", "10.0.0.1")
        device2 = make_device("router1.example.com", "10.0.0.2")
        config = AppConfig(catc_clusters=["https://catc1.example.com"])
        filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device1, device2]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        # Only one should be in fresh_dict
        assert len(fresh_dict) == 1
        assert stats.skipped == 1
        assert any("Duplicate" in w for w in stats.warnings)

    def test_normalisation_collision_warned(self, make_device: Any) -> None:
        device1 = make_device("sw_core.example.com", "10.0.0.1")
        device2 = make_device("sw-core.example.com", "10.0.0.2")
        config = AppConfig(catc_clusters=["https://catc1.example.com"])
        filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device1, device2]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        # Both normalise to "sw-core"
        assert len(fresh_dict) == 1
        assert any("collision" in w for w in stats.warnings)

    def test_cross_cluster_collision_warned(self, make_device: Any) -> None:
        device1 = make_device("router1.a.example.com", "10.0.0.1")
        device2 = make_device("router1.b.example.com", "10.0.0.2")
        config = AppConfig(
            catc_clusters=["https://catc1.example.com", "https://catc2.example.com"],
            name_mode="short",
        )
        filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(
                CatCClient,
                "get_devices",
                side_effect=[[device1], [device2]],
            ),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        # Both normalise to "router1"
        assert len(fresh_dict) == 1
        assert any("collision" in w and "catc" in w.lower() for w in stats.warnings)

    def test_collision_winner_is_lowest_device_id(self, make_device: Any) -> None:
        """Collision resolution must not depend on CatC's return order.

        The CatC API gives no ordering guarantee. If the winner depended on
        response order, the surviving device's host/metadata would flap
        between runs, causing endless update churn.
        """
        low = make_device("sw_core.example.com", "10.0.0.1", device_id="aaa")
        high = make_device("sw-core.example.com", "10.0.0.2", device_id="zzz")
        config = AppConfig(catc_clusters=["https://catc1.example.com"])
        filters = FilterSet.from_lists([], [])

        for order in ([low, high], [high, low]):
            stats = Stats()
            with (
                patch.object(CatCClient, "authenticate"),
                patch.object(CatCClient, "get_devices", return_value=order),
            ):
                fresh_dict = fetch_fresh_inventory(
                    config=config,
                    filters=filters,
                    catc_user="user",
                    catc_password="pass",
                    stats=stats,
                )

            assert len(fresh_dict) == 1
            device, _ = fresh_dict["sw-core-example-com"]
            assert device.device_id == "aaa", f"winner flipped for input order {order}"

    def test_unnameable_hostname_skipped(self, make_device: Any) -> None:
        """A hostname with no usable characters is skipped, not crashed on."""
        device = make_device("...", "10.0.0.1")
        config = AppConfig(catc_clusters=["https://catc1.example.com"])
        filters = FilterSet.from_lists([], [])
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        assert fresh_dict == {}
        assert stats.skipped_by_reason[SkipReason.UNNAMEABLE] == 1

    def test_strip_domains_applied(self, make_device: Any) -> None:
        device = make_device("router1.dc1.example.com", "10.0.0.1")
        config = AppConfig(
            catc_clusters=["https://catc1.example.com"],
            name_strip_domains=[".example.com"],
        )
        filters = FilterSet.from_lists([], [])
        stats = Stats()

        with (
            patch.object(CatCClient, "authenticate"),
            patch.object(CatCClient, "get_devices", return_value=[device]),
        ):
            fresh_dict = fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )

        assert list(fresh_dict) == ["router1-dc1"]

    def test_cluster_fetch_failure_aborts(self, make_device: Any) -> None:
        """A cluster fetch failure must abort the whole sync, not continue.

        Proceeding with a partial/empty inventory would let Step 5 delete
        managed devices that are merely unreachable.
        """
        device = make_device("router2.example.com", "10.0.0.2")
        config = AppConfig(catc_clusters=["https://catc1.example.com", "https://catc2.example.com"])
        filters = FilterSet.from_lists(config.device_whitelist, config.device_blacklist)
        stats = Stats()

        def auth_side_effect(self: Any) -> None:
            if "catc1" in self.base_url:
                raise RuntimeError("Connection failed")
            # catc2 would succeed, but we should never reach it

        with (
            patch.object(
                CatCClient,
                "authenticate",
                side_effect=auth_side_effect,
                autospec=True,
            ),
            patch.object(CatCClient, "get_devices", return_value=[device]),
            pytest.raises(CatCInventoryError, match="catc1"),
        ):
            fetch_fresh_inventory(
                config=config,
                filters=filters,
                catc_user="user",
                catc_password="pass",
                stats=stats,
            )
