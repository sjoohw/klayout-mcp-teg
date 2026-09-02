"""Actionable, deterministic validation reports shared by public workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping


ISSUE_CATEGORIES = frozenset(
    {
        "schema",
        "semantic",
        "coverage_or_identifiability",
        "decision_required",
        "adapter_compatibility",
        "execution_environment",
        "verification_gate",
        "internal",
    }
)
ISSUE_SEVERITIES = frozenset({"blocker", "error", "warning", "info"})
SECRET_FIELD_PATTERN = re.compile(
    r"(?:^|[._/-])(password|secret|token|credential|license_key|api_key)(?:$|[._/-])",
    re.IGNORECASE,
)


def _json_pointer(field: Any) -> str:
    if not isinstance(field, str) or not field.strip():
        return ""
    value = field.strip()
    if value.startswith("/"):
        return value
    parts = [part for part in re.split(r"\.|\[([^]]+)\]", value) if part]
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _safe_value(field_path: str, value: Any) -> dict[str, Any]:
    if SECRET_FIELD_PATTERN.search(field_path):
        text = "" if value is None else str(value)
        return {
            "redacted": True,
            "received_type": type(value).__name__,
            "length": len(text),
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        text = value
        if isinstance(text, str) and len(text) > 512:
            return {
                "redacted": True,
                "received_type": "str",
                "length": len(text),
                "reason": "value_too_large",
            }
        return {"redacted": False, "value": text, "received_type": type(value).__name__}
    if isinstance(value, (list, tuple, set, Mapping)):
        return {
            "redacted": True,
            "received_type": type(value).__name__,
            "length": len(value),
            "reason": "collection_not_embedded",
        }
    return {"redacted": True, "received_type": type(value).__name__}


def _category_for_code(code: str) -> str:
    upper = code.upper()
    if any(token in upper for token in ("ADAPTER", "PROCESS_CAPABILITY", "REGISTRY")):
        return "adapter_compatibility"
    if any(token in upper for token in ("DRC", "LVS", "PEX", "VERIFY", "EVIDENCE", "SIGNOFF")):
        return "verification_gate"
    if any(token in upper for token in ("UNRESOLVED", "DECISION", "APPROVAL", "QUESTION")):
        return "decision_required"
    if any(token in upper for token in ("COVERAGE", "IDENTIFI", "AMBIGU", "CORPUS")):
        return "coverage_or_identifiability"
    if any(token in upper for token in ("FILESYSTEM", "EXECUTABLE", "DEPENDENCY", "CLOCK", "ENVIRONMENT")):
        return "execution_environment"
    if any(token in upper for token in ("SCHEMA", "FIELD", "TYPE", "HASH", "DOCUMENT")):
        return "schema"
    return "semantic"


@dataclass(frozen=True, slots=True)
class ActionableIssue:
    code: str
    category: str
    severity: str
    stage: str
    message: str
    field_path: str = ""
    object_identity: Mapping[str, Any] = field(default_factory=dict)
    received: Mapping[str, Any] = field(default_factory=dict)
    expected: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""
    fix: str = ""
    example: Any = None
    related_artifact_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("ActionableIssue.code must be a non-empty stable code.")
        if self.category not in ISSUE_CATEGORIES:
            raise ValueError(f"Unsupported issue category: {self.category}")
        if self.severity not in ISSUE_SEVERITIES:
            raise ValueError(f"Unsupported issue severity: {self.severity}")
        if self.field_path and not self.field_path.startswith("/"):
            raise ValueError("ActionableIssue.field_path must be an RFC 6901 pointer.")

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (self.stage, self.field_path, self.severity, self.code, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
            "field_path": self.field_path,
            "object_identity": dict(self.object_identity),
            "received": dict(self.received),
            "expected": dict(self.expected),
            "reason": self.reason,
            "fix": self.fix,
            "example": self.example,
            "related_artifact_hashes": list(self.related_artifact_hashes),
        }


@dataclass(frozen=True, slots=True)
class ClarificationQuestion:
    question_id: str
    question: str
    reason: str
    answer_schema: Mapping[str, Any]
    options: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "reason": self.reason,
            "answer_schema": dict(self.answer_schema),
            "options": [dict(option) for option in self.options],
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    summary: str
    issues: tuple[ActionableIssue, ...]
    request_id: str | None = None
    draft_id: str | None = None
    draft_revision: int | None = None
    questions: tuple[ClarificationQuestion, ...] = ()
    next_action: str | None = None
    retry_stage: str | None = None
    resume_token: str | None = None
    source_modified: bool = False
    stage_appended: bool = False
    geometry_generation_started: bool = False
    final_output_promoted: bool = False
    total_issue_count: int | None = field(default=None, repr=False)

    @classmethod
    def build(
        cls,
        *,
        summary: str,
        issues: Iterable[ActionableIssue],
        max_embedded_issues: int = 50,
        **kwargs: Any,
    ) -> "ValidationReport":
        if isinstance(max_embedded_issues, bool) or max_embedded_issues <= 0:
            raise ValueError("max_embedded_issues must be a positive integer.")
        deduplicated = {issue.sort_key(): issue for issue in issues}
        ordered = tuple(deduplicated[key] for key in sorted(deduplicated))
        return cls(
            summary=summary,
            issues=ordered[:max_embedded_issues],
            total_issue_count=len(ordered),
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        total = self.total_issue_count if self.total_issue_count is not None else len(self.issues)
        return {
            "schema_version": 1,
            "summary": self.summary,
            "request_id": self.request_id,
            "draft_id": self.draft_id,
            "draft_revision": self.draft_revision,
            "issues": [issue.to_dict() for issue in self.issues],
            "questions": [question.to_dict() for question in self.questions],
            "total_issue_count": total,
            "issues_truncated": total > len(self.issues),
            "next_action": self.next_action,
            "retry_stage": self.retry_stage,
            "resume_token": self.resume_token,
            "mutation_state": {
                "source_modified": self.source_modified,
                "stage_appended": self.stage_appended,
                "geometry_generation_started": self.geometry_generation_started,
                "final_output_promoted": self.final_output_promoted,
            },
        }


def issue_from_error(
    *,
    code: str,
    message: str,
    details: Mapping[str, Any],
    next_action: str | None,
) -> ActionableIssue:
    """Convert a legacy structured domain error into the common issue contract."""

    field = details.get("field", details.get("field_path", ""))
    field_path = _json_pointer(field)
    received_value = details.get("value", details.get("received"))
    expected: dict[str, Any] = {}
    for key in (
        "expected",
        "received_type",
        "supported",
        "allowed",
        "minimum",
        "maximum",
        "unit",
        "grid",
        "missing",
        "unexpected",
    ):
        if key in details:
            expected[key] = details[key]
    identity = {
        key: details[key]
        for key in ("dut_id", "cell", "occurrence_id", "net", "pad_id", "segment_id")
        if key in details
    }
    related_hashes = tuple(
        sorted(
            str(value)
            for key, value in details.items()
            if key.endswith("sha256") and isinstance(value, str)
        )
    )
    return ActionableIssue(
        code=code,
        category=_category_for_code(code),
        severity="blocker",
        stage=str(details.get("stage", "validation")),
        message=message,
        field_path=field_path,
        object_identity=identity,
        received=_safe_value(field_path, received_value),
        expected=expected,
        reason=str(details.get("reason", message)),
        fix=next_action or "Correct the identified input and retry the same stage.",
        example=details.get("example_fix_payload"),
        related_artifact_hashes=related_hashes,
    )


def report_from_error(
    *,
    code: str,
    message: str,
    details: Mapping[str, Any],
    next_action: str | None,
) -> dict[str, Any]:
    stage = str(details.get("stage", "validation"))
    issue = issue_from_error(
        code=code,
        message=message,
        details=details,
        next_action=next_action,
    )
    report = ValidationReport.build(
        summary=f"{stage} 단계에서 blocker 1건이 발견되어 다음 단계로 진행하지 않았습니다.",
        issues=[issue],
        next_action=next_action,
        retry_stage=stage,
    )
    return report.to_dict()
