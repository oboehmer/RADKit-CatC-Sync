"""Tests for device filtering (should_import)."""

from __future__ import annotations

import catc_sync


class TestShouldImport:
    def setup_method(self) -> None:
        catc_sync.DEVICE_WHITELIST = []
        catc_sync.DEVICE_BLACKLIST = []
        catc_sync.compile_filters()

    def test_no_filters_allows_all(self) -> None:
        assert catc_sync.should_import("router1.dc1.example.com") is True

    def test_blacklist_blocks_on_fqdn(self) -> None:
        catc_sync.DEVICE_BLACKLIST = [r"\.lab\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw01.lab.example.com") is False
        assert catc_sync.should_import("sw01.prod.example.com") is True

    def test_blacklist_blocks_on_shortname_part(self) -> None:
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-lab-01.example.com") is False
        assert catc_sync.should_import("sw-prod-01.example.com") is True

    def test_whitelist_allows_only_matching(self) -> None:
        catc_sync.DEVICE_WHITELIST = [r"^router-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("router-01.example.com") is True
        assert catc_sync.should_import("switch-01.example.com") is False

    def test_blacklist_beats_whitelist(self) -> None:
        catc_sync.DEVICE_WHITELIST = [r"^router-"]
        catc_sync.DEVICE_BLACKLIST = [r"\.lab\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("router-01.lab.example.com") is False

    def test_whitelist_domain_match(self) -> None:
        catc_sync.DEVICE_WHITELIST = [r"\.prod\."]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-core-01.prod.example.com") is True
        assert catc_sync.should_import("sw-edge-01.dev.example.com") is False

    def test_search_matches_anywhere(self) -> None:
        # re.search — pattern need not be anchored to match
        catc_sync.DEVICE_WHITELIST = [r"core"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("sw-core-01.example.com") is True
        assert catc_sync.should_import("sw-edge-01.example.com") is False

    def test_case_insensitive(self) -> None:
        # re.IGNORECASE — uppercase hostnames match lowercase patterns
        catc_sync.DEVICE_BLACKLIST = [r"-lab-"]
        catc_sync.compile_filters()
        assert catc_sync.should_import("SW-LAB-01.EXAMPLE.COM") is False
        assert catc_sync.should_import("SW-PROD-01.EXAMPLE.COM") is True
