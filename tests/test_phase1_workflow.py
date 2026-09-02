from __future__ import annotations

import copy

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.phase1_layout import compose_phase1_direct_layout
from klayout_mcp.phase1_workflow import guide_phase1_direct_workflow


def _placement(primitive):
    return {"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}


def _route_plan(profile, request_plan):
    return {
        "ok": True,
        "process": profile["process"],
        "frame_um": request_plan["request"]["frame_um"],
        "pad_count": request_plan["request"]["pads"]["count"],
        "ready_for_direct_measurement_planner": True,
        "m1_feasibility_report": request_plan["routing_policy"]["m1_feasibility_report"],
    }


def test_workflow_starts_with_process_capability() -> None:
    result = guide_phase1_direct_workflow()

    assert result["workflow_status"] == "action_required"
    assert result["current_stage"] == "process_capability"
    assert result["next_tool"] == "describe_pdk_profile_inputs"
    assert result["next_tool_input_template"] == {
        "profile": "<target schema-v1 process capability>"
    }


def test_workflow_identifies_missing_primitive_without_test_module_import(
    ready_phase1_inputs,
) -> None:
    profile, _primitive, request_plan = ready_phase1_inputs

    result = guide_phase1_direct_workflow(
        process_capability=profile,
        intake_plan=request_plan,
    )

    assert result["current_stage"] == "primitive_geometry"
    assert result["next_tool"] == "plan_metal_resistor_primitive"
    assert result["blockers"] == ["missing primitive: R1"]


def test_workflow_returns_one_deterministic_primitive_tool_for_mixed_families(
    ready_phase1_inputs,
) -> None:
    profile, _primitive, request_plan = ready_phase1_inputs
    mixed = copy.deepcopy(request_plan)
    mixed["direct_measurement_contract"]["pad_budget"]["assignments"].append(
        {"dut": "T1", "family": "transistor", "terminal": "G", "net": "TG", "pad": 14}
    )

    result = guide_phase1_direct_workflow(
        process_capability=profile,
        intake_plan=mixed,
    )

    assert result["next_tool"] == "plan_metal_resistor_primitive"
    assert " / " not in result["next_tool"]


def test_workflow_exposes_routing_then_measurement_finalize_handoff(
    ready_phase1_inputs,
) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    placement = _placement(primitive)

    before_routing = guide_phase1_direct_workflow(
        process_capability=profile,
        intake_plan=request_plan,
        primitive_instances=[placement],
    )
    assert before_routing["next_tool"] == "plan_phase1_terminal_routes"

    before_finalize = guide_phase1_direct_workflow(
        process_capability=profile,
        intake_plan=request_plan,
        primitive_instances=[placement],
        route_plan=_route_plan(profile, request_plan),
    )
    assert before_finalize["current_stage"] == "measurement_finalize"
    assert before_finalize["next_tool"] == "plan_direct_measurement_teg"


def test_workflow_reaches_verified_nonproduction_completion(
    tmp_path,
    ready_phase1_inputs,
) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    placement = _placement(primitive)
    route_plan = _route_plan(profile, request_plan)
    layout_plan = compose_phase1_direct_layout(
        output_layout_path=str(tmp_path / "workflow.gds"),
        top_cell="PHASE1_DIRECT_TEG",
        process_capability=profile,
        request_plan=request_plan,
        primitive_instances=[placement],
        pad_rail_width_um=0.3,
    )
    generation = {
        "ok": True,
        "fresh_reload_verified": True,
        "evidence_ladder": {
            "highest_attained_state": "signoff_evidence_approved",
            "production_ready": True,
        },
        "phase1_manifest": {
            "process": profile["process"],
            "frame_um": layout_plan["frame_um"],
            "pad_count": layout_plan["pad_count"],
            "primitive_duts": layout_plan["primitive_duts"],
            "drawing_plan_fingerprint_sha256": layout_plan["drawing_plan_fingerprint_sha256"],
        },
        "production_ready": False,
    }

    result = guide_phase1_direct_workflow(
        process_capability=profile,
        intake_plan=request_plan,
        primitive_instances=[placement],
        route_plan=route_plan,
        final_request_plan=request_plan,
        layout_plan=layout_plan,
        generation_result=generation,
    )

    assert result["workflow_status"] == "complete_verified_nonproduction"
    assert result["next_tool"] is None
    assert result["production_ready"] is False
    assert result["evidence_ladder"]["highest_attained_state"] == "intent_draft_complete"
    assert result["evidence_ladder"]["production_ready"] is False
    assert "signoff_evidence_approved" not in result["evidence_ladder"]["attained_states"]
    assert result["completed_stages"][-1] == "atomic_generation"


def test_workflow_rejects_route_drift(ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    changed = copy.deepcopy(request_plan)
    changed["routing_policy"]["m1_feasibility_report"]["route_fingerprint_sha256"] = "changed"

    with pytest.raises(AnalysisError) as caught:
        guide_phase1_direct_workflow(
            process_capability=profile,
            intake_plan=request_plan,
            primitive_instances=[_placement(primitive)],
            route_plan=_route_plan(profile, request_plan),
            final_request_plan=changed,
        )

    assert caught.value.code == "PHASE1_WORKFLOW_ROUTE_DRIFT"
