"""Tests for load_config() and load_env_vars()."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import catc_sync


class TestLoadConfig:
    def test_loads_catc_clusters(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[catc]\nclusters = ["https://catc1.example.com"]\n')
        catc_sync.load_config(toml)
        assert catc_sync.CATC_CLUSTERS == ["https://catc1.example.com"]

    def test_loads_verify_tls_false(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[catc]\nverify_tls = false\n")
        catc_sync.load_config(toml)
        assert catc_sync.CATC_VERIFY_TLS is False

    def test_loads_radkit_base_url(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[radkit]\nbase_url = "https://radkit:9090/api/v1"\n')
        catc_sync.load_config(toml)
        assert catc_sync.RADKIT_BASE_URL == "https://radkit:9090/api/v1"

    def test_loads_filters(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text('[filters]\nwhitelist = ["^router"]\nblacklist = ["\\\\.lab\\\\."]\n')
        catc_sync.load_config(toml)
        assert catc_sync.DEVICE_WHITELIST == ["^router"]
        assert len(catc_sync.DEVICE_BLACKLIST) == 1

    def test_loads_metadata_settings(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text(
            '[metadata]\nsource_key = "my_source"\nfields = ["hostname", "serialNumber"]\n'
        )
        catc_sync.load_config(toml)
        assert catc_sync.META_SOURCE == "my_source"
        assert {"hostname", "serialNumber"} == catc_sync.METADATA_FIELDS

    def test_loads_sync_options(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text("[sync]\nadopt_existing = true\n")
        catc_sync.load_config(toml)
        assert catc_sync.ADOPT_EXISTING is True

    def test_loads_env_fallbacks(self, tmp_path: Path) -> None:
        toml = tmp_path / "catc_sync.toml"
        toml.write_text(
            '[catc]\nuser = "admin"\n\n[radkit]\nadmin_user = "radmin"\nssh_user = "netops"\n'
        )
        catc_sync.load_config(toml)
        assert catc_sync.CONFIG_ENV_FALLBACKS["CATC_USER"] == "admin"
        assert catc_sync.CONFIG_ENV_FALLBACKS["RADKIT_ADMIN_USER"] == "radmin"
        assert catc_sync.CONFIG_ENV_FALLBACKS["RADKIT_SSH_USER"] == "netops"

    def test_missing_explicit_path_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.toml"
        with pytest.raises(FileNotFoundError):
            catc_sync.load_config(missing)

    def test_none_path_no_default_file_is_noop(self, tmp_path: Path) -> None:
        # When no config file exists, load_config(None) silently returns
        with patch("catc_sync.Path") as mock_path:
            mock_path.__file__ = "fake"
            # Just call with None from a dir without catc_sync.toml
            catc_sync.load_config(None)  # should not raise


class TestLoadEnvVars:
    def test_all_vars_present(self) -> None:
        env = {
            "CATC_USER": "admin",
            "CATC_PASSWORD": "pass1",
            "RADKIT_ADMIN_USER": "radmin",
            "RADKIT_ADMIN_PASSWORD": "pass2",
            "RADKIT_SSH_USER": "netops",
            "RADKIT_SSH_PASSWORD": "pass3",
        }
        with patch.dict(os.environ, env, clear=False):
            result = catc_sync.load_env_vars()
        assert result["CATC_USER"] == "admin"
        assert result["RADKIT_SSH_PASSWORD"] == "pass3"

    def test_missing_vars_raises_system_exit(self) -> None:
        # Clear all required vars
        env_clear = {k: "" for k, _ in catc_sync._REQUIRED_ENV_VARS}
        with patch.dict(os.environ, env_clear, clear=False):
            catc_sync.CONFIG_ENV_FALLBACKS = {}
            with pytest.raises(SystemExit):
                catc_sync.load_env_vars()

    def test_fallback_from_config(self) -> None:
        catc_sync.CONFIG_ENV_FALLBACKS = {"CATC_USER": "fallback-admin"}
        env = {
            "CATC_PASSWORD": "pass1",
            "RADKIT_ADMIN_USER": "radmin",
            "RADKIT_ADMIN_PASSWORD": "pass2",
            "RADKIT_SSH_USER": "netops",
            "RADKIT_SSH_PASSWORD": "pass3",
        }
        # Remove CATC_USER from env so fallback is used
        env_with_no_catc_user = {k: v for k, v in os.environ.items() if k != "CATC_USER"}
        env_with_no_catc_user.update(env)
        with patch.dict(os.environ, env_with_no_catc_user, clear=True):
            result = catc_sync.load_env_vars()
        assert result["CATC_USER"] == "fallback-admin"
