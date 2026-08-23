"""Structured errors returned by MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AnalysisError(ValueError):
    """Expected input or geometry failure."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    next_action: str | None = None

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if self.next_action:
            result["next_action"] = self.next_action
        return result
