"""Device label management: creation in RADKit and backfill computation."""

from __future__ import annotations

import logging

from radkit_service.control_api import ControlAPI
from radkit_service.webserver.models.labels import NewLabel

from .apiutils import require_api_result_ok
from .config import AppConfig
from .models import StoredRadkitDevice

logger = logging.getLogger(__name__)

# Colour assigned to labels this tool auto-creates.
DEFAULT_LABEL_COLOR = "#000000"


def ensure_labels_exist(
    api: ControlAPI,
    config: AppConfig,
    dry_run: bool,
) -> dict[str, int]:
    """
    Ensure all configured labels exist in RADKit.

    Auto-creates labels that don't exist yet. In dry-run mode, logs what would
    be created but doesn't actually create them.

    Args:
        api: RADKit ControlAPI instance.
        config: Application config (for device_labels).
        dry_run: If True, don't create labels.

    Returns:
        Dictionary mapping label name -> label ID.
    """
    if not config.device_labels:
        return {}

    # Fetch existing labels
    stored_labels = require_api_result_ok(api.list_labels(), "list labels")
    label_map: dict[str, int] = {label.name: label.id for label in (stored_labels or [])}

    # Identify missing labels
    missing_names = [name for name in config.device_labels if name not in label_map]

    if missing_names:
        if dry_run:
            logger.info(
                "[DRY-RUN] Would create %d missing label(s): %s",
                len(missing_names),
                ", ".join(missing_names),
            )
        else:
            new_labels = [NewLabel(name=name, color=DEFAULT_LABEL_COLOR) for name in missing_names]
            bulk_result = api.create_labels(new_labels)
            # BulkResult has successful_results() method to get created labels
            created_labels = list(bulk_result.successful_results())
            logger.info("Created %d label(s)", len(created_labels))
            for label in created_labels:
                label_map[label.name] = label.id

    return label_map


def compute_labels_to_add(
    existing: StoredRadkitDevice,
    configured_labels: list[str],
    label_ids: dict[str, int],
) -> list[str]:
    """
    Determine which configured labels are missing from an existing device.

    Labels are only ever added, never removed: labels present on the device but
    absent from the configuration are left untouched.

    Args:
        existing: The device as currently stored in RADKit (carries label IDs).
        configured_labels: Label names requested via configuration.
        label_ids: Mapping of label name -> RADKit label ID, as returned by
            :func:`ensure_labels_exist`. Names missing from this mapping are
            ignored (e.g. during a dry run, where labels are not created).

    Returns:
        Sorted list of label names to add. Sorted so that log output and the
        resulting UpdateLabelSet are deterministic across runs.
    """
    if not configured_labels or not label_ids:
        return []

    configured_ids = {label_ids[name] for name in configured_labels if name in label_ids}
    missing_ids = configured_ids - existing.labels
    id_to_name = {v: k for k, v in label_ids.items()}
    return sorted(id_to_name[lid] for lid in missing_ids)
