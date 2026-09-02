"""Host-owned qualification policy boundary for transistor adapter candidates."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import AnalysisError
from .workflow_manifest import canonical_sha256, immutable_json_copy


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SCORING_FIELDS = (
    "minimum_aggregate_score",
    "exact_fingerprint_required",
    "metric_rules",
)
METRIC_RULE_FIELDS = frozenset(
    {
        "metric",
        "metric_kind",
        "comparison",
        "absolute_tolerance",
        "relative_tolerance",
        "weight",
        "hard_fail",
    }
)


def qualification_metric_kind(metric: str) -> str | None:
    """Return the physical/numeric kind encoded by a flattened corpus metric."""

    if metric.endswith(".present"):
        return "binary"
    if metric.endswith(".polygon_count"):
        return "count"
    if metric.endswith((".width_um", ".height_um")):
        return "length_um"
    if metric.endswith(".area_um2"):
        return "area_um2"
    return None


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
    }
    missing = sorted(required_fields.difference(document))
    unexpected = sorted(set(document).difference(required_fields))
    threshold = document.get("minimum_aggregate_score")
    metric_rules = document.get("metric_rules")
    invalid_rules: list[dict[str, Any]] = []
    normalized_rules: list[dict[str, Any]] = []
    seen_metrics: set[str] = set()
    if isinstance(metric_rules, list):
        for index, raw_rule in enumerate(metric_rules):
            field = f"metric_rules[{index}]"
            if not isinstance(raw_rule, Mapping):
                invalid_rules.append({"field": field, "reason": "must_be_object"})
                continue
            rule = immutable_json_copy(raw_rule)
            metric = rule.get("metric")
            expected_kind = (
                qualification_metric_kind(metric) if isinstance(metric, str) else None
            )
            numeric_names = ("absolute_tolerance", "relative_tolerance", "weight")
            invalid_numeric = [
                name
                for name in numeric_names
                if isinstance(rule.get(name), bool)
                or not isinstance(rule.get(name), (int, float))
                or not math.isfinite(float(rule[name]))
                or float(rule[name]) < 0
            ]
            reasons = []
            if set(rule) != METRIC_RULE_FIELDS:
                reasons.append("fields_must_match_schema")
            if not isinstance(metric, str) or metric not in available_metrics:
                reasons.append("metric_not_available")
            elif metric in seen_metrics:
                reasons.append("duplicate_metric")
            if expected_kind is None or rule.get("metric_kind") != expected_kind:
                reasons.append("metric_kind_mismatch")
            if rule.get("comparison") not in {"exact", "numeric_tolerance"}:
                reasons.append("comparison_invalid")
            if invalid_numeric:
                reasons.append("numeric_field_invalid")
            if not isinstance(rule.get("hard_fail"), bool):
                reasons.append("hard_fail_must_be_boolean")
            if (
                not invalid_numeric
                and rule.get("comparison") == "exact"
                and (
                    float(rule["absolute_tolerance"]) != 0
                    or float(rule["relative_tolerance"]) != 0
                )
            ):
                reasons.append("exact_comparison_requires_zero_tolerance")
            if expected_kind == "binary" and rule.get("comparison") != "exact":
                reasons.append("binary_metric_requires_exact_comparison")
            if reasons:
                invalid_rules.append(
                    {
                        "field": field,
                        "metric": metric,
                        "reasons": reasons,
                        "expected_metric_kind": expected_kind,
                        "invalid_numeric_fields": invalid_numeric,
                    }
                )
                continue
            seen_metrics.add(metric)
            normalized_rules.append(rule)
    missing_metric_rules = sorted(set(available_metrics).difference(seen_metrics))
    unexpected_metric_rules = sorted(seen_metrics.difference(available_metrics))
    positive_weight_count = sum(
        float(rule["weight"]) > 0 for rule in normalized_rules
    )
    hard_fail_count = sum(rule["hard_fail"] is True for rule in normalized_rules)
    if (
        missing
        or unexpected
        or document.get("schema_version") != 2
        or document.get("artifact_type") != "AdapterQualificationPolicy"
        or document.get("authority_id") != authority.authority_id
        or any(
            not isinstance(document.get(name), str) or not document.get(name, "").strip()
            for name in ("policy_id", "policy_version")
        )
        or not isinstance(document.get("exact_fingerprint_required"), bool)
        or isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or float(threshold) <= 0
        or float(threshold) > 1
        or not isinstance(metric_rules, list)
        or not metric_rules
        or invalid_rules
        or missing_metric_rules
        or unexpected_metric_rules
        or positive_weight_count == 0
        or hard_fail_count == 0
    ):
        _fail(
            "ADAPTER_QUALIFICATION_POLICY_INVALID",
            "The host qualification policy is incomplete, weak, or references unavailable metrics.",
            details={
                "missing": missing,
                "unexpected": unexpected,
                "invalid_metric_rules": invalid_rules,
                "missing_metric_rules": missing_metric_rules,
                "unexpected_metric_rules": unexpected_metric_rules,
                "available_metrics": list(available_metrics),
                "minimum_aggregate_score": document.get("minimum_aggregate_score"),
                "positive_weight_rule_count": positive_weight_count,
                "hard_fail_rule_count": hard_fail_count,
            },
            next_action="Approve schema_version 2 with one typed tolerance/weight/hard-fail rule for every available metric.",
        )
    document["metric_rules"] = sorted(
        normalized_rules, key=lambda rule: rule["metric"]
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
