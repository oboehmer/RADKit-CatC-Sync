"""Device name filtering logic."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FilterSet:
    """Compiled regex filter patterns for device name matching."""

    whitelist_patterns: list[re.Pattern[str]]
    blacklist_patterns: list[re.Pattern[str]]

    @classmethod
    def from_lists(cls, whitelist: list[str], blacklist: list[str]) -> FilterSet:
        """
        Create a FilterSet from pattern lists.

        Args:
            whitelist: Regex patterns to include devices (empty = include all).
            blacklist: Regex patterns to exclude devices (always takes precedence).

        Returns:
            FilterSet with compiled patterns.
        """
        whitelist_re = [re.compile(p, re.IGNORECASE) for p in whitelist]
        blacklist_re = [re.compile(p, re.IGNORECASE) for p in blacklist]
        return cls(whitelist_patterns=whitelist_re, blacklist_patterns=blacklist_re)

    def should_import(self, hostname: str) -> bool:
        """
        Return True if hostname passes whitelist/blacklist filters.

        Matching uses re.search with re.IGNORECASE — patterns match anywhere in
        the string case-insensitively. Use ^ / $ anchors to restrict to start/end.
        Blacklist always takes precedence over whitelist.

        Args:
            hostname: The device hostname to check.

        Returns:
            True if device should be imported, False if filtered out.
        """
        # Blacklist takes precedence
        for pat in self.blacklist_patterns:
            if pat.search(hostname):
                return False

        # If whitelist is non-empty, must match at least one pattern
        if self.whitelist_patterns:
            return any(pat.search(hostname) for pat in self.whitelist_patterns)

        # No whitelist = include by default
        return True
