"""Tests for device filtering."""

from __future__ import annotations

from radkit_catc_sync.filters import FilterDecision, FilterSet


class TestShouldImport:
    def test_no_filters_allows_all(self) -> None:
        filters = FilterSet.from_lists([], [])
        assert filters.should_import("router1.dc1.example.com") is True

    def test_blacklist_blocks_on_fqdn(self) -> None:
        filters = FilterSet.from_lists([], [r"\.lab\."])
        assert filters.should_import("sw01.lab.example.com") is False
        assert filters.should_import("sw01.prod.example.com") is True

    def test_blacklist_blocks_on_shortname_part(self) -> None:
        filters = FilterSet.from_lists([], [r"-lab-"])
        assert filters.should_import("sw-lab-01.example.com") is False
        assert filters.should_import("sw-prod-01.example.com") is True

    def test_whitelist_allows_only_matching(self) -> None:
        filters = FilterSet.from_lists([r"^router-"], [])
        assert filters.should_import("router-01.example.com") is True
        assert filters.should_import("switch-01.example.com") is False

    def test_blacklist_beats_whitelist(self) -> None:
        filters = FilterSet.from_lists([r"^router-"], [r"\.lab\."])
        assert filters.should_import("router-01.lab.example.com") is False

    def test_whitelist_domain_match(self) -> None:
        filters = FilterSet.from_lists([r"\.prod\."], [])
        assert filters.should_import("sw-core-01.prod.example.com") is True
        assert filters.should_import("sw-edge-01.dev.example.com") is False

    def test_search_matches_anywhere(self) -> None:
        # re.search — pattern need not be anchored to match
        filters = FilterSet.from_lists([r"core"], [])
        assert filters.should_import("sw-core-01.example.com") is True
        assert filters.should_import("sw-edge-01.example.com") is False

    def test_case_insensitive(self) -> None:
        # re.IGNORECASE — uppercase hostnames match lowercase patterns
        filters = FilterSet.from_lists([], [r"-lab-"])
        assert filters.should_import("SW-LAB-01.EXAMPLE.COM") is False
        assert filters.should_import("SW-PROD-01.EXAMPLE.COM") is True


class TestClassify:
    def test_no_filters_pass(self) -> None:
        filters = FilterSet.from_lists([], [])
        assert filters.classify("router1.example.com") is FilterDecision.PASS

    def test_blacklist_hit(self) -> None:
        filters = FilterSet.from_lists([], [r"\.lab\."])
        assert filters.classify("sw01.lab.example.com") is FilterDecision.BLACKLIST

    def test_whitelist_match_passes(self) -> None:
        filters = FilterSet.from_lists([r"^router-"], [])
        assert filters.classify("router-01.example.com") is FilterDecision.PASS

    def test_whitelist_miss(self) -> None:
        filters = FilterSet.from_lists([r"^router-"], [])
        assert filters.classify("switch-01.example.com") is FilterDecision.WHITELIST_MISS

    def test_blacklist_precedence_over_whitelist(self) -> None:
        # Even though the whitelist would match, blacklist wins.
        filters = FilterSet.from_lists([r"^router-"], [r"\.lab\."])
        assert filters.classify("router-01.lab.example.com") is FilterDecision.BLACKLIST

    def test_should_import_matches_classify(self) -> None:
        filters = FilterSet.from_lists([r"^router-"], [r"\.lab\."])
        for host in (
            "router-01.example.com",
            "switch-01.example.com",
            "router-01.lab.example.com",
        ):
            assert filters.should_import(host) is (filters.classify(host) is FilterDecision.PASS)
