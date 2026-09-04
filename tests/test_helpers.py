"""Tests for pure helper functions: normalise_name, get_device_type."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from radkit_common.types import DeviceType

from radkit_catc_sync.apiutils import require_api_result_ok
from radkit_catc_sync.builders import get_device_type
from radkit_catc_sync.sync import normalise_name

# ---------------------------------------------------------------------------
# normalise_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_name, expected",
    [
        ("router1.dc1.example.com", "router1"),
        ("router1", "router1"),
        ("ROUTER1.example.com", "router1"),
        ("sw--core.example.com", "sw-core"),
        ("SW--CORE1.dc.example.com", "sw-core1"),
    ],
)
def test_normalise_name(input_name: str, expected: str) -> None:
    assert normalise_name(input_name) == expected


# ---------------------------------------------------------------------------
# get_device_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "software_type, series, expected",
    [
        ("IOS-XE", None, DeviceType.IOS_XE),
        ("IOS", None, DeviceType.IOS_XE),
        ("NXOS", None, DeviceType.NX_OS),
        ("IOS-XE", "Cisco Catalyst 9800 Series Wireless Controllers", DeviceType.WLC),
        ("FXOS", None, DeviceType.GENERIC),
        (None, None, DeviceType.GENERIC),
    ],
)
def test_get_device_type(
    software_type: str | None, series: str | None, expected: DeviceType
) -> None:
    assert get_device_type(software_type, series) == expected


# ---------------------------------------------------------------------------
# require_api_result_ok
# ---------------------------------------------------------------------------


def test_require_api_result_ok_raises_on_error() -> None:
    """Test require_api_result_ok raises RuntimeError when APIResult.is_error() returns True."""
    mock_result = MagicMock()
    mock_result.root.message = "Something went wrong"
    mock_result.root.detail = "Detailed error information"

    with (
        patch("radkit_catc_sync.apiutils.APIResult.is_error", return_value=True),
        pytest.raises(RuntimeError, match="Something went wrong"),
    ):
        require_api_result_ok(mock_result, "test_action")
