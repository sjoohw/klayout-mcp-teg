"""Fail-closed normalization of external DRC/LVS/PEX report evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import AnalysisError
from .workflow_manifest import SHA256_PATTERN, canonical_sha256, immutable_json_copy


EVIDENCE_KINDS = ("drc", "lvs", "pex")
EVIDENCE_STATUSES = ("passed", "completed", "failed", "error")
RAW_MARKER_DISPOSITION_FIELDS = {
    "total_marker_count",
    "accepted_reference_precedent_count",
    "approved_waiver_count",
    "unresolved_count",
    "classifier_trusted",
    "classifier_authority_reference",
    "disposition_manifest_sha256",
    "disposition_input_layout_sha256",
    "disposition_deck_sha256",
    "itemized_marker_count",
    "unique_marker_count",
}
MARKER_DISPOSITION_FIELDS = RAW_MARKER_DISPOSITION_FIELDS | {
    "disposition_manifest_hash_verified"
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
NONPRODUCTION_MARKERS = ("test", "mock", "fake", "stub")


def _fail(code: str, message: str, *, details: Mapping[str, Any]) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**dict(details), "production_ready": False},
        next_action="Use provenance-matched external evidence from a host-approved adapter.",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(
            "EXTERNAL_EVIDENCE_FILE_READ_FAILED",
            "An external evidence artifact could not be read.",
            details={"path": str(path), "error_type": type(exc).__name__},
        )
    return digest.hexdigest()


@runtime_checkable
class ExternalEvidenceAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    trusted: bool
    is_mock: bool

    def normalize(self, *, report_path: str, report_sha256: str) -> Mapping[str, Any]: ...


class ExternalEvidenceAdapterRegistry:
    """Host-only adapter registry; callers may select only an existing identifier."""

    def __init__(self, *, production_mode: bool = True) -> None:
        self.production_mode = production_mode
        self._adapters: dict[str, ExternalEvidenceAdapter] = {}
        self._frozen = False

    def register(self, adapter: ExternalEvidenceAdapter) -> None:
        if self._frozen:
            _fail(
                "EXTERNAL_EVIDENCE_REGISTRY_FROZEN",
                "The host adapter registry cannot change after verification begins.",
                details={},
            )
        if not isinstance(adapter, ExternalEvidenceAdapter):
            _fail(
                "INVALID_EXTERNAL_EVIDENCE_ADAPTER",
                "The host adapter does not implement the evidence contract.",
                details={"adapter_type": type(adapter).__name__},
            )
        identity = " ".join(
            (
                adapter.adapter_id,
                type(adapter).__name__,
                type(adapter).__module__,
            )
        ).lower()
        if self.production_mode and (
            adapter.is_mock is True
            or any(marker in identity for marker in NONPRODUCTION_MARKERS)
        ):
            _fail(
                "NONPRODUCTION_EXTERNAL_ADAPTER_FORBIDDEN",
                "Production mode rejects test, mock, fake, and stub evidence adapters.",
                details={"adapter_id": adapter.adapter_id},
            )
        if adapter.adapter_id in self._adapters:
            _fail(
                "DUPLICATE_EXTERNAL_EVIDENCE_ADAPTER",
                "An adapter id can be registered only once.",
                details={"adapter_id": adapter.adapter_id},
            )
        self._adapters[adapter.adapter_id] = adapter

    def resolve(self, adapter_id: str) -> ExternalEvidenceAdapter:
        self._frozen = True
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            _fail(
                "EXTERNAL_EVIDENCE_ADAPTER_UNAVAILABLE",
                "The requested adapter is not configured by this host.",
                details={
                    "adapter_id": adapter_id,
                    "registered_adapter_ids": sorted(self._adapters),
                },
            )
        return adapter


class JsonExternalEvidenceAdapter:
    """Strict parser for the common schema; trust is a host deployment decision."""

    def __init__(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        trusted: bool,
        is_mock: bool = False,
    ) -> None:
        self.adapter_id = adapter_id
        self.adapter_version = adapter_version
        self.trusted = trusted
        self.is_mock = is_mock

    def normalize(self, *, report_path: str, report_sha256: str) -> Mapping[str, Any]:
        try:
            raw = json.loads(Path(report_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(
                "EXTERNAL_REPORT_PARSE_FAILED",
                "The adapter could not parse its external JSON report.",
                details={"error_type": type(exc).__name__},
            )
        if not isinstance(raw, Mapping):
            _fail(
                "INVALID_EXTERNAL_REPORT",
                "An external report must be one JSON object.",
                details={},
            )
        required = {
            "schema_version",
            "kind",
            "status",
            "engine",
            "deck_sha256",
            "input_layout_sha256",
            "violation_count",
            "mismatch_count",
            "generated_at",
            "invocation_sha256",
        }
        schema_version = raw.get("schema_version")
        expected = (
            required
            if schema_version == 1
            else required | {"marker_disposition_summary"}
            if schema_version == 2
            else set()
        )
        if set(raw) != expected:
            _fail(
                "EXTERNAL_REPORT_SCHEMA_MISMATCH",
                "The external report does not match common schema version 1 or 2.",
                details={
                    "schema_version": schema_version,
                    "supported_schema_versions": [1, 2],
                    "missing": sorted(expected.difference(raw)),
                    "unexpected": sorted(set(raw).difference(expected)),
                },
            )
        normalized = immutable_json_copy(raw)
        if schema_version == 2:
            normalized["marker_disposition_summary"] = {
                **normalized["marker_disposition_summary"],
                # The generic JSON parser has no authority to fetch and hash the
                # disposition manifest. A process-specific trusted adapter must do so.
                "disposition_manifest_hash_verified": False,
            }
        return {
            **normalized,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "adapter_trusted": self.trusted,
            "external_evidence_is_mock": self.is_mock,
            "report_sha256": report_sha256,
            "report_reference": str(Path(report_path).resolve()),
        }


def verify_external_report(
    *,
    adapter_registry: ExternalEvidenceAdapterRegistry,
    adapter_id: str,
    report_root: str | Path,
    report_name: str,
    generated_layout_path: str | Path,
    expected_kind: str,
) -> dict[str, Any]:
    """Normalize one report and bind it to the freshly hashed generated layout."""

    if expected_kind not in EVIDENCE_KINDS:
        _fail(
            "UNSUPPORTED_EXTERNAL_EVIDENCE_KIND",
            "External evidence kind must be drc, lvs, or pex.",
            details={"expected_kind": expected_kind},
        )
    if not isinstance(report_name, str) or not SAFE_NAME.fullmatch(report_name):
        _fail(
            "INVALID_EXTERNAL_REPORT_NAME",
            "External report references must be safe basenames under the host report root.",
            details={"report_name": report_name},
        )
    root = Path(report_root).resolve()
    report_path = (root / report_name).resolve()
    try:
        report_path.relative_to(root)
    except ValueError:
        _fail(
            "EXTERNAL_REPORT_OUTSIDE_HOST_ROOT",
            "The external report escaped its host-controlled root.",
            details={"report_root": str(root), "report_path": str(report_path)},
        )
    layout_path = Path(generated_layout_path).resolve()
    if not report_path.is_file() or not layout_path.is_file():
        _fail(
            "EXTERNAL_EVIDENCE_INPUT_MISSING",
            "The report and generated layout must both exist.",
            details={"report_path": str(report_path), "layout_path": str(layout_path)},
        )
    report_sha256 = _file_sha256(report_path)
    layout_sha256 = _file_sha256(layout_path)
    adapter = adapter_registry.resolve(adapter_id)
    normalized = adapter.normalize(
        report_path=str(report_path), report_sha256=report_sha256
    )
    if _file_sha256(report_path) != report_sha256:
        _fail(
            "EXTERNAL_REPORT_CHANGED_DURING_VERIFICATION",
            "The external report changed while it was being normalized.",
            details={"report_path": str(report_path)},
        )
    if not isinstance(normalized, Mapping):
        _fail(
            "INVALID_EXTERNAL_EVIDENCE_ADAPTER_RESULT",
            "The adapter returned a non-object result.",
            details={"adapter_id": adapter_id},
        )
    evidence = immutable_json_copy(normalized)
    engine = evidence.get("engine")
    hashes = {
        field: evidence.get(field)
        for field in (
            "deck_sha256",
            "input_layout_sha256",
            "invocation_sha256",
            "report_sha256",
        )
    }
    invalid_hashes = [
        field
        for field, value in hashes.items()
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value)
    ]
    counts_valid = all(
        isinstance(evidence.get(field), int)
        and not isinstance(evidence.get(field), bool)
        and evidence[field] >= 0
        for field in ("violation_count", "mismatch_count")
    )
    schema_version = evidence.get("schema_version")
    disposition = evidence.get("marker_disposition_summary")
    disposition_valid = schema_version == 1 and disposition is None
    if schema_version == 2 and isinstance(disposition, Mapping):
        disposition_counts_valid = all(
            isinstance(disposition.get(field), int)
            and not isinstance(disposition.get(field), bool)
            and disposition[field] >= 0
            for field in (
                "total_marker_count",
                "accepted_reference_precedent_count",
                "approved_waiver_count",
                "unresolved_count",
                "itemized_marker_count",
                "unique_marker_count",
            )
        )
        disposition_valid = (
            set(disposition) == MARKER_DISPOSITION_FIELDS
            and disposition_counts_valid
            and isinstance(disposition.get("classifier_trusted"), bool)
            and isinstance(disposition.get("classifier_authority_reference"), str)
            and bool(disposition["classifier_authority_reference"].strip())
            and isinstance(
                disposition.get("disposition_manifest_hash_verified"), bool
            )
            and all(
                isinstance(disposition.get(field), str)
                and SHA256_PATTERN.fullmatch(disposition[field]) is not None
                for field in (
                    "disposition_manifest_sha256",
                    "disposition_input_layout_sha256",
                    "disposition_deck_sha256",
                )
            )
        )
    if (
        schema_version not in {1, 2}
        or evidence.get("kind") not in EVIDENCE_KINDS
        or evidence.get("status") not in EVIDENCE_STATUSES
        or not isinstance(engine, Mapping)
        or set(engine) != {"name", "version"}
        or any(not isinstance(engine[field], str) or not engine[field].strip() for field in engine)
        or invalid_hashes
        or not counts_valid
        or not disposition_valid
        or evidence.get("adapter_id") != adapter.adapter_id
        or evidence.get("adapter_version") != adapter.adapter_version
        or evidence.get("report_sha256") != report_sha256
    ):
        _fail(
            "INVALID_EXTERNAL_EVIDENCE_ADAPTER_RESULT",
            "The normalized external evidence is incomplete or inconsistent.",
            details={"adapter_id": adapter_id, "invalid_hash_fields": invalid_hashes},
        )
    if evidence["kind"] != expected_kind:
        _fail(
            "EXTERNAL_EVIDENCE_KIND_MISMATCH",
            "The parsed report kind differs from the requested evidence kind.",
            details={"expected": expected_kind, "actual": evidence["kind"]},
        )
    if evidence["input_layout_sha256"] != layout_sha256:
        _fail(
            "STALE_EXTERNAL_EVIDENCE_LAYOUT_HASH",
            "The external report was produced for another layout file.",
            details={
                "expected": layout_sha256,
                "actual": evidence["input_layout_sha256"],
            },
        )
    if _file_sha256(layout_path) != layout_sha256:
        _fail(
            "EXTERNAL_LAYOUT_CHANGED_DURING_VERIFICATION",
            "The generated layout changed while external evidence was being verified.",
            details={"layout_path": str(layout_path)},
        )
    execution_completed = evidence["status"] == "passed" or (
        schema_version == 2 and evidence["status"] == "completed"
    )
    zero_clean = (
        execution_completed
        and evidence["violation_count"] == 0
        and evidence["mismatch_count"] == 0
    )
    trusted_dispositions = False
    if schema_version == 2:
        assert isinstance(disposition, Mapping)
        total = disposition["total_marker_count"]
        accepted = disposition["accepted_reference_precedent_count"]
        waived = disposition["approved_waiver_count"]
        unresolved = disposition["unresolved_count"]
        disposition_counts_complete = accepted + waived + unresolved == total
        disposition_matches_report = total == evidence["violation_count"]
        disposition_cross_bound = (
            disposition["disposition_input_layout_sha256"]
            == evidence["input_layout_sha256"]
            and disposition["disposition_deck_sha256"] == evidence["deck_sha256"]
        )
        disposition_items_complete = (
            disposition["itemized_marker_count"] == total
            and disposition["unique_marker_count"] == total
        )
        trusted_dispositions = (
            evidence["kind"] == "drc"
            and execution_completed
            and evidence["mismatch_count"] == 0
            and disposition_matches_report
            and disposition_counts_complete
            and disposition_cross_bound
            and disposition_items_complete
            and unresolved == 0
            and accepted + waived == total
            and disposition["classifier_trusted"] is True
            and disposition["disposition_manifest_hash_verified"] is True
        )
        if (
            not disposition_counts_complete
            or not disposition_matches_report
            or not disposition_cross_bound
            or not disposition_items_complete
        ):
            _fail(
                "EXTERNAL_EVIDENCE_DISPOSITION_BINDING_MISMATCH",
                "The disposition manifest must bind this layout/deck and account for every unique DRC marker exactly once.",
                details={
                    "violation_count": evidence["violation_count"],
                    "input_layout_sha256": evidence["input_layout_sha256"],
                    "deck_sha256": evidence["deck_sha256"],
                    "marker_disposition_summary": disposition,
                },
            )
    passed = zero_clean or trusted_dispositions
    if not passed:
        if schema_version == 2 and evidence["kind"] == "drc":
            _fail(
                "EXTERNAL_EVIDENCE_DISPOSITION_INCOMPLETE",
                "Every DRC marker must have one trusted reference-precedent or approved-waiver disposition.",
                details={
                    "status": evidence["status"],
                    "violation_count": evidence["violation_count"],
                    "mismatch_count": evidence["mismatch_count"],
                    "marker_disposition_summary": disposition,
                },
            )
        _fail(
            "EXTERNAL_EVIDENCE_NOT_CLEAN",
            "External evidence contains a failure, error, violation, or mismatch.",
            details={
                "kind": evidence["kind"],
                "status": evidence["status"],
                "violation_count": evidence["violation_count"],
                "mismatch_count": evidence["mismatch_count"],
            },
        )
    provenance_verified = (
        adapter.trusted is True
        and adapter.is_mock is False
        and evidence.get("adapter_trusted") is True
        and evidence.get("external_evidence_is_mock") is False
    )
    evidence_sha256 = canonical_sha256(evidence)
    return {
        "ok": True,
        "contract_version": 1,
        "evidence": evidence,
        "evidence_sha256": evidence_sha256,
        "adapter_contract_verified": True,
        "report_file_hash_verified": True,
        "input_layout_file_hash_verified": True,
        "external_evidence_provenance_verified": provenance_verified,
        "external_evidence_is_mock": adapter.is_mock,
        "marker_dispositions_complete": trusted_dispositions,
        "accepted_violation_count": (
            evidence["violation_count"] if trusted_dispositions else 0
        ),
        "eligibility_basis": (
            "trusted_complete_marker_dispositions"
            if trusted_dispositions
            else "zero_clean_report"
        ),
        "eligible_for_external_evidence_state": provenance_verified,
        "eligible_for_signoff_state": False,
        "production_ready": False,
    }


@runtime_checkable
class SignoffPolicy(Protocol):
    policy_id: str
    policy_version: str
    required_evidence_kinds: tuple[str, ...]
    trusted: bool

    def approve(self, *, evidence_documents: list[Mapping[str, Any]]) -> Mapping[str, Any]: ...


def evaluate_signoff_policy(
    *,
    verified_evidence: list[Mapping[str, Any]],
    policy: SignoffPolicy | None,
) -> dict[str, Any]:
    """Invoke an organizational policy hook; no bundled policy grants signoff."""

    if not verified_evidence or any(
        item.get("external_evidence_provenance_verified") is not True
        or item.get("external_evidence_is_mock") is not False
        for item in verified_evidence
    ):
        _fail(
            "SIGNOFF_EVIDENCE_NOT_ELIGIBLE",
            "Signoff requires non-mock, provenance-verified external evidence.",
            details={"evidence_count": len(verified_evidence)},
        )
    if policy is None:
        _fail(
            "SIGNOFF_POLICY_UNAVAILABLE",
            "No trusted organizational signoff policy is configured.",
            details={"policy_configured": False},
        )
    if (
        not isinstance(policy, SignoffPolicy)
        or policy.trusted is not True
        or not isinstance(policy.policy_id, str)
        or not policy.policy_id.strip()
        or not isinstance(policy.policy_version, str)
        or not policy.policy_version.strip()
        or not isinstance(policy.required_evidence_kinds, tuple)
        or not policy.required_evidence_kinds
        or len(set(policy.required_evidence_kinds)) != len(policy.required_evidence_kinds)
        or any(kind not in EVIDENCE_KINDS for kind in policy.required_evidence_kinds)
    ):
        _fail(
            "UNTRUSTED_SIGNOFF_POLICY",
            "The configured signoff policy is not a trusted organizational authority.",
            details={"policy_type": type(policy).__name__},
        )
    actual_kinds = {str(item["evidence"].get("kind")) for item in verified_evidence}
    required_kinds = set(policy.required_evidence_kinds)
    if actual_kinds != required_kinds:
        _fail(
            "SIGNOFF_CURRENT_LAYOUT_EVIDENCE_INCOMPLETE",
            "Signoff requires the host policy's exact current-layout evidence set.",
            details={
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "required": sorted(required_kinds),
                "actual": sorted(actual_kinds),
            },
        )
    evidence_documents = [item["evidence"] for item in verified_evidence]
    result = policy.approve(evidence_documents=evidence_documents)
    if not isinstance(result, Mapping) or result.get("approved") is not True:
        _fail(
            "SIGNOFF_POLICY_NOT_APPROVED",
            "The organizational policy did not approve this exact evidence set.",
            details={"policy_id": policy.policy_id},
        )
    evidence_hashes = sorted(canonical_sha256(item) for item in evidence_documents)
    receipt_document = result.get("receipt_document")
    receipt_sha256 = result.get("receipt_sha256")
    if (
        result.get("policy_id") != policy.policy_id
        or result.get("policy_version") != policy.policy_version
        or result.get("evidence_sha256s") != evidence_hashes
        or result.get("external_evidence_is_mock") is not False
        or result.get("violations_reviewed") is not True
        or not isinstance(result.get("approval_reference"), str)
        or not result["approval_reference"].strip()
        or not isinstance(receipt_document, Mapping)
        or not isinstance(receipt_sha256, str)
        or not SHA256_PATTERN.fullmatch(receipt_sha256)
        or canonical_sha256(receipt_document) != receipt_sha256
    ):
        _fail(
            "INVALID_SIGNOFF_POLICY_RECEIPT",
            "The policy receipt does not bind the exact eligible evidence set.",
            details={"policy_id": policy.policy_id},
        )
    return {
        "ok": True,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "evidence_sha256s": evidence_hashes,
        "signoff_approval_reference_present": True,
        "signoff_policy_approved": True,
        "external_evidence_is_mock": False,
        "receipt_document": immutable_json_copy(receipt_document),
        "receipt_sha256": receipt_sha256,
        "layout_signoff_evidence_approved": True,
        "production_ready": False,
    }


def external_evidence_contract() -> dict[str, Any]:
    return {
        "contract_version": 2,
        "supported_report_schema_versions": [1, 2],
        "evidence_kinds": list(EVIDENCE_KINDS),
        "report_and_layout_file_hashes_recomputed": True,
        "adapter_registry_host_controlled": True,
        "adapter_registry_frozen_after_first_resolve": True,
        "report_and_layout_rechecked_during_verification": True,
        "model_can_register_or_import_adapter": False,
        "drc_lvs_pex_universally_required": False,
        "signoff_evidence_set_selected_by_host_policy": True,
        "mock_adapter_can_reach_external_evidence_state": False,
        "mock_evidence_can_reach_signoff": False,
        "v1_nonzero_markers_can_progress": False,
        "v2_nonzero_drc_requires": [
            "trusted_process_adapter",
            "verified_disposition_manifest_hash",
            "layout_and_deck_cross_binding",
            "one_unique_itemized_disposition_per_marker",
            "zero_unresolved_markers",
            "reference_precedent_or_approved_waiver_per_marker",
        ],
        "unvalidated_reference_similarity_blocks_drawing": False,
        "unvalidated_reference_similarity_can_promote_production": False,
        "signoff_policy_default": "fail_closed_unavailable",
        "measurement_package_is_external_signoff": False,
    }
