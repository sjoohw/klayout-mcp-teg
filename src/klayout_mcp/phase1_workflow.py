"""Resumable workflow guidance for the Phase 1 direct-measurement pipeline."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .evidence_state import evaluate_evidence_ladder
from .errors import AnalysisError
from .process_capability import validate_process_capability


PHASE1_WORKFLOW_STAGES = (
    {
        "stage": "process_capability",
        "tools": ["describe_process_capability", "validate_process_capability_profile"],
        "purpose": "lock exact process/version, DBU, layers, devices, rules, and evidence",
    },
    {
        "stage": "measurement_intake",
        "tools": ["plan_direct_measurement_teg"],
        "purpose": "close user questions and establish explicit DUT terminal/Pad assignments",
    },
    {
        "stage": "device_doe_optional",
        "tools": ["plan_phase1_device_doe"],
        "purpose": "expand only process-supported splits when a DOE is requested",
    },
    {
        "stage": "primitive_geometry",
        "tools": [
            "process-specific transistor primitive adapter",
            "plan_metal_resistor_primitive",
            "plan_mom_capacitor_primitive",
        ],
        "purpose": "create one verified DUT-local primitive instance per mapped DUT",
    },
    {
        "stage": "terminal_routing",
        "tools": ["plan_phase1_terminal_routes"],
        "purpose": "derive exact terminal/Pad endpoints and bounded first-metal routes",
    },
    {
        "stage": "measurement_finalize",
        "tools": ["plan_direct_measurement_teg"],
        "purpose": "rerun the intake with generated routing_connections until ready_for_geometry",
    },
    {
        "stage": "layout_composition",
        "tools": ["plan_phase1_direct_teg_layout"],
        "purpose": "compose Pads, primitives, routes, outline, and semantic fingerprints",
    },
    {
        "stage": "atomic_generation",
        "tools": ["generate_phase1_direct_teg"],
        "purpose": "write once, fresh-reload, and attach the verified Phase 1 manifest",
    },
)


def _next(
    *,
    completed: list[str],
    stage: str,
    tool: str,
    action: str,
    blockers: Sequence[str] = (),
    process: Mapping[str, Any] | None = None,
    input_template: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "contract_version": 1,
        "workflow": "phase1_direct_measurement",
        "workflow_status": "action_required",
        "production_ready": False,
        "process": dict(process) if process is not None else None,
        "completed_stages": completed,
        "current_stage": stage,
        "next_tool": tool,
        "next_tool_input_template": dict(input_template) if input_template is not None else None,
        "next_action": action,
        "blockers": list(blockers),
        "stages": [dict(item) for item in PHASE1_WORKFLOW_STAGES],
    }


def _result_ok(value: Mapping[str, Any], *, stage: str) -> None:
    if value.get("ok") is not True:
        raise AnalysisError(
            code="PHASE1_WORKFLOW_STAGE_FAILED",
            message=f"The supplied {stage} result is not successful.",
            details={"stage": stage, "result_code": value.get("code")},
            next_action="Resolve that stage's structured error before continuing.",
        )


def _primitive_tool(family: str, *, process_name: str) -> str:
    if family == "transistor":
        raise AnalysisError(
            code="PROCESS_PRIMITIVE_ADAPTER_NOT_IMPLEMENTED",
            message="This process has no implemented transistor primitive adapter.",
            details={"family": family, "process_name": process_name},
            next_action=(
                "Follow onboarding.md and implement a verified process-specific primitive "
                "adapter from an approved PCell, confirmed reference geometry, or explicit rules."
            ),
        )
    if family == "resistor":
        return "plan_metal_resistor_primitive"
    if family == "capacitor":
        return "plan_mom_capacitor_primitive"
    raise AnalysisError(
        code="UNSUPPORTED_TEG_DEVICE_FAMILY",
        message="The workflow cannot select a primitive tool for this device family.",
        details={"family": family},
        next_action="Use a Phase 1 transistor, resistor, or capacitor family.",
        example_fix_payload={"family": "transistor"},
    )


def guide_phase1_direct_workflow(
    *,
    process_capability: Mapping[str, Any] | None = None,
    intake_plan: Mapping[str, Any] | None = None,
    doe_plan: Mapping[str, Any] | None = None,
    doe_required: bool = False,
    primitive_instances: Sequence[Mapping[str, Any]] | None = None,
    route_plan: Mapping[str, Any] | None = None,
    final_request_plan: Mapping[str, Any] | None = None,
    layout_plan: Mapping[str, Any] | None = None,
    generation_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate completed handoffs and identify exactly one next Phase 1 tool."""

    completed: list[str] = []
    if process_capability is None:
        return _next(
            completed=completed,
            stage="process_capability",
            tool="describe_pdk_profile_inputs",
            action=(
                "Follow onboarding.md, compose the target process profile from approved inputs, "
                "then validate it with validate_process_capability_profile."
            ),
            input_template={"profile": "<target schema-v1 process capability>"},
        )
    capability = validate_process_capability(process_capability)
    process = capability["process"]
    completed.append("process_capability")

    if intake_plan is None:
        return _next(
            completed=completed,
            stage="measurement_intake",
            tool="plan_direct_measurement_teg",
            action="Collect process identity, dimensions, DUT contracts, terminal/Pad assignments, bias, and obstacles.",
            process=process,
            input_template={
                "device_families": [],
                "process_profile": process["name"],
                "process_profile_version": process["version"],
                "frame_width_um": 2000.0,
                "frame_height_um": 54.0,
                "pad_count": 25,
                "pad_rows": 1,
                "pad_width_um": 40.0,
                "pad_height_um": 40.0,
                "measurement_mode": "direct",
                "prefer_first_metal": True,
            },
        )
    _result_ok(intake_plan, stage="measurement_intake")
    request = intake_plan.get("request")
    direct = intake_plan.get("direct_measurement_contract")
    if not isinstance(request, Mapping) or not isinstance(direct, Mapping):
        raise AnalysisError(
            code="INVALID_PHASE1_WORKFLOW_HANDOFF",
            message="The intake plan lacks its request or direct-measurement contract.",
            details={"stage": "measurement_intake"},
            next_action="Use the unmodified plan_direct_measurement_teg result.",
        )
    if (
        request.get("process_profile") != process["name"]
        or request.get("process_profile_version") != process["version"]
    ):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_PROCESS_MISMATCH",
            message="The intake plan and process capability identities differ.",
            details={"request": dict(request), "process": process},
            next_action="Rerun measurement intake with the selected exact process/version.",
        )
    pad_budget = direct.get("pad_budget")
    if not isinstance(pad_budget, Mapping) or pad_budget.get("status") != "fits":
        return _next(
            completed=completed,
            stage="measurement_intake",
            tool="plan_direct_measurement_teg",
            action="Complete the explicit DUT terminal/Pad budget before primitive generation.",
            blockers=intake_plan.get("required_question_ids", ()),
            process=process,
        )
    unresolved = set(intake_plan.get("required_question_ids", ()))
    allowed_until_routing = {"routing_connection_geometry"}
    blocking_questions = sorted(unresolved - allowed_until_routing)
    if blocking_questions:
        return _next(
            completed=completed,
            stage="measurement_intake",
            tool="plan_direct_measurement_teg",
            action="Resolve every non-routing question before drawing DUT primitives.",
            blockers=blocking_questions,
            process=process,
        )
    completed.append("measurement_intake")

    if doe_required:
        if doe_plan is None:
            return _next(
                completed=completed,
                stage="device_doe_optional",
                tool="plan_phase1_device_doe",
                action="Expand only the DOE axes explicitly supported by this process capability.",
                process=process,
            )
        _result_ok(doe_plan, stage="device_doe_optional")
        doe_process = doe_plan.get("process_contract")
        if not isinstance(doe_process, Mapping) or (
            doe_process.get("profile") != process["name"]
            or doe_process.get("version") != process["version"]
        ):
            raise AnalysisError(
                code="PHASE1_WORKFLOW_PROCESS_MISMATCH",
                message="The DOE plan and process capability identities differ.",
                details={"doe_process": doe_process, "process": process},
                next_action="Regenerate the DOE from the selected process/version.",
            )
        completed.append("device_doe_optional")

    assignments = pad_budget.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise AnalysisError(
            code="INVALID_PHASE1_WORKFLOW_HANDOFF",
            message="The verified Pad budget has no terminal assignments.",
            details={},
            next_action="Regenerate the direct-measurement intake plan.",
        )
    expected_duts = {assignment["dut"] for assignment in assignments}
    family_by_dut: dict[str, str] = {}
    for assignment in assignments:
        existing_family = family_by_dut.setdefault(assignment["dut"], assignment["family"])
        if existing_family != assignment["family"]:
            raise AnalysisError(
                code="PHASE1_WORKFLOW_ASSIGNMENT_DRIFT",
                message="One DUT is assigned to more than one device family.",
                details={"dut": assignment["dut"]},
                next_action="Correct the terminal assignment family contract.",
            )
    instances = list(primitive_instances or ())
    instance_duts = {
        entry.get("dut")
        for entry in instances
        if isinstance(entry, Mapping) and isinstance(entry.get("dut"), str)
    }
    if instance_duts != expected_duts:
        missing = sorted(expected_duts - instance_duts)
        unexpected = sorted(instance_duts - expected_duts)
        next_dut = missing[0] if missing else sorted(expected_duts)[0]
        next_family = family_by_dut[next_dut]
        return _next(
            completed=completed,
            stage="primitive_geometry",
            tool=_primitive_tool(next_family, process_name=process["name"]),
            action="Create exactly one verified, process-matched DUT-local primitive for each mapped DUT.",
            blockers=[
                *(f"missing primitive: {dut}" for dut in missing),
                *(f"unexpected primitive: {dut}" for dut in unexpected),
            ],
            process=process,
            input_template={
                "process_capability": "<validated process capability result>",
                "device_name": "<process capability device name>",
            },
        )
    for entry in instances:
        primitive = entry.get("primitive")
        if (
            not isinstance(primitive, Mapping)
            or primitive.get("process") != process
            or primitive.get("geometry_status") != "process_gated_primitive_not_routed"
            or not primitive.get("verification")
        ):
            raise AnalysisError(
                code="INVALID_PHASE1_WORKFLOW_PRIMITIVE",
                message="A primitive is unverified or belongs to another process.",
                details={"dut": entry.get("dut")},
                next_action="Regenerate that primitive using the selected process capability.",
            )
        device = primitive.get("device")
        if not isinstance(device, Mapping) or device.get("family") != family_by_dut[entry["dut"]]:
            raise AnalysisError(
                code="PHASE1_WORKFLOW_PRIMITIVE_FAMILY_MISMATCH",
                message="A primitive family differs from its terminal assignment family.",
                details={
                    "dut": entry["dut"],
                    "assigned_family": family_by_dut[entry["dut"]],
                    "primitive_device": device,
                },
                next_action="Regenerate the DUT primitive for the assigned family.",
            )
    completed.append("primitive_geometry")

    if route_plan is None:
        return _next(
            completed=completed,
            stage="terminal_routing",
            tool="plan_phase1_terminal_routes",
            action="Generate exact terminal-to-Pad connections with Pad/DUT keepouts and terminal-specific width/space.",
            process=process,
        )
    _result_ok(route_plan, stage="terminal_routing")
    if route_plan.get("process") != process:
        raise AnalysisError(
            code="PHASE1_WORKFLOW_PROCESS_MISMATCH",
            message="The terminal route plan belongs to another process.",
            details={"route_process": route_plan.get("process"), "process": process},
            next_action="Regenerate terminal routing from the selected capability.",
        )
    if (
        route_plan.get("frame_um") != request.get("frame_um")
        or route_plan.get("pad_count") != request.get("pads", {}).get("count")
    ):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_FRAME_DRIFT",
            message="Frame or Pad count changed between intake and terminal routing.",
            details={
                "request_frame_um": request.get("frame_um"),
                "route_frame_um": route_plan.get("frame_um"),
                "request_pad_count": request.get("pads", {}).get("count"),
                "route_pad_count": route_plan.get("pad_count"),
            },
            next_action="Regenerate routing with the intake frame and Pad topology unchanged.",
        )
    if route_plan.get("ready_for_direct_measurement_planner") is not True:
        report = route_plan.get("m1_feasibility_report", {})
        return _next(
            completed=completed,
            stage="terminal_routing",
            tool="plan_phase1_terminal_routes",
            action="Revise placement, keepouts, width/space, or preferred Manhattan waypoints and rerun bounded routing.",
            blockers=[str(report.get("status", "first-metal routing not feasible"))],
            process=process,
        )
    completed.append("terminal_routing")

    if final_request_plan is None:
        return _next(
            completed=completed,
            stage="measurement_finalize",
            tool="plan_direct_measurement_teg",
            action="Rerun measurement planning with route_plan.routing_connections; require ready_for_geometry.",
            process=process,
        )
    _result_ok(final_request_plan, stage="measurement_finalize")
    if final_request_plan.get("planning_status") != "ready_for_geometry":
        return _next(
            completed=completed,
            stage="measurement_finalize",
            tool="plan_direct_measurement_teg",
            action="Close the remaining questions and first-metal feasibility gate.",
            blockers=final_request_plan.get("required_question_ids", ()),
            process=process,
        )
    final_request = final_request_plan.get("request")
    if not isinstance(final_request, Mapping) or (
        final_request.get("process_profile") != process["name"]
        or final_request.get("process_profile_version") != process["version"]
        or final_request.get("frame_um") != request.get("frame_um")
        or final_request.get("pads") != request.get("pads")
    ):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_REQUEST_DRIFT",
            message="Process, frame, or Pad topology changed during measurement finalization.",
            details={},
            next_action="Finalize with the original intake contract and generated routes unchanged.",
        )
    final_direct = final_request_plan.get("direct_measurement_contract", {})
    final_budget = final_direct.get("pad_budget", {}) if isinstance(final_direct, Mapping) else {}
    if final_budget.get("assignments") != assignments:
        raise AnalysisError(
            code="PHASE1_WORKFLOW_ASSIGNMENT_DRIFT",
            message="Terminal/Pad assignments changed between intake and finalization.",
            details={},
            next_action="Return to primitive placement and routing with one stable assignment contract.",
        )
    planned_report = final_request_plan.get("routing_policy", {}).get("m1_feasibility_report", {})
    route_report = route_plan.get("m1_feasibility_report", {})
    if planned_report.get("route_fingerprint_sha256") != route_report.get("route_fingerprint_sha256"):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_ROUTE_DRIFT",
            message="Final measurement planning did not preserve the terminal route geometry.",
            details={},
            next_action="Pass route_plan.routing_connections unchanged into final measurement planning.",
        )
    completed.append("measurement_finalize")

    if layout_plan is None:
        return _next(
            completed=completed,
            stage="layout_composition",
            tool="plan_phase1_direct_teg_layout",
            action="Compose the finalized request, verified primitives, routes, Pads, and outline.",
            process=process,
        )
    _result_ok(layout_plan, stage="layout_composition")
    if layout_plan.get("ready_for_klayout_generation") is not True:
        raise AnalysisError(
            code="INVALID_PHASE1_WORKFLOW_HANDOFF",
            message="The supplied layout plan is not ready for KLayout generation.",
            details={},
            next_action="Regenerate the atomic Phase 1 layout plan.",
        )
    if (
        layout_plan.get("process") != process
        or layout_plan.get("frame_um") != request.get("frame_um")
        or layout_plan.get("pad_count") != request.get("pads", {}).get("count")
        or set(layout_plan.get("primitive_duts", ())) != expected_duts
    ):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_LAYOUT_DRIFT",
            message="The composed layout changed process, frame, Pad count, or DUT set.",
            details={},
            next_action="Recompose from the validated workflow handoffs.",
        )
    completed.append("layout_composition")

    if generation_result is None:
        return _next(
            completed=completed,
            stage="atomic_generation",
            tool="generate_phase1_direct_teg",
            action="Generate to a new path and require fresh-reload semantic verification.",
            process=process,
        )
    _result_ok(generation_result, stage="atomic_generation")
    if (
        generation_result.get("fresh_reload_verified") is not True
        or not isinstance(generation_result.get("phase1_manifest"), Mapping)
    ):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_GENERATION_NOT_VERIFIED",
            message="Generation lacks fresh-reload verification or its Phase 1 manifest.",
            details={},
            next_action="Do not accept the output; rerun atomic generation and inspect the structured error.",
        )
    manifest = generation_result["phase1_manifest"]
    if (
        manifest.get("process") != process
        or manifest.get("frame_um") != layout_plan.get("frame_um")
        or manifest.get("pad_count") != layout_plan.get("pad_count")
        or set(manifest.get("primitive_duts", ())) != expected_duts
        or manifest.get("drawing_plan_fingerprint_sha256")
        != layout_plan.get("drawing_plan_fingerprint_sha256")
    ):
        raise AnalysisError(
            code="PHASE1_WORKFLOW_GENERATION_DRIFT",
            message="The fresh-reload generation manifest differs from the composed layout plan.",
            details={},
            next_action="Reject the output and rerun atomic generation from the validated layout plan.",
        )
    completed.append("atomic_generation")
    projection = manifest.get("connectivity_projection", {})
    # Never trust a caller-supplied ladder summary. Recompute observed state from
    # the handoffs this guide has checked, while keeping approval explicitly false
    # until a future trusted client supplies verifiable provenance.
    evidence_ladder = evaluate_evidence_ladder(
        {
            "draft_schema_valid": True,
            "unresolved_questions_zero": True,
            "approval_backend_trusted": False,
            "approval_verified": False,
            "plan_fingerprint_verified": bool(
                manifest.get("drawing_plan_fingerprint_sha256")
            ),
            "routing_plan_complete": bool(
                manifest.get("first_metal_route_fingerprint_sha256")
            ),
            "fresh_reload_verified": generation_result.get("fresh_reload_verified") is True,
            "drawing_fingerprint_verified": True,
            "connectivity_projection_verified": bool(projection)
            and all(
                projection.get(field) is True
                for field in (
                    "route_set_exact",
                    "different_net_route_spacing_verified",
                    "same_net_route_connectivity_verified",
                    "primitive_terminal_component_overlap_verified",
                )
            ),
        }
    )
    return {
        "ok": True,
        "contract_version": 1,
        "workflow": "phase1_direct_measurement",
        "workflow_status": "complete_verified_nonproduction",
        "production_ready": False,
        "evidence_ladder": dict(evidence_ladder),
        "process": dict(process),
        "completed_stages": completed,
        "current_stage": None,
        "next_tool": None,
        "next_action": "Obtain approved DRC/LVS/PEX and process sign-off before fabrication.",
        "blockers": ["production sign-off requires target-organization evidence"],
        "stages": [dict(item) for item in PHASE1_WORKFLOW_STAGES],
    }
