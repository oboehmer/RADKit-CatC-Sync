"""Statistics tracking for sync operations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Stats:
    """Tracks sync operation results."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    adopted: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self, dry_run: bool = False) -> str:
        """
        Generate a human-readable summary of sync results.

        Args:
            dry_run: Whether this was a dry-run (affects header text).

        Returns:
            Formatted summary string.
        """
        header = "--- Sync Summary (DRY-RUN) ---" if dry_run else "--- Sync Summary ---"
        lines = [
            header,
            f"  Added:           {self.added}",
            f"  Updated:         {self.updated}",
            f"  Unchanged:       {self.unchanged}",
            f"  Adopted:         {self.adopted}  (existing unmanaged devices taken over)",
            f"  Deleted:         {self.deleted}",
            f"  Skipped:         {self.skipped}",
            f"  Errors:          {self.errors}",
        ]
        if self.warnings:
            lines.append(f"  Warnings: {len(self.warnings)}")
            for w in self.warnings:
                display = w if len(w) <= 200 else w[:197] + "..."
                lines.append(f"    - {display}")
        return "\n".join(lines)
