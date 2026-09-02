"""Data models for radkit-catc-sync."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CatCDevice:
    """
    Lightweight representation of a Catalyst Center network device.
    Parsed directly from the /api/v1/network-device JSON response.
    """

    hostname: str | None
    management_ip: str
    software_type: str | None
    series: str | None
    raw: dict[str, Any]  # full response payload, used for RADKit metadata

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatCDevice:
        """Create a CatCDevice from a Catalyst Center API response dict."""
        return cls(
            hostname=data.get("hostname"),
            management_ip=data["managementIpAddress"],
            software_type=data.get("softwareType"),
            series=data.get("series"),
            raw=data,
        )


@dataclass
class StoredRadkitDevice:
    """Represents a device stored in RADKit."""

    name: str
    uuid: str
    host: str
    device_type: str
    catc_source: str  # ownership marker (source CatC cluster)
    metadata: dict[str, str]
