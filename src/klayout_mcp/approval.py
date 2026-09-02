"""Fail-closed trust boundary for externally approved design intent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .errors import AnalysisError
from .evidence_state import evaluate_evidence_ladder
from .workflow_manifest import (
    canonical_sha256,
    validate_approved_design_intent_reference,
    validate_design_intent_draft,
)


APPROVAL_SCOPES = ("planning", "planning_and_generation")
_NONPRODUCTION_VERIFIER_MARKERS = ("test", "mock", "fake", "stub")


@runtime_checkable
class ApprovalVerifier(Protocol):
    """Host-injected verifier; it is never constructed from MCP model arguments."""

    backend_id: str
    trusted: bool

    def verify(
        self,
        *,
        draft_document: Mapping[str, Any],
        approval_reference: Mapping[str, Any],
        required_scope: str,
        output_class: str,
        checked_at: str,
    ) -> Mapping[str, Any]: ...


def require_host_approval_verifier(
    verifier: ApprovalVerifier | None,
    *,
    production_mode: bool,
) -> ApprovalVerifier:
    """Validate a host-injected verifier without accepting model-selected plugins."""

    if verifier is None:
        _fail(
            "APPROVAL_BACKEND_UNAVAILABLE",
            "No trusted approval verifier is configured on this MCP host.",
            details={"backend_configured": False},
        )
    if not isinstance(verifier, ApprovalVerifier):
        _fail(
            "INVALID_APPROVAL_VERIFIER",
            "The configured approval verifier does not implement the host contract.",
            details={"verifier_type": type(verifier).__name__},
        )
    backend_id = getattr(verifier, "backend_id", None)
    if (
        getattr(verifier, "trusted", None) is not True
        or not isinstance(backend_id, str)
        or not backend_id.strip()
    ):
        _fail(
            "UNTRUSTED_APPROVAL_BACKEND",
            "The configured backend is not a trusted approval authority.",
            details={"backend_id": backend_id, "trusted": False},
        )
    if production_mode:
        verifier_type = type(verifier)
        identity = " ".join(
            (
                backend_id,
                verifier_type.__name__,
                verifier_type.__module__,
            )
        ).lower()
        markers = sorted(
            marker for marker in _NONPRODUCTION_VERIFIER_MARKERS if marker in identity
        )
        explicitly_nonproduction = (
            getattr(verifier, "nonproduction_only", False) is True
        )
        if markers or explicitly_nonproduction:
            _fail(
                "NONPRODUCTION_APPROVAL_VERIFIER_FORBIDDEN",
                "Production mode rejects test, mock, fake, and stub approval backends.",
                details={
                    "backend_id": backend_id,
                    "verifier_type": verifier_type.__name__,
                    "module": verifier_type.__module__,
                    "matched_markers": markers,
                    "explicitly_nonproduction": explicitly_nonproduction,
                },
            )
    return verifier


def approval_verifier_contract(*, backend_configured: bool = False) -> dict[str, Any]:
    """Describe the boundary without exposing a token-minting or trust bypass API."""

    return {
        "contract_version": 1,
        "backend_configured": backend_configured,
        "default_behavior": "fail_closed",
        "allowed_scopes": list(APPROVAL_SCOPES),
        "model_arguments_can_select_or_construct_verifier": False,
        "mcp_can_mint_approval": False,
        "reference_shape_validation_is_approval": False,
        "explicit_nonproduction_verifier_rejected_in_production_mode": True,
        "revocation_check_required": True,
        "exact_draft_hash_required": True,
        "exact_process_capability_hash_required": True,
        "exact_output_scope_required": True,
    }


def _fail(code: str, message: str, *, details: Mapping[str, Any]) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**dict(details), "production_ready": False},
        next_action="Use a trusted client-controlled approval backend for this exact intent.",
    )


def _timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _fail(
            "INVALID_APPROVAL_TIMESTAMP",
            f"{field} must be a timezone-aware ISO-8601 timestamp.",
            details={"field": field},
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(
            "INVALID_APPROVAL_TIMESTAMP",
            f"{field} must be a timezone-aware ISO-8601 timestamp.",
            details={"field": field},
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(
            "INVALID_APPROVAL_TIMESTAMP",
            f"{field} must include a timezone offset.",
            details={"field": field},
        )
    return parsed.astimezone(timezone.utc)


def verify_design_intent_approval(
    *,
    design_intent_draft: Mapping[str, Any],
    approval_reference: Mapping[str, Any],
    required_scope: str,
    output_class: str,
    verifier: ApprovalVerifier | None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Verify exact approval provenance; never trust booleans or caller-made hashes."""

    if required_scope not in APPROVAL_SCOPES:
        _fail(
            "UNSUPPORTED_APPROVAL_SCOPE",
            "The requested approval scope is not part of this verifier contract.",
            details={"required_scope": required_scope, "allowed_scopes": list(APPROVAL_SCOPES)},
        )
    if not isinstance(output_class, str) or not output_class.strip():
        _fail(
            "INVALID_APPROVAL_OUTPUT_CLASS",
            "The requested output class must be a non-empty string.",
            details={"output_class": output_class},
        )
    output_class = output_class.strip()
    try:
        checked_time = (clock or (lambda: datetime.now(timezone.utc)))()
    except Exception as exc:
        _fail(
            "APPROVAL_CLOCK_FAILED",
            "The host approval clock failed closed.",
            details={"error_type": type(exc).__name__},
        )
    if (
        not isinstance(checked_time, datetime)
        or checked_time.tzinfo is None
        or checked_time.utcoffset() is None
    ):
        _fail(
            "INVALID_APPROVAL_CLOCK",
            "The host approval clock must return a timezone-aware datetime.",
            details={"received_type": type(checked_time).__name__},
        )
    checked_time = checked_time.astimezone(timezone.utc)
    checked_at = checked_time.isoformat()

    draft_result = validate_design_intent_draft(design_intent_draft)
    if not draft_result["draft_complete"]:
        _fail(
            "INCOMPLETE_DESIGN_INTENT_CANNOT_BE_APPROVED",
            "Unresolved design questions prevent approval verification.",
            details={
                "unresolved_question_count": draft_result["unresolved_question_count"]
            },
        )
    reference_result = validate_approved_design_intent_reference(approval_reference)
    reference = reference_result["document"]
    draft = draft_result["document"]
    expected_draft_hash = draft_result["canonical_sha256"]
    capability_hash = draft["process"]["capability_sha256"]
    reference_hash = reference_result["canonical_sha256"]

    binding_mismatches: dict[str, Any] = {}
    if reference["draft_sha256"] != expected_draft_hash:
        binding_mismatches["draft_sha256"] = {
            "expected": expected_draft_hash,
            "actual": reference["draft_sha256"],
        }
    if reference["process_capability_sha256"] != capability_hash:
        binding_mismatches["process_capability_sha256"] = {
            "expected": capability_hash,
            "actual": reference["process_capability_sha256"],
        }
    if reference["approval_scope"] != required_scope:
        binding_mismatches["approval_scope"] = {
            "expected": required_scope,
            "actual": reference["approval_scope"],
        }
    if output_class not in reference["output_classes"]:
        binding_mismatches["output_class"] = {
            "expected_member": output_class,
            "actual": reference["output_classes"],
        }
    if binding_mismatches:
        _fail(
            "APPROVAL_REFERENCE_BINDING_MISMATCH",
            "The approval reference does not bind the exact draft, process, scope, and output.",
            details={"mismatches": binding_mismatches},
        )

    approved_at = _timestamp(reference["approved_at"], field="approved_at")
    if approved_at > checked_time:
        _fail(
            "APPROVAL_NOT_YET_VALID",
            "Approval time is later than the verifier check time.",
            details={"approved_at": reference["approved_at"], "checked_at": checked_at},
        )
    if reference.get("expires_at") is not None:
        expires_at = _timestamp(reference["expires_at"], field="expires_at")
        if expires_at <= approved_at or checked_time >= expires_at:
            _fail(
                "APPROVAL_EXPIRED",
                "The approval reference is expired or has an invalid validity interval.",
                details={"expires_at": reference["expires_at"], "checked_at": checked_at},
            )

    if verifier is None:
        _fail(
            "APPROVAL_BACKEND_UNAVAILABLE",
            "No trusted approval verifier is configured on this MCP host.",
            details={"backend_configured": False},
        )
    if not isinstance(verifier, ApprovalVerifier):
        _fail(
            "INVALID_APPROVAL_VERIFIER",
            "The configured approval verifier does not implement the host contract.",
            details={"verifier_type": type(verifier).__name__},
        )
    backend_id = getattr(verifier, "backend_id", None)
    if (
        getattr(verifier, "trusted", None) is not True
        or not isinstance(backend_id, str)
        or not backend_id.strip()
    ):
        _fail(
            "UNTRUSTED_APPROVAL_BACKEND",
            "The configured backend is not a trusted approval authority.",
            details={"backend_id": backend_id, "trusted": False},
        )

    try:
        raw_receipt = verifier.verify(
            draft_document=draft,
            approval_reference=reference,
            required_scope=required_scope,
            output_class=output_class,
            checked_at=checked_at,
        )
    except Exception as exc:
        _fail(
            "APPROVAL_BACKEND_FAILED",
            "The trusted approval backend failed closed.",
            details={"backend_id": backend_id, "error_type": type(exc).__name__},
        )
    if not isinstance(raw_receipt, Mapping):
        _fail(
            "INVALID_APPROVAL_RECEIPT",
            "The trusted backend returned an invalid receipt.",
            details={"backend_id": backend_id},
        )
    receipt = dict(raw_receipt)
    expected_receipt = {
        "verified": True,
        "backend_id": backend_id,
        "draft_sha256": expected_draft_hash,
        "process_capability_sha256": capability_hash,
        "approval_reference_sha256": reference_hash,
        "approval_scope": required_scope,
        "output_class": output_class,
        "signature_or_attestation_verified": True,
        "revocation_checked": True,
        "not_revoked": True,
        "verified_at": checked_at,
    }
    missing = sorted(set(expected_receipt).difference(receipt))
    mismatches = {
        field: {"expected": expected, "actual": receipt.get(field)}
        for field, expected in expected_receipt.items()
        if field in receipt and receipt[field] != expected
    }
    verified_at = receipt.get("verified_at")
    verification_receipt_sha256 = receipt.get("verification_receipt_sha256")
    if isinstance(verified_at, str) and verified_at.strip():
        _timestamp(verified_at, field="receipt.verified_at")
    if (
        not isinstance(verification_receipt_sha256, str)
        or len(verification_receipt_sha256) != 64
        or any(character not in "0123456789abcdef" for character in verification_receipt_sha256)
    ):
        missing.append("verification_receipt_sha256")
    if missing or mismatches:
        _fail(
            "APPROVAL_VERIFICATION_FAILED",
            "The trusted backend did not verify every required approval binding.",
            details={
                "backend_id": backend_id,
                "missing_or_invalid_fields": sorted(set(missing)),
                "mismatches": mismatches,
            },
        )

    evidence_ladder = evaluate_evidence_ladder(
        {
            "draft_schema_valid": True,
            "unresolved_questions_zero": True,
            "approval_backend_trusted": True,
            "approval_verified": True,
        }
    )
    return {
        "ok": True,
        "contract_version": 1,
        "approval_backend_id": backend_id,
        "design_intent_sha256": expected_draft_hash,
        "approved_intent_reference_sha256": reference_hash,
        "process_capability_sha256": capability_hash,
        "approval_scope": required_scope,
        "output_class": output_class,
        "verification_receipt_sha256": verification_receipt_sha256,
        "verified_at": verified_at,
        "intent_approved": True,
        "approval_verified": True,
        "approval_scope_allows_planning": True,
        "approval_scope_allows_generation": required_scope == "planning_and_generation",
        "authorizes_planning": False,
        "authorizes_generation": False,
        "evidence_ladder": evidence_ladder,
        "production_ready": False,
        "next_gate": (
            "Bind the exact live process capability, content-addressed parent job, and "
            "approved output policy before planning or generation."
        ),
    }
