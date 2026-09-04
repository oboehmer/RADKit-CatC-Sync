"""Tests for RADKit connection/credential failures surfacing as clean errors.

A bad admin credential or an unreachable service used to escape run_sync as a
raw client exception and reach the user as a traceback. These failures happen
before anything is applied, so they are wrapped in RadkitInventoryError and
reported by the CLI like any other abort.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from radkit_service.control_api import APIError, AuthenticationError

from radkit_catc_sync.config import AppConfig
from radkit_catc_sync.sync import RadkitInventoryError, run_sync

BASE_URL = "https://radkit.example.com/"


def _run(exc: BaseException) -> RadkitInventoryError:
    """Run a sync whose RADKit inventory fetch raises, return the wrapper."""
    config = AppConfig(
        catc_clusters=["https://catc1.example.com"],
        radkit_base_url=BASE_URL,
    )
    with (
        patch("radkit_catc_sync.sync.ControlAPI") as mock_cls,
        patch("radkit_catc_sync.sync.fetch_radkit_devices", side_effect=exc),
        patch("radkit_catc_sync.sync.fetch_fresh_inventory", return_value={}),
    ):
        api = MagicMock()
        api.__enter__ = MagicMock(return_value=api)
        api.__exit__ = MagicMock(return_value=False)
        mock_cls.create.return_value = api

        with pytest.raises(RadkitInventoryError) as excinfo:
            run_sync(
                config=config,
                dry_run=False,
                update_credentials=False,
                catc_user="u",
                catc_password="p",
                radkit_admin_user="admin",
                radkit_admin_password="wrong",
                ssh_user="s",
                ssh_password="p",
            )
    return excinfo.value


def test_bad_credentials_name_the_env_vars_to_fix() -> None:
    """AuthenticationError's own message is just '400' — we supply the context."""
    error = _run(AuthenticationError("400 "))

    assert "rejected the admin credentials" in str(error)
    assert "RADKIT_ADMIN_USER" in str(error)
    assert "RADKIT_ADMIN_PASSWORD" in str(error)
    assert BASE_URL in str(error)


def test_unreachable_service_points_at_the_url() -> None:
    error = _run(ConnectionError("Failed to establish a connection."))

    assert "Could not reach the RADKit service" in str(error)
    assert BASE_URL in str(error)


def test_api_error_is_wrapped_too() -> None:
    error = _run(APIError("boom"))

    assert "RADKit API error" in str(error)
    assert BASE_URL in str(error)


def test_original_exception_is_preserved_as_cause() -> None:
    """Wrapping must not hide the underlying error from --debug tracebacks."""
    original = AuthenticationError("400 ")
    error = _run(original)

    assert error.__cause__ is original


def test_unexpected_errors_are_not_swallowed() -> None:
    """Only the known client failures are translated; bugs still surface."""
    config = AppConfig(catc_clusters=["https://catc1.example.com"])
    with (
        patch("radkit_catc_sync.sync.ControlAPI") as mock_cls,
        patch("radkit_catc_sync.sync.fetch_radkit_devices", side_effect=KeyError("bug")),
        patch("radkit_catc_sync.sync.fetch_fresh_inventory", return_value={}),
    ):
        api: Any = MagicMock()
        api.__enter__ = MagicMock(return_value=api)
        api.__exit__ = MagicMock(return_value=False)
        mock_cls.create.return_value = api

        with pytest.raises(KeyError):
            run_sync(
                config=config,
                dry_run=False,
                update_credentials=False,
                catc_user="u",
                catc_password="p",
                radkit_admin_user="a",
                radkit_admin_password="p",
                ssh_user="s",
                ssh_password="p",
            )
