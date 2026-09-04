"""Small shared helpers for talking to the RADKit ControlAPI."""

from __future__ import annotations

from typing import Any

from radkit_service.control_api import APIResult


def require_api_result_ok(result: APIResult, action: str) -> Any:
    """Return the typed API result payload or raise on RADKit API errors."""
    if APIResult.is_error(result):
        raise RuntimeError(
            f"RADKit ControlAPI failed to {action}: {result.root.message} ({result.root.detail})"
        )
    return result.result
