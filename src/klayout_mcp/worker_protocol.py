"""Shared response contract for code executed in KLayout's Python runtime."""

from __future__ import annotations

from typing import Any

from .errors import AnalysisError


def worker_error(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    """Build the same actionable error envelope used by host-side services."""

    return AnalysisError(
        code=code,
        message=message,
        details=details or {},
        next_action=next_action,
    ).to_result()
