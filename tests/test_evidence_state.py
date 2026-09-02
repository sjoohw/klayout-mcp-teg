from __future__ import annotations

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.evidence_state import (
    EVIDENCE_STATES,
    evaluate_evidence_ladder,
    evidence_ladder_contract,
    require_evidence_state,
)


def _complete_evidence() -> dict[str, bool]:
    return {
        "draft_schema_valid": True,
        "unresolved_questions_zero": True,
        "approval_backend_trusted": True,
        "approval_verified": True,
        "plan_fingerprint_verified": True,
        "routing_plan_complete": True,
        "staged_layout_hash_verified": True,
        "generation_result_persisted": True,
        "fresh_reload_verified": True,
        "drawing_fingerprint_verified": True,
        "connectivity_projection_verified": True,
        "measurement_manifest_verified": True,
        "measurement_layout_hash_match": True,
        "external_evidence_provenance_verified": True,
        "external_evidence_is_mock": False,
        "signoff_approval_reference_present": True,
        "signoff_policy_approved": True,
    }


def test_contract_exposes_order_and_claim_boundaries() -> None:
    contract = evidence_ladder_contract()

    assert contract["ordered_states"] == list(EVIDENCE_STATES)
    assert contract["sequential_attainment_required"] is True
    assert contract["mock_evidence_can_reach_signoff"] is False
    assert contract["connectivity_projection_is_lvs"] is False
    assert contract[
        "measurement_package_complete_means_instrument_program_ready"
    ] is False
    assert contract[
        "measurement_package_complete_means_silicon_measurement_complete"
    ] is False
    assert (
        contract["readiness_dimensions"]["pcm_release_ready"]
        == "outside_current_evidence_ladder"
    )


def test_observed_drawing_evidence_does_not_skip_missing_approval() -> None:
    evidence = {
        "draft_schema_valid": True,
        "unresolved_questions_zero": True,
        "plan_fingerprint_verified": True,
        "routing_plan_complete": True,
        "staged_layout_hash_verified": True,
        "generation_result_persisted": True,
        "fresh_reload_verified": True,
        "drawing_fingerprint_verified": True,
        "connectivity_projection_verified": True,
    }

    report = evaluate_evidence_ladder(evidence)

    assert report["highest_attained_state"] == "intent_draft_complete"
    assert report["production_ready"] is False
    assert "drawing_complete" in report["observed_satisfied_states"]
    assert "drawing_complete" in report["observed_but_sequentially_blocked_states"]


def test_sequential_evidence_reaches_drawing_but_not_measurement_or_signoff() -> None:
    evidence = _complete_evidence()
    evidence["measurement_manifest_verified"] = False

    report = evaluate_evidence_ladder(evidence)

    assert report["highest_attained_state"] == "connectivity_projected"
    assert report["next_required_state"] == "measurement_package_complete"
    assert report["production_ready"] is False


def test_mock_evidence_cannot_be_approved_as_signoff() -> None:
    evidence = _complete_evidence()
    evidence["external_evidence_is_mock"] = True

    report = evaluate_evidence_ladder(evidence)
    assert report["highest_attained_state"] == "external_evidence_attached"
    assert report["production_ready"] is False

    with pytest.raises(AnalysisError) as caught:
        require_evidence_state(evidence, claimed_state="signoff_evidence_approved")
    assert caught.value.code == "MOCK_SIGNOFF_EVIDENCE_FORBIDDEN"


def test_complete_real_evidence_is_layout_signoff_not_production_ready() -> None:
    report = require_evidence_state(
        _complete_evidence(), claimed_state="signoff_evidence_approved"
    )

    assert report["highest_attained_state"] == "signoff_evidence_approved"
    assert report["layout_signoff_evidence_approved"] is True
    assert report["production_ready"] is False


def test_skipped_state_claim_is_rejected() -> None:
    with pytest.raises(AnalysisError) as caught:
        require_evidence_state(
            {
                "draft_schema_valid": True,
                "unresolved_questions_zero": True,
                "plan_fingerprint_verified": True,
                "routing_plan_complete": True,
            },
            claimed_state="plan_complete",
        )

    assert caught.value.code == "EVIDENCE_STATE_NOT_ATTAINED"
    assert caught.value.details["highest_attained_state"] == "intent_draft_complete"


def test_unknown_state_is_rejected() -> None:
    with pytest.raises(AnalysisError) as caught:
        require_evidence_state({}, claimed_state="fabrication_ready")

    assert caught.value.code == "UNKNOWN_EVIDENCE_STATE"
