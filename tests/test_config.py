"""Tests for load_config() and load_env_vars()."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from radkit_catc_sync.config import AppConfig, load_config, load_env_vars


class TestLoadConfig:
    def test_loads_catc_clusters(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[catc]\nclusters = ["https://catc1.example.com"]\n')
        config = load_config(toml)
        assert config.catc_clusters == ["https://catc1.example.com"]

    def test_loads_verify_tls_false(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[catc]\nverify_tls = false\n")
        config = load_config(toml)
        assert config.catc_verify_tls is False

    def test_loads_radkit_base_url(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[radkit]\nbase_url = "https://radkit:9090/api/v1"\n')
        config = load_config(toml)
        assert config.radkit_base_url == "https://radkit:9090/api/v1"

    def test_loads_filters(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[filters]\nwhitelist = ["^router"]\nblacklist = ["\\\\.lab\\\\."]\n')
        config = load_config(toml)
        assert config.device_whitelist == ["^router"]
        assert len(config.device_blacklist) == 1

    def test_loads_metadata_settings(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text(
            '[metadata]\nsource_key = "my_source"\nfields = ["hostname", "serialNumber"]\n'
        )
        config = load_config(toml)
        assert config.meta_source_key == "my_source"
        assert frozenset(["hostname", "serialNumber"]) == config.metadata_fields

    def test_loads_sync_options(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[sync]\nadopt_existing = true\n")
        config = load_config(toml)
        assert config.adopt_existing is True

    def test_batch_size_defaults_to_500(self) -> None:
        assert AppConfig().batch_size == 500

    def test_loads_batch_size(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[sync]\nbatch_size = 250\n")
        config = load_config(toml)
        assert config.batch_size == 250

    # ------------------------------------------------------------------
    # [sync.naming]
    # ------------------------------------------------------------------

    def test_naming_defaults_to_fqdn(self) -> None:
        assert AppConfig().name_mode == "fqdn"
        assert AppConfig().name_strip_domains == []

    def test_loads_naming_options(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text(
            "[sync]\nbatch_size = 250\n\n"
            '[sync.naming]\nmode = "short"\nstrip_domains = [".example.com"]\n'
        )
        config = load_config(toml)
        assert config.name_mode == "short"
        assert config.name_strip_domains == [".example.com"]
        assert config.batch_size == 250  # sibling [sync] keys still parsed

    def test_invalid_naming_mode_raises(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[sync.naming]\nmode = "hostname"\n')
        with pytest.raises(ValueError, match="Invalid \\[sync.naming\\] mode"):
            load_config(toml)

    def test_invalid_strip_domains_raises(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[sync.naming]\nstrip_domains = "example.com"\n')
        with pytest.raises(ValueError, match="strip_domains"):
            load_config(toml)

    def test_loads_usernames_from_config(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text(
            '[catc]\nuser = "admin"\n\n[radkit]\nadmin_user = "radmin"\nssh_user = "netops"\n'
        )
        config = load_config(toml)
        assert config.catc_user == "admin"
        assert config.radkit_admin_user == "radmin"
        assert config.radkit_ssh_user == "netops"

    def test_missing_explicit_path_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.toml"
        with pytest.raises(FileNotFoundError):
            load_config(missing)

    def test_none_path_no_default_file_returns_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no config file exists, load_config(None) returns defaults."""
        monkeypatch.chdir(tmp_path)
        config = load_config(None)
        assert config.catc_clusters == []
        assert config.catc_verify_tls is True


class TestLoadEnvVars:
    def test_all_vars_present(self) -> None:
        config = AppConfig()
        env = {
            "CATC_USER": "admin",
            "CATC_PASSWORD": "pass1",
            "RADKIT_ADMIN_USER": "radmin",
            "RADKIT_ADMIN_PASSWORD": "pass2",
            "RADKIT_SSH_USER": "netops",
            "RADKIT_SSH_PASSWORD": "pass3",
        }
        with patch.dict(os.environ, env, clear=False):
            result = load_env_vars(config)
        assert result["CATC_USER"] == "admin"
        assert result["RADKIT_SSH_PASSWORD"] == "pass3"

    def test_missing_vars_raises_system_exit(self) -> None:
        """Missing required env vars raises SystemExit."""
        config = AppConfig()
        env_clear = {
            "CATC_USER": "",
            "CATC_PASSWORD": "",
            "RADKIT_ADMIN_USER": "",
            "RADKIT_ADMIN_PASSWORD": "",
            "RADKIT_SSH_USER": "",
            "RADKIT_SSH_PASSWORD": "",
        }
        with patch.dict(os.environ, env_clear, clear=False), pytest.raises(SystemExit):
            load_env_vars(config)

    def test_fallback_from_config(self) -> None:
        """Config-provided usernames are used as fallback."""
        config = AppConfig(catc_user="fallback-admin")
        env = {
            "CATC_PASSWORD": "pass1",
            "RADKIT_ADMIN_USER": "radmin",
            "RADKIT_ADMIN_PASSWORD": "pass2",
            "RADKIT_SSH_USER": "netops",
            "RADKIT_SSH_PASSWORD": "pass3",
        }
        # Remove CATC_USER from env so fallback is used
        env_filtered = {k: v for k, v in os.environ.items() if k != "CATC_USER"}
        env_filtered.update(env)
        with patch.dict(os.environ, env_filtered, clear=True):
            result = load_env_vars(config)
        assert result["CATC_USER"] == "fallback-admin"
