"""Evidence-backed workflow states for end-to-end TEG drawing."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import AnalysisError


EVIDENCE_STATE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "state": "intent_draft_complete",
        "meaning": "The canonical draft is complete, but it is not necessarily user-approved.",
        "requirements": {
            "draft_schema_valid": True,
            "unresolved_questions_zero": True,
        },
    },
    {
        "state": "intent_approved",
        "meaning": "A trusted client verified approval of the exact canonical draft.",
        "requirements": {
            "approval_backend_trusted": True,
            "approval_verified": True,
        },
    },
    {
        "state": "plan_complete",
        "meaning": "Primitive, placement, Pad budget, routing, and drawing plans are fixed.",
        "requirements": {
            "plan_fingerprint_verified": True,
            "routing_plan_complete": True,
        },
    },
    {
        "state": "generation_staged",
        "meaning": (
            "A verified stream file and generation result are durably staged so final "
            "promotion can resume without rerunning the generator."
        ),
        "requirements": {
            "staged_layout_hash_verified": True,
            "generation_result_persisted": True,
        },
    },
    {
        "state": "drawing_complete",
        "meaning": "The atomic layout was fresh-loaded and matched the planned geometry.",
        "requirements": {
            "fresh_reload_verified": True,
            "drawing_fingerprint_verified": True,
        },
    },
    {
        "state": "connectivity_projected",
        "meaning": "Labeled layout geometry passed projected open/short checks; this is not LVS.",
        "requirements": {
            "connectivity_projection_verified": True,
        },
    },
    {
        "state": "measurement_package_complete",
        "meaning": "A validated measurement manifest is bound to the exact generated layout.",
        "requirements": {
            "measurement_manifest_verified": True,
            "measurement_layout_hash_match": True,
        },
    },
    {
        "state": "external_evidence_attached",
        "meaning": "External verification evidence with matching provenance is attached.",
        "requirements": {
            "external_evidence_provenance_verified": True,
        },
    },
    {
        "state": "signoff_evidence_approved",
        "meaning": "A trusted organizational policy approved real, non-mock sign-off evidence.",
        "requirements": {
            "external_evidence_is_mock": False,
            "signoff_approval_reference_present": True,
            "signoff_policy_approved": True,
        },
    },
)

EVIDENCE_STATES = tuple(item["state"] for item in EVIDENCE_STATE_DEFINITIONS)


def evidence_ladder_contract() -> dict[str, Any]:
    """Return the immutable public contract without claiming any state is attained."""

    return {
        "contract_version": 1,
        "ordered_states": list(EVIDENCE_STATES),
        "states": [
            {
                "state": item["state"],
                "meaning": item["meaning"],
                "required_evidence": dict(item["requirements"]),
            }
            for item in EVIDENCE_STATE_DEFINITIONS
        ],
        "sequential_attainment_required": True,
        "mock_evidence_can_reach_signoff": False,
        "connectivity_projection_is_lvs": False,
        "measurement_package_complete_means_instrument_program_ready": False,
        "measurement_package_complete_means_silicon_measurement_complete": False,
        "readiness_dimensions": {
            "geometry_verified": "drawing_complete",
            "connectivity_projection_verified": "connectivity_projected",
            "layout_signoff_evidence_approved": "signoff_evidence_approved",
            "measurement_program_ready": "outside_current_evidence_ladder",
            "silicon_correlation_ready": "outside_current_evidence_ladder",
            "pcm_release_ready": "outside_current_evidence_ladder",
        },
        "production_ready_requires": "outside_current_evidence_ladder",
        "signoff_evidence_approved_means_production_ready": False,
    }


def evaluate_evidence_ladder(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate observed evidence separately from sequential workflow attainment."""

    if not isinstance(evidence, Mapping):
        raise AnalysisError(
            code="INVALID_EVIDENCE_STATE_INPUT",
            message="Workflow evidence must be a mapping.",
            details={"received_type": type(evidence).__name__},
            next_action="Provide explicit evidence fields without inferring missing values.",
        )

    records: list[dict[str, Any]] = []
    attained_states: list[str] = []
    observed_satisfied_states: list[str] = []
    previous_attained = True
    previous_state: str | None = None

    for definition in EVIDENCE_STATE_DEFINITIONS:
        state = definition["state"]
        requirements = definition["requirements"]
        missing_fields: list[str] = []
        mismatched_fields: list[dict[str, Any]] = []
        for field, expected in requirements.items():
            if field not in evidence:
                missing_fields.append(field)
            elif evidence[field] is not expected:
                mismatched_fields.append(
                    {"field": field, "expected": expected, "actual": evidence[field]}
                )

        evidence_satisfied = not missing_fields and not mismatched_fields
        if evidence_satisfied:
            observed_satisfied_states.append(state)
        attained = previous_attained and evidence_satisfied
        if attained:
            attained_states.append(state)

        records.append(
            {
                "state": state,
                "evidence_satisfied": evidence_satisfied,
                "attained": attained,
                "missing_fields": missing_fields,
                "mismatched_fields": mismatched_fields,
                "blocked_by_previous_state": (
                    previous_state if evidence_satisfied and not previous_attained else None
                ),
            }
        )
        previous_attained = attained
        previous_state = state

    highest = attained_states[-1] if attained_states else None
    next_state = (
        EVIDENCE_STATES[len(attained_states)]
        if len(attained_states) < len(EVIDENCE_STATES)
        else None
    )
    return {
        "contract_version": 1,
        "highest_attained_state": highest,
        "next_required_state": next_state,
        "attained_states": attained_states,
        "observed_satisfied_states": observed_satisfied_states,
        "observed_but_sequentially_blocked_states": [
            record["state"]
            for record in records
            if record["evidence_satisfied"] and not record["attained"]
        ],
        "states": records,
        "layout_signoff_evidence_approved": highest == "signoff_evidence_approved",
        "production_ready": False,
        "connectivity_projection_is_lvs": False,
    }


def require_evidence_state(
    evidence: Mapping[str, Any],
    *,
    claimed_state: str,
) -> dict[str, Any]:
    """Require one state without allowing skipped predecessors or mock sign-off."""

    if claimed_state not in EVIDENCE_STATES:
        raise AnalysisError(
            code="UNKNOWN_EVIDENCE_STATE",
            message="The requested evidence state is not part of the workflow contract.",
            details={"claimed_state": claimed_state, "allowed_states": list(EVIDENCE_STATES)},
            next_action="Use one ordered evidence state exactly.",
        )
    if claimed_state == "signoff_evidence_approved" and evidence.get(
        "external_evidence_is_mock"
    ) is True:
        raise AnalysisError(
            code="MOCK_SIGNOFF_EVIDENCE_FORBIDDEN",
            message="Mock verification evidence cannot create an approved sign-off state.",
            details={"claimed_state": claimed_state, "production_ready": False},
            next_action="Attach provenance-matched evidence from the trusted approval flow.",
        )

    report = evaluate_evidence_ladder(evidence)
    record = next(item for item in report["states"] if item["state"] == claimed_state)
    if not record["attained"]:
        raise AnalysisError(
            code="EVIDENCE_STATE_NOT_ATTAINED",
            message="The requested workflow state lacks evidence or skips a predecessor.",
            details={
                "claimed_state": claimed_state,
                "highest_attained_state": report["highest_attained_state"],
                "missing_fields": record["missing_fields"],
                "mismatched_fields": record["mismatched_fields"],
                "blocked_by_previous_state": record["blocked_by_previous_state"],
                "production_ready": False,
            },
            next_action="Supply trusted evidence for every preceding state in order.",
        )
    return report
