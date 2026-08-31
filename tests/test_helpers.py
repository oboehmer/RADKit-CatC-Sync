"""Tests for pure helper functions: normalise_name, _get_device_type, _format_name_list."""

from __future__ import annotations

import pytest
from radkit_common.types import DeviceType

import catc_sync

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
    assert catc_sync.normalise_name(input_name) == expected


# ---------------------------------------------------------------------------
# _get_device_type
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
    assert catc_sync._get_device_type(software_type, series) == expected


# ---------------------------------------------------------------------------
# _format_name_list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "names, max_names, expected_contains, not_contains",
    [
        (["zebra", "alpha", "charlie"], 10, "alpha, charlie, zebra", None),
        ([f"device{i}" for i in range(10)], 10, "device0", "... and"),
        ([f"device{i}" for i in range(15)], 10, "... and 5 more", None),
        (["a", "b", "c", "d", "e"], 2, "a, b, ... and 3 more", None),
    ],
)
def test_format_name_list(
    names: list[str], max_names: int, expected_contains: str, not_contains: str | None
) -> None:
    result = catc_sync._format_name_list(names, max_names)
    assert expected_contains in result
    if not_contains:
        assert not_contains not in result


# ---------------------------------------------------------------------------
# _require_api_result_ok
# ---------------------------------------------------------------------------


def test_require_api_result_ok_raises_on_error() -> None:
    """Test _require_api_result_ok raises RuntimeError when APIResult.is_error() returns True."""
    from unittest.mock import MagicMock, patch

    mock_result = MagicMock()
    mock_result.root.message = "Something went wrong"
    mock_result.root.detail = "Detailed error information"

    with (
        patch("catc_sync.APIResult.is_error", return_value=True),
        pytest.raises(RuntimeError, match="Something went wrong"),
    ):
        catc_sync._require_api_result_ok(mock_result, "test_action")
