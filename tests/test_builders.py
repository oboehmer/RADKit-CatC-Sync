"""Tests for metadata and device builders."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from radkit_common.types import ConnectionMethod, DeviceType
from radkit_service.webserver.models.base import DontUpdate as DontUpdateType

from radkit_catc_sync import AppConfig
from radkit_catc_sync.builders import build_metadata, build_new_device, build_update_device

# ---------------------------------------------------------------------------
# build_metadata
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
        config = AppConfig()
        meta = build_metadata(
            device, "catc1.example.com", config.metadata_fields, config.meta_source_key
        )
        keys = {m.key for m in meta}
        assert "serialNumber" in keys
        assert "platformId" in keys
        assert "extraField" not in keys
        assert config.meta_source_key in keys

    def test_none_values_become_empty_string(self, make_device: Any) -> None:
        device = make_device(
            raw_extra={
                "serialNumber": None,
            }
        )
        config = AppConfig()
        meta = build_metadata(
            device, "catc1.example.com", config.metadata_fields, config.meta_source_key
        )
        serial_entry = next((m for m in meta if m.key == "serialNumber"), None)
        assert serial_entry is not None
        assert serial_entry.value == ""

    def test_catc_source_added(self, make_device: Any) -> None:
        device = make_device()
        config = AppConfig()
        meta = build_metadata(
            device, "catc2.example.com", config.metadata_fields, config.meta_source_key
        )
        catc_entry = next((m for m in meta if m.key == config.meta_source_key), None)
        assert catc_entry is not None
        assert catc_entry.value == "catc2.example.com"

    def test_metadata_catc_source_replaced_when_in_raw(self, make_device: Any) -> None:
        """Test that catc_source in raw is replaced by the cluster hostname."""
        config = AppConfig()
        device = make_device(raw_extra={"catc_source": "old-cluster"})
        meta = build_metadata(device, "new-cluster", config.metadata_fields, config.meta_source_key)
        sources = [m for m in meta if m.key == config.meta_source_key]
        assert len(sources) == 1
        assert sources[0].value == "new-cluster"


# ---------------------------------------------------------------------------
# build_new_device
# ---------------------------------------------------------------------------


class TestBuildNewDevice:
    def test_fields_populated(self, make_device: Any) -> None:
        device = make_device("sw1.example.com", "10.1.1.1")
        config = AppConfig()
        new_dev = build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw1",
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
        )
        assert new_dev.name == "sw1"
        assert new_dev.host == "10.1.1.1"
        assert new_dev.device_type == DeviceType.IOS_XE
        assert "Imported from CatC:" in new_dev.description
        assert new_dev.enabled is True

    def test_terminal_ssh(self, make_device: Any) -> None:
        device = make_device("sw2.example.com", "10.1.1.2")
        config = AppConfig()
        new_dev = build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw2",
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
        )
        assert new_dev.terminal is not None
        assert new_dev.terminal.connection_method == ConnectionMethod.SSH
        assert new_dev.terminal.port == 22


# ---------------------------------------------------------------------------
# build_update_device
# ---------------------------------------------------------------------------


class TestBuildUpdateDevice:
    def test_fields_populated_without_pw(self, make_device: Any) -> None:
        device = make_device("r1.example.com", "10.0.0.1")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_credentials=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
        )
        assert upd.host == "10.0.0.1"
        # terminal should not be set when update_credentials=False
        assert isinstance(upd.terminal, DontUpdateType)

    def test_includes_password_when_flag_true(self, make_device: Any) -> None:
        device = make_device("r2.example.com", "10.0.0.2")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_credentials=True,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
        )
        assert upd.terminal is not None
        assert upd.terminal.username == "netops"
