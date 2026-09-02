"""Host-owned qualification policy boundary for transistor adapter candidates."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import AnalysisError
from .workflow_manifest import canonical_sha256, immutable_json_copy


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SCORING_FIELDS = (
    "absolute_tolerance",
    "relative_tolerance",
    "minimum_aggregate_score",
    "exact_fingerprint_required",
)


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "adapter_qualification_policy", **dict(details)},
        next_action=next_action,
    )


@runtime_checkable
class QualificationPolicyAuthority(Protocol):
    """Host-injected authority; MCP arguments cannot construct or select it."""

    authority_id: str
    trusted: bool

    def issue_policy(
        self,
        *,
        corpus_sha256: str,
        compiler_identity: Mapping[str, Any],
        available_metrics: tuple[str, ...],
    ) -> Mapping[str, Any]: ...

    def verify_policy(
        self,
        *,
        policy_document: Mapping[str, Any],
        approval_receipt: Mapping[str, Any],
        corpus_sha256: str,
        compiler_identity: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def _require_authority(
    authority: QualificationPolicyAuthority | None,
) -> QualificationPolicyAuthority:
    if authority is None:
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_AUTHORITY_UNAVAILABLE",
            "No trusted host qualification-policy authority is configured.",
            details={"authority_configured": False},
            next_action="Configure an installed host-owned qualification policy authority; caller scoring policies are diagnostic only.",
        )
    if (
        not isinstance(authority, QualificationPolicyAuthority)
        or authority.trusted is not True
        or not isinstance(authority.authority_id, str)
        or not authority.authority_id.strip()
    ):
        _fail(
            "UNTRUSTED_ADAPTER_QUALIFICATION_POLICY_AUTHORITY",
            "The configured qualification-policy authority is not trusted.",
            details={"authority_type": type(authority).__name__},
            next_action="Install and allowlist a trusted organizational qualification policy authority.",
        )
    return authority


def _validate_policy_document(
    policy: Any,
    *,
    authority: QualificationPolicyAuthority,
    available_metrics: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_INVALID",
            "The host authority returned no qualification policy document.",
            details={"received_type": type(policy).__name__},
            next_action="Correct the installed qualification-policy authority.",
        )
    document = immutable_json_copy(policy)
    required_fields = {
        "schema_version",
        "artifact_type",
        "authority_id",
        "policy_id",
        "policy_version",
        *SCORING_FIELDS,
        "required_metrics",
    }
    missing = sorted(required_fields.difference(document))
    unexpected = sorted(set(document).difference(required_fields))
    required_metrics = document.get("required_metrics")
    numeric_values: dict[str, float] = {}
    for name in SCORING_FIELDS[:3]:
        value = document.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            numeric_values[name] = math.nan
        else:
            numeric_values[name] = float(value)
    invalid_metrics = (
        []
        if isinstance(required_metrics, list)
        else ["<required_metrics-must-be-a-list>"]
    )
    if isinstance(required_metrics, list):
        invalid_metrics = sorted(
            str(metric)
            for metric in required_metrics
            if not isinstance(metric, str)
            or not metric.strip()
            or metric not in available_metrics
        )
    if (
        missing
        or unexpected
        or document.get("schema_version") != 1
        or document.get("artifact_type") != "AdapterQualificationPolicy"
        or document.get("authority_id") != authority.authority_id
        or any(
            not isinstance(document.get(name), str) or not document.get(name, "").strip()
            for name in ("policy_id", "policy_version")
        )
        or not isinstance(document.get("exact_fingerprint_required"), bool)
        or any(not math.isfinite(value) or value < 0 for value in numeric_values.values())
        or numeric_values.get("minimum_aggregate_score", 0) <= 0
        or numeric_values.get("minimum_aggregate_score", 0) > 1
        or not isinstance(required_metrics, list)
        or not required_metrics
        or len(required_metrics) != len(set(required_metrics))
        or invalid_metrics
    ):
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_INVALID",
            "The host qualification policy is incomplete, weak, or references unavailable metrics.",
            details={
                "missing": missing,
                "unexpected": unexpected,
                "invalid_metrics": invalid_metrics,
                "available_metrics": list(available_metrics),
                "minimum_aggregate_score": document.get("minimum_aggregate_score"),
            },
            next_action="Approve a policy with a positive threshold and explicit required metrics from this corpus.",
        )
    return document


def _validate_receipt(
    receipt: Any,
    *,
    authority: QualificationPolicyAuthority,
    policy_sha256: str,
    corpus_sha256: str,
    compiler_identity_sha256: str,
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_RECEIPT_INVALID",
            "The host authority returned no qualification-policy approval receipt.",
            details={"received_type": type(receipt).__name__},
            next_action="Correct the installed qualification-policy authority.",
        )
    document = immutable_json_copy(receipt)
    expected = {
        "approved": True,
        "authority_id": authority.authority_id,
        "policy_sha256": policy_sha256,
        "corpus_sha256": corpus_sha256,
        "compiler_identity_sha256": compiler_identity_sha256,
        "signature_or_attestation_verified": True,
        "revocation_checked": True,
        "not_revoked": True,
    }
    mismatches = {
        name: {"expected": value, "received": document.get(name)}
        for name, value in expected.items()
        if document.get(name) != value
    }
    receipt_sha256 = document.get("approval_receipt_sha256")
    receipt_body = {
        name: value
        for name, value in document.items()
        if name != "approval_receipt_sha256"
    }
    receipt_hash_matches = receipt_sha256 == canonical_sha256(receipt_body)
    if (
        mismatches
        or not isinstance(document.get("approved_by"), str)
        or not document.get("approved_by", "").strip()
        or not SHA256_PATTERN.fullmatch(str(receipt_sha256 or ""))
        or not receipt_hash_matches
    ):
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_RECEIPT_INVALID",
            "The policy receipt does not prove approval of this exact policy, corpus, and compiler.",
            details={
                "mismatches": mismatches,
                "approval_receipt_hash_matches": receipt_hash_matches,
            },
            next_action="Obtain a current non-revoked receipt from the trusted host authority.",
        )
    return document


def issue_qualification_policy(
    *,
    authority: QualificationPolicyAuthority | None,
    corpus_sha256: str,
    compiler_identity: Mapping[str, Any],
    available_metrics: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = _require_authority(authority)
    try:
        issued = selected.issue_policy(
            corpus_sha256=corpus_sha256,
            compiler_identity=immutable_json_copy(compiler_identity),
            available_metrics=available_metrics,
        )
    except AnalysisError:
        raise
    except Exception as exc:
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_AUTHORITY_FAILED",
            "The host qualification-policy authority failed closed.",
            details={"authority_id": selected.authority_id, "error_type": type(exc).__name__},
            next_action="Repair the host authority and retry without changing the corpus.",
        )
    if not isinstance(issued, Mapping):
        issued = {}
    policy = _validate_policy_document(
        issued.get("policy_document"),
        authority=selected,
        available_metrics=available_metrics,
    )
    policy_sha256 = canonical_sha256(policy)
    receipt = _validate_receipt(
        issued.get("approval_receipt"),
        authority=selected,
        policy_sha256=policy_sha256,
        corpus_sha256=corpus_sha256,
        compiler_identity_sha256=canonical_sha256(compiler_identity),
    )
    return policy, receipt


def verify_qualification_policy(
    *,
    authority: QualificationPolicyAuthority | None,
    policy_document: Mapping[str, Any],
    approval_receipt: Mapping[str, Any],
    corpus_sha256: str,
    compiler_identity: Mapping[str, Any],
    available_metrics: tuple[str, ...],
) -> dict[str, Any]:
    selected = _require_authority(authority)
    policy = _validate_policy_document(
        policy_document,
        authority=selected,
        available_metrics=available_metrics,
    )
    receipt = _validate_receipt(
        approval_receipt,
        authority=selected,
        policy_sha256=canonical_sha256(policy),
        corpus_sha256=corpus_sha256,
        compiler_identity_sha256=canonical_sha256(compiler_identity),
    )
    try:
        verified = selected.verify_policy(
            policy_document=policy,
            approval_receipt=receipt,
            corpus_sha256=corpus_sha256,
            compiler_identity=immutable_json_copy(compiler_identity),
        )
    except AnalysisError:
        raise
    except Exception as exc:
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_AUTHORITY_FAILED",
            "The host qualification-policy authority could not reverify the candidate receipt.",
            details={"authority_id": selected.authority_id, "error_type": type(exc).__name__},
            next_action="Restore authority availability and retry candidate packaging.",
        )
    expected = {
        "verified": True,
        "authority_id": selected.authority_id,
        "policy_sha256": canonical_sha256(policy),
        "approval_receipt_sha256": receipt["approval_receipt_sha256"],
        "corpus_sha256": corpus_sha256,
        "compiler_identity_sha256": canonical_sha256(compiler_identity),
        "revocation_checked": True,
        "not_revoked": True,
    }
    mismatches = {
        name: {"expected": value, "received": verified.get(name)}
        for name, value in expected.items()
        if not isinstance(verified, Mapping) or verified.get(name) != value
    }
    if mismatches:
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_REVERIFICATION_FAILED",
            "The host authority did not reverify the exact current qualification policy receipt.",
            details={"mismatches": mismatches},
            next_action="Do not package the candidate; obtain a current non-revoked policy receipt.",
        )
    return immutable_json_copy(verified)
