"""Configurable CatC hostname → RADKit device name conversion.

The conversion runs in two stages:

1. A **configurable** transform of the raw CatC hostname (domain-suffix
   stripping plus a short/FQDN mode), driven by ``[sync.naming]`` in the
   TOML config.
2. A **fixed** sanitiser that enforces RADKit's naming rules.

Stage 2 always runs last and is deliberately not configurable, so no
combination of user settings can produce an invalid RADKit device name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig

# Any character outside the RADKit-permitted set becomes a dash.
_INVALID_CHARS = re.compile(r"[^a-z0-9-]")
_REPEATED_DASHES = re.compile(r"-{2,}")


class NameMode(StrEnum):
    """How much of the CatC hostname is carried into the RADKit name."""

    #: Keep the full FQDN: ``router1.dc1.example.com`` -> ``router1-dc1-example-com``
    FQDN = "fqdn"
    #: Keep only the first label: ``router1.dc1.example.com`` -> ``router1``
    SHORT = "short"


def sanitise_name(value: str) -> str:
    """
    Enforce RADKit device-name rules on an already-transformed name.

    RADKit device names must contain only lower-case letters, digits and
    dashes, must not contain consecutive dashes, and must not start or end
    with a dash.

    Args:
        value: Candidate name.

    Returns:
        Sanitised name. May be an empty string if nothing usable remains.
    """
    sanitised = _INVALID_CHARS.sub("-", value.lower())
    sanitised = _REPEATED_DASHES.sub("-", sanitised)
    return sanitised.strip("-")


@dataclass(frozen=True)
class NameNormaliser:
    """
    Converts CatC hostnames into RADKit device names.

    Args:
        mode: Whether to keep the full FQDN or only the first label.
        strip_domains: Domain suffixes removed before the mode is applied.
            Matching is case-insensitive and the longest match wins. Entries
            may be written with or without a leading dot. Has no effect under
            :attr:`NameMode.SHORT`, which already discards everything after
            the first label.
    """

    mode: NameMode = NameMode.FQDN
    strip_domains: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, config: AppConfig) -> NameNormaliser:
        """Build a normaliser from the ``[sync.naming]`` config section."""
        return cls(
            mode=NameMode(config.name_mode),
            strip_domains=tuple(config.name_strip_domains),
        )

    def _strip_domain(self, hostname: str) -> str:
        """Remove the longest configured domain suffix, if any matches."""
        best = ""
        for suffix in self.strip_domains:
            lowered = suffix.lower()
            normalised = lowered if lowered.startswith(".") else f".{lowered}"
            if hostname.endswith(normalised) and len(normalised) > len(best):
                best = normalised
        return hostname[: -len(best)] if best else hostname

    def __call__(self, hostname: str) -> str:
        """
        Normalise a CatC hostname into a RADKit device name.

        Args:
            hostname: Raw hostname or FQDN as reported by Catalyst Center.

        Returns:
            RADKit-safe device name. May be an empty string if the hostname
            contains no usable characters; callers must treat that as
            unnameable and skip the device.
        """
        name = self._strip_domain(hostname.lower())
        if self.mode is NameMode.SHORT:
            name = name.split(".")[0]
        return sanitise_name(name)
