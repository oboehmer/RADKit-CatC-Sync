"""Integration tests for catc-sync --init command."""

from __future__ import annotations

from pathlib import Path
from subprocess import run
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


class TestCliInit:
    """Test the --init flag functionality."""

    @pytest.fixture
    def isolated_dir(self, tmp_path: Path) -> Generator[Path, None, None]:
        """Create an isolated temporary directory and change to it."""
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            yield tmp_path
        finally:
            os.chdir(old_cwd)

    def test_init_creates_valid_config_file(self, isolated_dir: Path) -> None:
        """Test that --init creates a valid, loadable catc_sync.toml file."""
        result = run(["catc-sync", "--init"], capture_output=True, text=True)

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        config_file = isolated_dir / "catc_sync.toml"
        assert config_file.exists(), "catc_sync.toml was not created"

        # Verify it's valid TOML and can be loaded
        import tomllib

        with open(config_file, "rb") as f:
            config = tomllib.load(f)

        # Verify required sections exist
        assert "catc" in config
        assert "radkit" in config
        assert "filters" in config
        assert "metadata" in config
        assert "sync" in config
        assert "labels" in config

        # Verify the config can be loaded by our application
        from radkit_catc_sync.config import load_config

        loaded = load_config(config_file)
        assert loaded is not None

    def test_init_prevents_overwrite(self, isolated_dir: Path) -> None:
        """Test that --init refuses to overwrite an existing config file."""
        # Create the file first
        config_file = isolated_dir / "catc_sync.toml"
        config_file.write_text("# existing config\n")

        # Try to init again
        result = run(["catc-sync", "--init"], capture_output=True, text=True)

        assert result.returncode == 1, "Should have failed when file exists"
        assert "already exists" in result.stdout, "Error message not found in output"

    @pytest.mark.parametrize(
        "flags",
        [
            (["-c", "/tmp/config.toml"],),
            (["--config", "/tmp/config.toml"],),
            (["--dry-run"],),
            (["--update-passwords"],),
            (["-A"],),
            (["--adopt-existing"],),
            (["-k"],),
            (["--no-verify-tls"],),
        ],
    )
    def test_init_rejects_conflicting_flags(
        self, isolated_dir: Path, flags: tuple[list[str]]
    ) -> None:
        """Test that --init rejects conflicting CLI flags."""
        cmd = ["catc-sync", "--init"] + flags[0]
        result = run(cmd, capture_output=True, text=True)

        assert result.returncode == 1, f"Should reject {flags[0]}"
        assert (
            "cannot be used with" in result.stderr or "cannot be used with" in result.stdout
        ), f"Error message not found for {flags[0]}"
        assert not (isolated_dir / "catc_sync.toml").exists()
