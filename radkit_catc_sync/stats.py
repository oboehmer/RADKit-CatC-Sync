"""Statistics tracking for sync operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkipReason(Enum):
    """Why a device was skipped/ignored during a sync run.

    Definition order is also the display order used in :meth:`Stats.summary`.
    """

    NO_HOSTNAME = "no hostname"
    UNNAMEABLE = "unnameable hostname"
    BLACKLIST = "blacklisted"
    WHITELIST_MISS = "not whitelisted"
    COLLISION = "hostname collision"
    DUPLICATE = "duplicate"
    UNMANAGED_NOT_ADOPTED = "unmanaged (not adopted)"


@dataclass
class Stats:
    """Tracks sync operation results."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    adopted: int = 0
    deleted: int = 0
    # Devices renamed in place (UpdateDevice.name). Distinct from added/deleted:
    # a rename preserves the device UUID.
    renamed: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)

    # Fetch/ignore reporting
    fetched_total: int = 0
    fetched_per_cluster: dict[str, int] = field(default_factory=dict)
    skipped_by_reason: dict[SkipReason, int] = field(default_factory=dict)

    def record_skip(self, reason: SkipReason, count: int = 1) -> None:
        """Record a skipped device under a reason and bump the total skipped count."""
        self.skipped_by_reason[reason] = self.skipped_by_reason.get(reason, 0) + count
        self.skipped += count

    def summary(self, dry_run: bool = False) -> str:
        """
        Generate a human-readable summary of sync results.

        Args:
            dry_run: Whether this was a dry-run (affects header text).

        Returns:
            Formatted summary string.
        """
        header = "--- Sync Summary (DRY-RUN) ---" if dry_run else "--- Sync Summary ---"
        lines = [header]

        # Fetched block (only when we actually fetched something)
        if self.fetched_per_cluster or self.fetched_total:
            lines.append(
                f"  Fetched:         {self.fetched_total}  "
                f"(across {len(self.fetched_per_cluster)} cluster(s))"
            )
            for hostname, count in self.fetched_per_cluster.items():
                lines.append(f"    {hostname}:  {count}")

        lines.extend(
            [
                f"  Added:           {self.added}",
                f"  Updated:         {self.updated}",
                f"  Renamed:         {self.renamed}  (in place — device UUID preserved)",
                f"  Unchanged:       {self.unchanged}",
                f"  Adopted:         {self.adopted}  (existing unmanaged devices taken over)",
                f"  Deleted:         {self.deleted}",
            ]
        )
        lines.append(f"  Skipped:         {self.skipped}")

        # Skip breakdown by reason (enum order, non-zero only)
        for reason in SkipReason:
            count = self.skipped_by_reason.get(reason, 0)
            if count:
                lines.append(f"    {reason.value}: {count}")

        lines.append(f"  Errors:          {self.errors}")

        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)}")
            for w in self.warnings:
                display = w if len(w) <= 200 else w[:197] + "..."
                lines.append(f"    - {display}")
        return "\n".join(lines)
