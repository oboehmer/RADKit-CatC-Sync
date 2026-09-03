"""Tests for the Stats tracker (fetch/skip reporting)."""

from __future__ import annotations

from radkit_catc_sync.stats import SkipReason, Stats


class TestRecordSkip:
    def test_increments_reason_and_total(self) -> None:
        stats = Stats()
        stats.record_skip(SkipReason.BLACKLIST)
        stats.record_skip(SkipReason.BLACKLIST)
        stats.record_skip(SkipReason.WHITELIST_MISS)
        assert stats.skipped == 3
        assert stats.skipped_by_reason[SkipReason.BLACKLIST] == 2
        assert stats.skipped_by_reason[SkipReason.WHITELIST_MISS] == 1

    def test_count_argument(self) -> None:
        stats = Stats()
        stats.record_skip(SkipReason.NO_HOSTNAME, 5)
        assert stats.skipped == 5
        assert stats.skipped_by_reason[SkipReason.NO_HOSTNAME] == 5


class TestSummary:
    def test_fetched_block_present_when_populated(self) -> None:
        stats = Stats()
        stats.fetched_total = 12
        stats.fetched_per_cluster = {"catc1.example.com": 7, "catc2.example.com": 5}
        summary = stats.summary()
        assert "Fetched:" in summary
        assert "across 2 cluster(s)" in summary
        assert "catc1.example.com:  7" in summary
        assert "catc2.example.com:  5" in summary

    def test_fetched_block_absent_when_empty(self) -> None:
        summary = Stats().summary()
        assert "Fetched:" not in summary

    def test_skip_breakdown_only_nonzero_in_enum_order(self) -> None:
        stats = Stats()
        stats.record_skip(SkipReason.WHITELIST_MISS)
        stats.record_skip(SkipReason.BLACKLIST)
        summary = stats.summary()
        assert "blacklisted: 1" in summary
        assert "not whitelisted: 1" in summary
        # Reasons with zero count are omitted
        assert "duplicate:" not in summary
        assert "no hostname:" not in summary
        # Enum order: BLACKLIST before WHITELIST_MISS
        assert summary.index("blacklisted:") < summary.index("not whitelisted:")

    def test_dry_run_header(self) -> None:
        assert "DRY-RUN" in Stats().summary(dry_run=True)
        assert "DRY-RUN" not in Stats().summary(dry_run=False)
