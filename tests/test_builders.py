"""Tests for metadata and device builders."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from radkit_common.types import ConnectionMethod, DeviceType
from radkit_service.webserver.models.base import DontUpdate

import catc_sync

# ---------------------------------------------------------------------------
# _build_metadata
# ---------------------------------------------------------------------------


class TestBuildMetadata:
    def test_filters_to_metadata_fields(self, make_device: Any) -> None:
        device = make_device(
            raw_extra={
                "serialNumber": "SN123456",
                "platformId": "C9300-24U",
                "extraField": "should_be_filtered",
            }
        )
        meta = catc_sync._build_metadata(device, "catc1.example.com")
        keys = {m.key for m in meta}
        assert "serialNumber" in keys
        assert "platformId" in keys
        assert "extraField" not in keys
        assert "catc_source" in keys

    def test_none_values_become_empty_string(self, make_device: Any) -> None:
        device = make_device(
            raw_extra={
                "serialNumber": None,
            }
        )
        meta = catc_sync._build_metadata(device, "catc1.example.com")
        serial_entry = next((m for m in meta if m.key == "serialNumber"), None)
        assert serial_entry is not None
        assert serial_entry.value == ""

    def test_catc_source_added(self, make_device: Any) -> None:
        device = make_device()
        meta = catc_sync._build_metadata(device, "catc2.example.com")
        catc_entry = next((m for m in meta if m.key == "catc_source"), None)
        assert catc_entry is not None
        assert catc_entry.value == "catc2.example.com"

    def test_metadata_catc_source_replaced_when_in_raw(self, make_device: Any) -> None:
        """Test that catc_source in raw is replaced by the cluster hostname."""
        original_fields = catc_sync.METADATA_FIELDS.copy()
        catc_sync.METADATA_FIELDS = original_fields | {"catc_source"}
        device = make_device(raw_extra={"catc_source": "old-cluster"})
        meta = catc_sync._build_metadata(device, "new-cluster")
        catc_sync.METADATA_FIELDS = original_fields  # restore
        sources = [m for m in meta if m.key == "catc_source"]
        assert len(sources) == 1
        assert sources[0].value == "new-cluster"


# ---------------------------------------------------------------------------
# build_new_device
# ---------------------------------------------------------------------------


class TestBuildNewDevice:
    def test_fields_populated(self, make_device: Any) -> None:
        device = make_device("sw1.example.com", "10.1.1.1")
        new_dev = catc_sync.build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw1",
        )
        assert new_dev.name == "sw1"
        assert new_dev.host == "10.1.1.1"
        assert new_dev.device_type == DeviceType.IOS_XE
        assert "Imported from CatC:" in new_dev.description
        assert new_dev.enabled is True

    def test_terminal_ssh(self, make_device: Any) -> None:
        device = make_device("sw2.example.com", "10.1.1.2")
        new_dev = catc_sync.build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw2",
        )
        assert new_dev.terminal is not None
        assert new_dev.terminal.connection_method == ConnectionMethod.SSH
        assert new_dev.terminal.port == 22


# ---------------------------------------------------------------------------
# build_update_device
# ---------------------------------------------------------------------------


class TestBuildUpdateDevice:
    def test_no_password_update(self, make_device: Any) -> None:
        device = make_device("r1.example.com", "10.2.2.1")
        upd = catc_sync.build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
        )
        # When update_passwords=False, terminal field is not included in kwargs,
        # so it defaults to DontUpdate sentinel (not None)
        assert isinstance(upd.terminal, DontUpdate)

    def test_with_password_update(self, make_device: Any) -> None:
        device = make_device("r2.example.com", "10.2.2.2")
        upd = catc_sync.build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=True,
            ssh_user="netops",
            ssh_password="newsecret",  # noqa: S106
        )
        assert upd.terminal is not None
        assert upd.terminal.username == "netops"
