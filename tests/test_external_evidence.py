from __future__ import annotations

import hashlib
import json

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.external_evidence import (
    ExternalEvidenceAdapterRegistry,
    JsonExternalEvidenceAdapter,
    external_evidence_contract,
    evaluate_signoff_policy,
    verify_external_report,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_inputs(
    tmp_path,
    *,
    status="passed",
    violations=0,
    mismatches=0,
    layout_hash=None,
    schema_version=1,
    disposition_summary=None,
):
    layout = tmp_path / "final.gds"
    layout.write_bytes(b"semantic-layout-fixture")
    report_root = tmp_path / "reports"
    report_root.mkdir()
    report = {
        "schema_version": schema_version,
        "kind": "drc",
        "status": status,
        "engine": {"name": "fixture-drc", "version": "1"},
        "deck_sha256": "a" * 64,
        "input_layout_sha256": layout_hash or _sha(layout.read_bytes()),
        "violation_count": violations,
        "mismatch_count": mismatches,
        "generated_at": "2026-08-31T00:00:00Z",
        "invocation_sha256": "b" * 64,
    }
    if schema_version == 2:
        default_disposition = {
            "total_marker_count": violations,
            "accepted_reference_precedent_count": violations,
            "approved_waiver_count": 0,
            "unresolved_count": 0,
            "classifier_trusted": True,
            "classifier_authority_reference": "fixture-classifier-receipt",
            "disposition_manifest_sha256": "d" * 64,
            "disposition_input_layout_sha256": layout_hash or _sha(layout.read_bytes()),
            "disposition_deck_sha256": "a" * 64,
            "itemized_marker_count": violations,
            "unique_marker_count": violations,
        }
        if disposition_summary:
            default_disposition.update(disposition_summary)
        report["marker_disposition_summary"] = default_disposition
    (report_root / "drc.json").write_text(json.dumps(report), encoding="utf-8")
    return layout, report_root


class _FixtureVerifiedDispositionAdapter(JsonExternalEvidenceAdapter):
    """Stand in for a host adapter that actually hashes the disposition manifest."""

    def normalize(self, *, report_path: str, report_sha256: str):
        normalized = dict(
            super().normalize(
                report_path=report_path,
                report_sha256=report_sha256,
            )
        )
        summary = dict(normalized["marker_disposition_summary"])
        summary["disposition_manifest_hash_verified"] = True
        normalized["marker_disposition_summary"] = summary
        return normalized


def _registry(*, trusted=True, mock=False, production=False, dispositions=False):
    registry = ExternalEvidenceAdapterRegistry(production_mode=production)
    registry.register(
        (_FixtureVerifiedDispositionAdapter if dispositions else JsonExternalEvidenceAdapter)(
            adapter_id="fixture-json-adapter" if not mock else "mock-json-adapter",
            adapter_version="1",
            trusted=trusted,
            is_mock=mock,
        )
    )
    return registry


def test_trusted_report_recomputes_report_and_layout_hashes(tmp_path):
    layout, report_root = _write_inputs(tmp_path)
    result = verify_external_report(
        adapter_registry=_registry(),
        adapter_id="fixture-json-adapter",
        report_root=report_root,
        report_name="drc.json",
        generated_layout_path=layout,
        expected_kind="drc",
    )

    assert result["adapter_contract_verified"] is True
    assert result["report_file_hash_verified"] is True
    assert result["input_layout_file_hash_verified"] is True
    assert result["external_evidence_provenance_verified"] is True
    assert result["eligible_for_external_evidence_state"] is True
    assert result["eligible_for_signoff_state"] is False


def test_mock_adapter_cannot_reach_external_evidence_state(tmp_path):
    layout, report_root = _write_inputs(tmp_path)
    result = verify_external_report(
        adapter_registry=_registry(mock=True),
        adapter_id="mock-json-adapter",
        report_root=report_root,
        report_name="drc.json",
        generated_layout_path=layout,
        expected_kind="drc",
    )

    assert result["adapter_contract_verified"] is True
    assert result["external_evidence_is_mock"] is True
    assert result["external_evidence_provenance_verified"] is False
    assert result["eligible_for_external_evidence_state"] is False


def test_production_registry_rejects_mock_adapter():
    with pytest.raises(AnalysisError) as caught:
        _registry(mock=True, production=True)

    assert caught.value.code == "NONPRODUCTION_EXTERNAL_ADAPTER_FORBIDDEN"


def test_registry_freezes_after_first_resolve():
    registry = _registry()
    registry.resolve("fixture-json-adapter")

    with pytest.raises(AnalysisError) as caught:
        registry.register(
            JsonExternalEvidenceAdapter(
                adapter_id="late-adapter",
                adapter_version="1",
                trusted=True,
            )
        )

    assert caught.value.code == "EXTERNAL_EVIDENCE_REGISTRY_FROZEN"


def test_stale_report_layout_hash_is_rejected(tmp_path):
    layout, report_root = _write_inputs(tmp_path, layout_hash="c" * 64)

    with pytest.raises(AnalysisError) as caught:
        verify_external_report(
            adapter_registry=_registry(),
            adapter_id="fixture-json-adapter",
            report_root=report_root,
            report_name="drc.json",
            generated_layout_path=layout,
            expected_kind="drc",
        )

    assert caught.value.code == "STALE_EXTERNAL_EVIDENCE_LAYOUT_HASH"


@pytest.mark.parametrize(
    ("status", "violations", "mismatches"),
    [("failed", 0, 0), ("passed", 1, 0), ("passed", 0, 1)],
)
def test_nonclean_report_cannot_progress(tmp_path, status, violations, mismatches):
    layout, report_root = _write_inputs(
        tmp_path,
        status=status,
        violations=violations,
        mismatches=mismatches,
    )

    with pytest.raises(AnalysisError) as caught:
        verify_external_report(
            adapter_registry=_registry(),
            adapter_id="fixture-json-adapter",
            report_root=report_root,
            report_name="drc.json",
            generated_layout_path=layout,
            expected_kind="drc",
        )

    assert caught.value.code == "EXTERNAL_EVIDENCE_NOT_CLEAN"


def test_v2_drc_with_trusted_complete_dispositions_can_progress(tmp_path):
    layout, report_root = _write_inputs(
        tmp_path,
        status="completed",
        schema_version=2,
        violations=3,
        disposition_summary={
            "total_marker_count": 3,
            "accepted_reference_precedent_count": 2,
            "approved_waiver_count": 1,
            "unresolved_count": 0,
        },
    )

    result = verify_external_report(
        adapter_registry=_registry(dispositions=True),
        adapter_id="fixture-json-adapter",
        report_root=report_root,
        report_name="drc.json",
        generated_layout_path=layout,
        expected_kind="drc",
    )

    assert result["marker_dispositions_complete"] is True
    assert result["accepted_violation_count"] == 3
    assert result["eligibility_basis"] == "trusted_complete_marker_dispositions"
    assert result["production_ready"] is False


@pytest.mark.parametrize(
    "summary",
    [
        {
            "total_marker_count": 3,
            "accepted_reference_precedent_count": 2,
            "approved_waiver_count": 0,
            "unresolved_count": 1,
        },
        {
            "total_marker_count": 3,
            "accepted_reference_precedent_count": 3,
            "approved_waiver_count": 0,
            "unresolved_count": 0,
            "classifier_trusted": False,
        },
    ],
)
def test_v2_unresolved_or_untrusted_dispositions_cannot_progress(tmp_path, summary):
    layout, report_root = _write_inputs(
        tmp_path,
        schema_version=2,
        violations=3,
        disposition_summary=summary,
    )

    with pytest.raises(AnalysisError) as caught:
        verify_external_report(
            adapter_registry=_registry(dispositions=True),
            adapter_id="fixture-json-adapter",
            report_root=report_root,
            report_name="drc.json",
            generated_layout_path=layout,
            expected_kind="drc",
        )

    assert caught.value.code == "EXTERNAL_EVIDENCE_DISPOSITION_INCOMPLETE"


def test_generic_json_adapter_cannot_self_assert_disposition_manifest_hash(tmp_path):
    layout, report_root = _write_inputs(tmp_path, schema_version=2, violations=1)

    with pytest.raises(AnalysisError) as caught:
        verify_external_report(
            adapter_registry=_registry(),
            adapter_id="fixture-json-adapter",
            report_root=report_root,
            report_name="drc.json",
            generated_layout_path=layout,
            expected_kind="drc",
        )

    assert caught.value.code == "EXTERNAL_EVIDENCE_DISPOSITION_INCOMPLETE"


def test_v2_disposition_manifest_must_cross_bind_layout_deck_and_unique_markers(tmp_path):
    layout, report_root = _write_inputs(
        tmp_path,
        schema_version=2,
        violations=2,
        disposition_summary={
            "disposition_input_layout_sha256": "e" * 64,
            "unique_marker_count": 1,
        },
    )

    with pytest.raises(AnalysisError) as caught:
        verify_external_report(
            adapter_registry=_registry(dispositions=True),
            adapter_id="fixture-json-adapter",
            report_root=report_root,
            report_name="drc.json",
            generated_layout_path=layout,
            expected_kind="drc",
        )

    assert caught.value.code == "EXTERNAL_EVIDENCE_DISPOSITION_BINDING_MISMATCH"


def test_report_name_cannot_escape_host_root(tmp_path):
    layout, report_root = _write_inputs(tmp_path)

    with pytest.raises(AnalysisError) as caught:
        verify_external_report(
            adapter_registry=_registry(),
            adapter_id="fixture-json-adapter",
            report_root=report_root,
            report_name="../drc.json",
            generated_layout_path=layout,
            expected_kind="drc",
        )

    assert caught.value.code == "INVALID_EXTERNAL_REPORT_NAME"


def test_external_evidence_contract_never_promotes_mock_or_signoff():
    contract = external_evidence_contract()

    assert contract["contract_version"] == 2
    assert contract["supported_report_schema_versions"] == [1, 2]
    assert contract["mock_adapter_can_reach_external_evidence_state"] is False
    assert contract["mock_evidence_can_reach_signoff"] is False
    assert contract["v1_nonzero_markers_can_progress"] is False
    assert contract["unvalidated_reference_similarity_blocks_drawing"] is False
    assert contract["unvalidated_reference_similarity_can_promote_production"] is False
    assert contract["signoff_policy_default"] == "fail_closed_unavailable"


def test_signoff_policy_is_fail_closed_when_unconfigured(tmp_path):
    layout, report_root = _write_inputs(tmp_path)
    verified = verify_external_report(
        adapter_registry=_registry(),
        adapter_id="fixture-json-adapter",
        report_root=report_root,
        report_name="drc.json",
        generated_layout_path=layout,
        expected_kind="drc",
    )

    with pytest.raises(AnalysisError) as caught:
        evaluate_signoff_policy(verified_evidence=[verified], policy=None)

    assert caught.value.code == "SIGNOFF_POLICY_UNAVAILABLE"


def test_mock_evidence_is_rejected_before_signoff_policy(tmp_path):
    layout, report_root = _write_inputs(tmp_path)
    verified = verify_external_report(
        adapter_registry=_registry(mock=True),
        adapter_id="mock-json-adapter",
        report_root=report_root,
        report_name="drc.json",
        generated_layout_path=layout,
        expected_kind="drc",
    )

    with pytest.raises(AnalysisError) as caught:
        evaluate_signoff_policy(verified_evidence=[verified], policy=None)

    assert caught.value.code == "SIGNOFF_EVIDENCE_NOT_ELIGIBLE"
