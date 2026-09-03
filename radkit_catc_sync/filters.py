"""Device name filtering logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FilterDecision(Enum):
    """Outcome of applying whitelist/blacklist filters to a hostname."""

    PASS = "pass"
    BLACKLIST = "blacklist"
    WHITELIST_MISS = "whitelist_miss"


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

    def classify(self, hostname: str) -> FilterDecision:
        """
        Classify a hostname against the whitelist/blacklist filters.

        Matching uses re.search with re.IGNORECASE — patterns match anywhere in
        the string case-insensitively. Use ^ / $ anchors to restrict to start/end.
        Blacklist always takes precedence over whitelist.

        Args:
            hostname: The device hostname to check.

        Returns:
            FilterDecision.PASS if the device should be imported,
            FilterDecision.BLACKLIST if excluded by a blacklist pattern,
            FilterDecision.WHITELIST_MISS if a non-empty whitelist matched nothing.
        """
        # Blacklist takes precedence
        for pat in self.blacklist_patterns:
            if pat.search(hostname):
                return FilterDecision.BLACKLIST

        # If whitelist is non-empty, must match at least one pattern
        if self.whitelist_patterns:
            if any(pat.search(hostname) for pat in self.whitelist_patterns):
                return FilterDecision.PASS
            return FilterDecision.WHITELIST_MISS

        # No whitelist = include by default
        return FilterDecision.PASS

    def should_import(self, hostname: str) -> bool:
        """
        Return True if hostname passes whitelist/blacklist filters.

        Thin wrapper around :meth:`classify` for callers that only need a
        boolean include/exclude decision.

        Args:
            hostname: The device hostname to check.

        Returns:
            True if device should be imported, False if filtered out.
        """
        return self.classify(hostname) is FilterDecision.PASS
