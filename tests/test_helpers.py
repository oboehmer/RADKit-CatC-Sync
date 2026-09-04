"""Tests for pure helper functions: name normalisation, get_device_type."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from radkit_common.types import DeviceType

from radkit_catc_sync.apiutils import require_api_result_ok
from radkit_catc_sync.builders import get_device_type
from radkit_catc_sync.naming import NameMode, NameNormaliser, sanitise_name

# ---------------------------------------------------------------------------
# NameNormaliser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_name, expected",
    [
        ("router1.dc1.example.com", "router1-dc1-example-com"),
        ("router1", "router1"),
        ("ROUTER1.example.com", "router1-example-com"),
        ("sw--core.example.com", "sw-core-example-com"),
        ("SW--CORE1.dc.example.com", "sw-core1-dc-example-com"),
        ("-r1-.example.com-", "r1-example-com"),
        ("r1_a.example.com", "r1-a-example-com"),
    ],
)
def test_normalise_fqdn_mode(input_name: str, expected: str) -> None:
    """Default mode preserves the whole FQDN."""
    assert NameNormaliser()(input_name) == expected


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
def test_normalise_short_mode(input_name: str, expected: str) -> None:
    """Short mode keeps only the first label."""
    assert NameNormaliser(mode=NameMode.SHORT)(input_name) == expected


@pytest.mark.parametrize(
    "input_name, strip, expected",
    [
        # Longest suffix wins.
        ("r1.dc1.example.com", (".example.com", ".dc1.example.com"), "r1"),
        ("r1.dc1.example.com", (".example.com",), "r1-dc1"),
        # Leading dot is optional in config.
        ("r1.dc1.example.com", ("example.com",), "r1-dc1"),
        # Case-insensitive.
        ("R1.DC1.EXAMPLE.COM", (".example.com",), "r1-dc1"),
        # No match leaves the FQDN intact.
        ("r1.partner.net", (".example.com",), "r1-partner-net"),
        # Suffix must be on a label boundary.
        ("r1.notexample.com", (".example.com",), "r1-notexample-com"),
    ],
)
def test_normalise_strip_domains(input_name: str, strip: tuple[str, ...], expected: str) -> None:
    assert NameNormaliser(strip_domains=strip)(input_name) == expected


def test_normalise_returns_empty_for_unnameable_hostname() -> None:
    """A hostname with no usable characters yields an empty name, not a crash."""
    assert NameNormaliser()("...") == ""
    assert NameNormaliser(mode=NameMode.SHORT)("___") == ""


def test_sanitise_name_enforces_radkit_rules() -> None:
    assert sanitise_name("Foo_Bar!!Baz") == "foo-bar-baz"
    assert sanitise_name("--x--") == "x"


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


def test_strip_domains_is_case_insensitive() -> None:
    """A suffix configured in upper case must still match a mixed-case host."""
    normalise = NameNormaliser(mode=NameMode.FQDN, strip_domains=("EXAMPLE.COM",))

    assert normalise("router1.dc1.Example.Com") == "router1-dc1"


def test_strip_domains_longest_match_wins() -> None:
    """Overlapping suffixes resolve to the most specific one."""
    normalise = NameNormaliser(
        mode=NameMode.FQDN, strip_domains=(".example.com", ".dc1.example.com")
    )

    assert normalise("router1.dc1.example.com") == "router1"


def test_strip_domains_leading_dot_is_optional() -> None:
    normalise = NameNormaliser(mode=NameMode.FQDN, strip_domains=("example.com",))

    assert normalise("router1.dc1.example.com") == "router1-dc1"


def test_strip_domains_is_inert_under_short_mode() -> None:
    """Documented no-op: short mode already discards everything but label one."""
    with_strip = NameNormaliser(mode=NameMode.SHORT, strip_domains=(".example.com",))
    without_strip = NameNormaliser(mode=NameMode.SHORT)

    assert with_strip("router1.dc1.example.com") == without_strip("router1.dc1.example.com")
