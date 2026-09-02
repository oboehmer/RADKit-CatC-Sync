"""Tests for device label functionality."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from radkit_catc_sync import AppConfig
from radkit_catc_sync.builders import build_new_device, build_update_device
from radkit_catc_sync.config import load_config
from radkit_catc_sync.models import StoredRadkitDevice
from radkit_catc_sync.sync import ensure_labels_exist
from radkit_service.webserver.models.base import APIResult, Ok
from radkit_service.webserver.models.labels import StoredLabel


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestLoadConfigLabels:
    """Test label configuration loading from TOML."""

    def test_loads_labels_from_config(self, tmp_path: Path) -> None:
        """Test loading label names from [labels] section."""
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[labels]\nnames = ["catc-managed", "production"]\n')
        config = load_config(toml)
        assert config.device_labels == ["catc-managed", "production"]

    def test_labels_default_empty(self, tmp_path: Path) -> None:
        """Test that labels default to empty list if section missing."""
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[sync]\nadopt_existing = false\n")
        config = load_config(toml)
        assert config.device_labels == []

    def test_labels_empty_list_when_names_empty(self, tmp_path: Path) -> None:
        """Test that empty names list is preserved."""
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[labels]\nnames = []\n")
        config = load_config(toml)
        assert config.device_labels == []

    def test_labels_single_name(self, tmp_path: Path) -> None:
        """Test loading a single label."""
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[labels]\nnames = ["sr12345"]\n')
        config = load_config(toml)
        assert config.device_labels == ["sr12345"]

    def test_labels_multiple_names(self, tmp_path: Path) -> None:
        """Test loading multiple labels."""
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[labels]\nnames = ["label1", "label2", "label3"]\n')
        config = load_config(toml)
        assert config.device_labels == ["label1", "label2", "label3"]


# ---------------------------------------------------------------------------
# Builder tests with labels
# ---------------------------------------------------------------------------


class TestBuildNewDeviceWithLabels:
    """Test build_new_device with label support."""

    def test_new_device_with_labels(self, make_device: Any) -> None:
        """Test that NewDevice receives configured labels."""
        device = make_device("sw1.example.com", "10.1.1.1")
        config = AppConfig(device_labels=["catc-managed", "production"])
        new_dev = build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw1",
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            device_labels=config.device_labels,
        )
        assert new_dev.labels == ["catc-managed", "production"]

    def test_new_device_without_labels(self, make_device: Any) -> None:
        """Test that NewDevice works without labels (backward compat)."""
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
        assert new_dev.labels == []

    def test_new_device_with_none_labels(self, make_device: Any) -> None:
        """Test that None labels defaults to empty list."""
        device = make_device("sw3.example.com", "10.1.1.3")
        config = AppConfig()
        new_dev = build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw3",
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            device_labels=None,
        )
        assert new_dev.labels == []

    def test_new_device_with_single_label(self, make_device: Any) -> None:
        """Test NewDevice with a single label."""
        device = make_device("sw4.example.com", "10.1.1.4")
        config = AppConfig()
        new_dev = build_new_device(
            device=device,
            catc_hostname="catc1.example.com",
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            radkit_name="sw4",
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            device_labels=["sr12345"],
        )
        assert new_dev.labels == ["sr12345"]


class TestBuildUpdateDeviceWithLabels:
    """Test build_update_device with label support."""

    def test_update_device_with_labels_to_add(self, make_device: Any) -> None:
        """Test that UpdateDevice receives labels_to_add."""
        device = make_device("r1.example.com", "10.0.0.1")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            labels_to_add=["production", "sr12345"],
        )
        assert list(upd.label_update.add) == ["production", "sr12345"]
        assert upd.label_update.replace is None
        assert not upd.label_update.is_empty()

    def test_update_device_without_labels(self, make_device: Any) -> None:
        """Test that UpdateDevice without labels has empty label_update."""
        device = make_device("r2.example.com", "10.0.0.2")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
        )
        assert upd.label_update.is_empty()

    def test_update_device_with_none_labels(self, make_device: Any) -> None:
        """Test that None labels_to_add results in empty label_update."""
        device = make_device("r3.example.com", "10.0.0.3")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            labels_to_add=None,
        )
        assert upd.label_update.is_empty()

    def test_update_device_with_single_label_to_add(self, make_device: Any) -> None:
        """Test UpdateDevice with single label."""
        device = make_device("r4.example.com", "10.0.0.4")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            labels_to_add=["catc-managed"],
        )
        assert list(upd.label_update.add) == ["catc-managed"]
        assert not upd.label_update.is_empty()

    def test_update_device_labels_never_remove(self, make_device: Any) -> None:
        """Test that label_update uses .add (never .remove or .replace)."""
        device = make_device("r5.example.com", "10.0.0.5")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc1.example.com",
            existing_uuid=str(uuid4()),
            update_passwords=False,
            ssh_user="netops",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            labels_to_add=["new-label"],
        )
        # Verify only .add is set, not .remove or .replace
        assert upd.label_update.add == ["new-label"]
        assert upd.label_update.remove == []
        assert upd.label_update.replace is None


# ---------------------------------------------------------------------------
# ensure_labels_exist tests
# ---------------------------------------------------------------------------


class TestEnsureLabelsExist:
    """Test ensure_labels_exist label management function."""

    def test_no_labels_configured_returns_empty_dict(self) -> None:
        """Test that empty config returns empty dict."""
        mock_api = MagicMock()
        config = AppConfig(device_labels=[])
        result = ensure_labels_exist(mock_api, config, dry_run=False)
        assert result == {}
        # API should not be called
        mock_api.list_labels.assert_not_called()
        mock_api.create_labels.assert_not_called()

    def test_all_labels_exist(self) -> None:
        """Test when all configured labels already exist in RADKit."""
        mock_api = MagicMock()
        config = AppConfig(device_labels=["catc-managed", "production"])

        # Mock list_labels to return existing labels
        existing_labels = [
            StoredLabel(id=10, name="catc-managed", color="#ff0000", is_context=False),
            StoredLabel(id=11, name="production", color="#00ff00", is_context=False),
        ]
        mock_api.list_labels.return_value = APIResult(  # type: ignore[call-arg]
            Ok(success=True, result=existing_labels)
        )

        result = ensure_labels_exist(mock_api, config, dry_run=False)

        # All labels found
        assert result == {"catc-managed": 10, "production": 11}
        # create_labels should not be called
        mock_api.create_labels.assert_not_called()

    def test_creates_missing_labels(self) -> None:
        """Test that missing labels are auto-created."""
        mock_api = MagicMock()
        config = AppConfig(device_labels=["catc-managed", "production", "sr12345"])

        # Mock list_labels: only catc-managed exists
        existing_labels = [
            StoredLabel(id=10, name="catc-managed", color="#ff0000", is_context=False),
        ]
        mock_api.list_labels.return_value = APIResult(  # type: ignore[call-arg]
            Ok(success=True, result=existing_labels)
        )

        # Mock create_labels to return newly created labels
        created_labels = [
            StoredLabel(id=20, name="production", color="#000000", is_context=False),
            StoredLabel(id=21, name="sr12345", color="#000000", is_context=False),
        ]
        mock_bulk_result = MagicMock()
        mock_bulk_result.successful_results.return_value = iter(created_labels)
        mock_api.create_labels.return_value = mock_bulk_result

        result = ensure_labels_exist(mock_api, config, dry_run=False)

        # All labels mapped (existing + created)
        assert result == {"catc-managed": 10, "production": 20, "sr12345": 21}
        # Verify create_labels was called with correct labels
        mock_api.create_labels.assert_called_once()
        call_args = mock_api.create_labels.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0].name == "production"
        assert call_args[1].name == "sr12345"

    def test_dry_run_does_not_create_labels(self) -> None:
        """Test that dry-run mode logs intent but doesn't create labels."""
        mock_api = MagicMock()
        config = AppConfig(device_labels=["new-label"])

        # Mock list_labels: no labels exist
        mock_api.list_labels.return_value = APIResult(  # type: ignore[call-arg]
            Ok(success=True, result=[])
        )

        with patch("radkit_catc_sync.sync.logger") as mock_logger:
            result = ensure_labels_exist(mock_api, config, dry_run=True)

        # Empty dict returned (labels don't exist yet)
        assert result == {}
        # create_labels should not be called in dry-run
        mock_api.create_labels.assert_not_called()
        # But should log what would be created
        mock_logger.info.assert_called()

    def test_created_labels_have_default_color(self) -> None:
        """Test that newly created labels use default color #000000."""
        mock_api = MagicMock()
        config = AppConfig(device_labels=["new-label"])

        mock_api.list_labels.return_value = APIResult(  # type: ignore[call-arg]
            Ok(success=True, result=[])
        )

        created_labels = [
            StoredLabel(id=1, name="new-label", color="#000000", is_context=False),
        ]
        mock_bulk_result = MagicMock()
        mock_bulk_result.successful_results.return_value = iter(created_labels)
        mock_api.create_labels.return_value = mock_bulk_result

        ensure_labels_exist(mock_api, config, dry_run=False)

        # Verify create_labels was called with correct color
        call_args = mock_api.create_labels.call_args[0][0]
        assert call_args[0].color == "#000000"

    def test_empty_list_labels_response(self) -> None:
        """Test handling of empty list from list_labels."""
        mock_api = MagicMock()
        config = AppConfig(device_labels=["label1"])

        # list_labels returns empty list
        mock_api.list_labels.return_value = APIResult(  # type: ignore[call-arg]
            Ok(success=True, result=[])
        )

        created_labels = [
            StoredLabel(id=1, name="label1", color="#000000", is_context=False),
        ]
        mock_bulk_result = MagicMock()
        mock_bulk_result.successful_results.return_value = iter(created_labels)
        mock_api.create_labels.return_value = mock_bulk_result

        result = ensure_labels_exist(mock_api, config, dry_run=False)

        assert result == {"label1": 1}
        mock_api.create_labels.assert_called_once()


# ---------------------------------------------------------------------------
# StoredRadkitDevice label handling
# ---------------------------------------------------------------------------


class TestStoredRadkitDeviceLabels:
    """Test StoredRadkitDevice label field."""

    def test_device_with_labels(self) -> None:
        """Test creating device with label IDs."""
        device = StoredRadkitDevice(
            name="test",
            uuid=str(uuid4()),
            host="10.0.0.1",
            device_type="LINUX",
            catc_source="catc1",
            metadata={},
            labels={1, 2, 3},
        )
        assert device.labels == {1, 2, 3}

    def test_device_without_labels(self) -> None:
        """Test creating device with empty labels."""
        device = StoredRadkitDevice(
            name="test",
            uuid=str(uuid4()),
            host="10.0.0.1",
            device_type="LINUX",
            catc_source="catc1",
            metadata={},
            labels=set(),
        )
        assert device.labels == set()

    def test_device_labels_default_to_empty(self) -> None:
        """Test that labels default to empty set."""
        device = StoredRadkitDevice(
            name="test",
            uuid=str(uuid4()),
            host="10.0.0.1",
            device_type="LINUX",
            catc_source="catc1",
            metadata={},
        )
        assert device.labels == set()

    def test_label_computation_logic(self) -> None:
        """Test the label missing computation logic."""
        # Simulate label resolution
        label_config_ids = {"catc-managed": 1, "production": 2, "sr12345": 3}
        configured_label_ids = {label_config_ids[name] for name in label_config_ids.keys()}

        # Device has only ID 1
        device = StoredRadkitDevice(
            name="test",
            uuid=str(uuid4()),
            host="10.0.0.1",
            device_type="LINUX",
            catc_source="catc1",
            metadata={},
            labels={1},
        )

        # Compute missing
        missing_label_ids = configured_label_ids - device.labels
        id_to_name = {v: k for k, v in label_config_ids.items()}
        labels_to_add = sorted([id_to_name[lid] for lid in missing_label_ids])

        assert set(labels_to_add) == {"production", "sr12345"}

    def test_device_with_extra_labels_not_configured(self) -> None:
        """Test device that has labels not in configuration."""
        label_config_ids = {"catc-managed": 1}
        configured_label_ids = {label_config_ids["catc-managed"]}

        # Device has configured label + extra labels
        device = StoredRadkitDevice(
            name="test",
            uuid=str(uuid4()),
            host="10.0.0.1",
            device_type="LINUX",
            catc_source="catc1",
            metadata={},
            labels={1, 99, 100},  # 1=catc-managed, 99 & 100 are extra
        )

        # Compute missing
        missing_label_ids = configured_label_ids - device.labels

        # No missing labels (1 is present)
        assert missing_label_ids == set()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestLabelsIntegration:
    """Integration tests for label feature."""

    def test_label_backfill_logic(self, make_device: Any) -> None:
        """Test the full label backfill logic for existing devices."""
        # Setup
        label_config_ids = {
            "catc-managed": 10,
            "production": 11,
            "sr12345": 12,
        }

        # Scenario: Device has only catc-managed label
        existing_device = StoredRadkitDevice(
            name="router-01",
            uuid=str(uuid4()),
            host="10.10.10.5",
            device_type="IOS_XE",
            catc_source="catc.example.com",
            metadata={"catc_source": "catc.example.com"},
            labels={10},  # Only has catc-managed
        )

        # Compute what needs to be added
        configured_label_ids = set(label_config_ids.values())
        missing_label_ids = configured_label_ids - existing_device.labels
        id_to_name = {v: k for k, v in label_config_ids.items()}
        labels_to_add = [id_to_name[lid] for lid in missing_label_ids]

        # Assertions
        assert set(labels_to_add) == {"production", "sr12345"}

        # Verify these would be added via UpdateDevice
        device = make_device("router-01.example.com", "10.10.10.5")
        config = AppConfig()
        upd = build_update_device(
            device=device,
            catc_hostname="catc.example.com",
            existing_uuid=existing_device.uuid,
            update_passwords=False,
            ssh_user="admin",
            ssh_password="secret",  # noqa: S106
            metadata_fields=config.metadata_fields,
            meta_source_key=config.meta_source_key,
            labels_to_add=labels_to_add,
        )

        assert set(upd.label_update.add) == {"production", "sr12345"}

    def test_no_backfill_when_all_labels_present(self) -> None:
        """Test that no update is needed when all labels already present."""
        label_config_ids = {
            "catc-managed": 10,
            "production": 11,
        }

        # Device has all configured labels
        device = StoredRadkitDevice(
            name="router-02",
            uuid=str(uuid4()),
            host="10.10.10.6",
            device_type="IOS_XE",
            catc_source="catc.example.com",
            metadata={},
            labels={10, 11},
        )

        # Compute missing
        configured_label_ids = set(label_config_ids.values())
        missing_label_ids = configured_label_ids - device.labels

        # No labels to add
        assert missing_label_ids == set()
