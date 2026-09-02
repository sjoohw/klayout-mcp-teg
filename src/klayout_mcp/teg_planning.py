"""Process-aware request gate for general direct-measurement TEG planning."""

from __future__ import annotations

import math
from typing import Any, Iterable

from .design_contract import SUPPORTED_DIMENSION_SEMANTICS
from .direct_measurement import analyze_direct_pad_budget
from .errors import AnalysisError
from .routing_feasibility import analyze_first_metal_feasibility
from .organization_presets import load_organization_preset


SUPPORTED_DEVICE_FAMILIES = ("transistor", "resistor", "capacitor")
SUPPORTED_MEASUREMENT_MODES = ("direct", "multiplexed")


def _positive_finite(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise AnalysisError(
            code="INVALID_TEG_PLANNING_INPUT",
            message=f"{field} must be a finite positive number.",
            details={"field": field, "value": value},
            next_action=f"Provide a finite positive {field} value.",
        )
    return float(value)


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AnalysisError(
            code="INVALID_TEG_PLANNING_INPUT",
            message=f"{field} must be a positive integer.",
            details={"field": field, "value": value},
            next_action=f"Provide a positive integer {field} value.",
        )
    return value


def _normalize_families(values: Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    for value in values:
        if value not in SUPPORTED_DEVICE_FAMILIES:
            raise AnalysisError(
                code="UNSUPPORTED_TEG_DEVICE_FAMILY",
                message="The requested device family is outside the Phase 1 contract.",
                details={
                    "device_family": value,
                    "supported_device_families": list(SUPPORTED_DEVICE_FAMILIES),
                },
                next_action="Choose transistor, resistor, and/or capacitor.",
            )
        if value not in normalized:
            normalized.append(value)
    return normalized


def _question(question_id: str, prompt: str, reason: str) -> dict[str, str]:
    return {"id": question_id, "prompt": prompt, "reason": reason}


def plan_teg_measurement_request(
    *,
    device_families: Iterable[str] | None = None,
    process_profile: str | None = None,
    process_profile_version: str | None = None,
    frame_width_um: float = 2000.0,
    frame_height_um: float = 54.0,
    pad_count: int = 25,
    pad_rows: int = 1,
    pad_width_um: float = 40.0,
    pad_height_um: float = 40.0,
    dut_count: int | None = None,
    measurement_mode: str = "direct",
    prefer_first_metal: bool = True,
    allow_additional_metals_if_unavoidable: bool = True,
    approved_layermap: bool = False,
    approved_design_rules: bool = False,
    terminal_mapping_confirmed: bool = False,
    measurement_bias_confirmed: bool = False,
    routing_obstacles_confirmed: bool = False,
    dimension_semantics: str | None = None,
    dimension_semantics_by_family: dict[str, str] | None = None,
    terminal_assignments: Iterable[dict[str, Any]] | None = None,
    reserved_pad_indices: Iterable[int] | None = None,
    dut_terminal_contracts: Iterable[dict[str, Any]] | None = None,
    routing_connections: Iterable[dict[str, Any]] | None = None,
    routing_obstacles_um: Iterable[list[float]] | None = None,
    routing_boundary_um: list[float] | None = None,
) -> dict[str, Any]:
    """Return the complete question gate and bounded Phase 1 design plan.

    This function intentionally does not draw.  It prevents a generator from silently
    inventing process layers, terminal sharing, W/L meaning, or M1 feasibility.
    """

    for field, value in (
        ("process_profile", process_profile),
        ("process_profile_version", process_profile_version),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise AnalysisError(
                code="INVALID_TEG_PLANNING_INPUT",
                message=f"{field} must be a non-empty string when provided.",
                details={"field": field, "value": value},
                next_action="Provide the exact non-empty process capability identity.",
            )
    process_profile = process_profile.strip() if process_profile is not None else None
    process_profile_version = (
        process_profile_version.strip() if process_profile_version is not None else None
    )

    width = _positive_finite(frame_width_um, field="frame_width_um")
    height = _positive_finite(frame_height_um, field="frame_height_um")
    pad_width = _positive_finite(pad_width_um, field="pad_width_um")
    pad_height = _positive_finite(pad_height_um, field="pad_height_um")
    pads = _positive_int(pad_count, field="pad_count")
    rows = _positive_int(pad_rows, field="pad_rows")
    if dut_count is not None:
        _positive_int(dut_count, field="dut_count")
    if measurement_mode not in SUPPORTED_MEASUREMENT_MODES:
        raise AnalysisError(
            code="UNSUPPORTED_MEASUREMENT_MODE",
            message="measurement_mode is not supported by the planning contract.",
            details={
                "measurement_mode": measurement_mode,
                "supported_measurement_modes": list(SUPPORTED_MEASUREMENT_MODES),
            },
            next_action="Use direct for Phase 1 or multiplexed for the deferred Phase 2 scope.",
        )
    if dimension_semantics is not None and dimension_semantics not in SUPPORTED_DIMENSION_SEMANTICS:
        raise AnalysisError(
            code="INVALID_DIMENSION_SEMANTICS",
            message="dimension_semantics is not a supported explicit confirmation token.",
            details={
                "dimension_semantics": dimension_semantics,
                "supported_dimension_semantics": list(SUPPORTED_DIMENSION_SEMANTICS),
            },
            next_action="Confirm and pass one supported dimension_semantics token exactly.",
        )

    families = _normalize_families(device_families)
    family_semantics = dict(dimension_semantics_by_family or {})
    invalid_semantic_families = sorted(set(family_semantics).difference(families))
    invalid_semantic_values = {
        family: value
        for family, value in family_semantics.items()
        if value not in SUPPORTED_DIMENSION_SEMANTICS
    }
    if invalid_semantic_families or invalid_semantic_values:
        raise AnalysisError(
            code="INVALID_FAMILY_DIMENSION_SEMANTICS",
            message="Per-family dimension semantics do not match the requested families.",
            details={
                "unexpected_families": invalid_semantic_families,
                "invalid_values": invalid_semantic_values,
                "supported_dimension_semantics": list(SUPPORTED_DIMENSION_SEMANTICS),
            },
            next_action="Provide one supported semantics token for each requested family only.",
        )
    questions: list[dict[str, str]] = []
    if not families:
        questions.append(
            _question(
                "device_families",
                "Which Phase 1 families are required: transistor, resistor, capacitor?",
                "Device families determine terminal count, process layers, and verification.",
            )
        )
    if not process_profile:
        questions.append(
            _question(
                "process_profile",
                "Which exact process/PDK profile should be used?",
                "Layer numbers and legal devices must never be inferred.",
            )
        )
    if not process_profile_version:
        questions.append(
            _question(
                "process_profile_version",
                "Which exact version of that process capability profile should be used?",
                "A profile name alone cannot prevent rules or layer maps from drifting between releases.",
            )
        )
    if not approved_layermap:
        questions.append(
            _question(
                "approved_layermap",
                "Provide or approve the production layermap.",
                "A named process alone does not prove the stream layer/datatype mapping.",
            )
        )
    if not approved_design_rules:
        questions.append(
            _question(
                "approved_design_rules",
                "Provide or approve the applicable DRC/device-rule source and version.",
                "Routing and device geometry require process-specific width, space, and enclosure rules.",
            )
        )
    if dut_count is None:
        questions.append(
            _question(
                "dut_count_and_splits",
                "How many DUTs and which parameter splits are required?",
                "Pad budgeting and placement cannot be derived from family names alone.",
            )
        )
    assignment_records = list(terminal_assignments or [])
    terminal_contract_records = list(dut_terminal_contracts or [])
    if not terminal_mapping_confirmed:
        questions.append(
            _question(
                "terminal_mapping",
                "Confirm every DUT terminal, shared net, pad role, and unused pad.",
                "Direct measurement forbids silently introduced muxing or terminal sharing.",
            )
        )
    if terminal_mapping_confirmed and not assignment_records:
        questions.append(
            _question(
                "terminal_assignment_records",
                "Provide explicit {dut, family, terminal, net, pad} records.",
                "A confirmation flag alone cannot prove the 25-Pad direct-measurement budget.",
            )
        )
    if assignment_records and not terminal_contract_records:
        questions.append(
            _question(
                "dut_terminal_contracts",
                "Provide each DUT family, measurement type, and complete required terminal list.",
                "Pad assignments cannot prove that a Body, sense, shield, or substrate terminal was omitted.",
            )
        )
    semantics_complete = bool(families) and (
        (len(families) == 1 and dimension_semantics is not None)
        or set(family_semantics) == set(families)
    )
    if families and not semantics_complete:
        questions.append(
            _question(
                "dimension_semantics_by_family",
                "Confirm the physical meaning and axes of W/L for each device family.",
                "Transistor W/L and resistor transverse/longitudinal dimensions are not interchangeable.",
            )
        )
    if not measurement_bias_confirmed:
        questions.append(
            _question(
                "measurement_bias",
                "Confirm voltage/current ranges, polarity, accuracy target, and current-density limits.",
                "Conductor sizing and direct-measurement validity depend on the electrical envelope.",
            )
        )
    if not routing_obstacles_confirmed:
        questions.append(
            _question(
                "routing_obstacles",
                "Confirm DUT keepouts, blocked layers, probe constraints, and allowed routing windows.",
                "M1 planarity cannot be established without terminals and obstacles.",
            )
        )
    connection_records = list(routing_connections or [])
    if routing_obstacles_confirmed and not connection_records:
        questions.append(
            _question(
                "routing_connection_geometry",
                "Provide each direct net's terminal-to-Pad landing coordinates, width, and clear space.",
                "Obstacle confirmation alone cannot establish first-metal route feasibility.",
            )
        )

    if pads == 25 and rows == 1:
        topology_status = "primary_supported_profile"
    elif pads == 16 and rows == 2:
        topology_status = "recognized_abnormal_profile_deferred"
        questions.append(
            _question(
                "abnormal_pad_profile",
                "Provide and approve the 16-Pad two-row geometry, numbering, and probe order.",
                "This rare topology is recognized but is not the Phase 1 primary profile.",
            )
        )
    else:
        topology_status = "custom_profile_required"
        questions.append(
            _question(
                "custom_pad_profile",
                "Provide and approve the custom Pad geometry, numbering, and probe order.",
                "Only the 25-Pad single-row topology is a primary built-in profile.",
            )
        )

    if measurement_mode == "multiplexed":
        phase = 2
        questions.append(
            _question(
                "phase2_mux_architecture",
                "Define mux, decoder/control, buffer, supplies, and output loading.",
                "Multiplexed measurement is intentionally outside the Phase 1 direct path.",
            )
        )
    else:
        phase = 1

    pad_budget = (
        analyze_direct_pad_budget(
            assignment_records,
            pad_count=pads,
            reserved_pad_indices=reserved_pad_indices or (),
            terminal_contracts=terminal_contract_records or None,
        )
        if assignment_records
        else {
            "status": "not_evaluated_missing_terminal_assignments",
            "pad_count": pads,
            "implicit_terminal_sharing": False,
        }
    )
    if assignment_records:
        assigned_families = set(pad_budget["device_families"])
        requested_families = set(families)
        if assigned_families != requested_families:
            raise AnalysisError(
                code="REQUESTED_DEVICE_FAMILY_MISMATCH",
                message="Terminal assignments do not represent every requested device family exactly.",
                details={
                    "requested_device_families": sorted(requested_families),
                    "assigned_device_families": sorted(assigned_families),
                },
                next_action="Align device_families with the explicit DUT terminal assignments.",
            )
        if dut_count is not None and pad_budget["dut_count"] != dut_count:
            raise AnalysisError(
                code="DUT_COUNT_TERMINAL_MAPPING_MISMATCH",
                message="dut_count differs from the unique DUTs in terminal assignments.",
                details={
                    "dut_count": dut_count,
                    "mapped_dut_count": pad_budget["dut_count"],
                },
                next_action="Correct dut_count or the explicit terminal assignment records.",
            )
    if connection_records and assignment_records:
        budget_nets = set(pad_budget["pad_to_net"].values())
        connection_nets = {
            record.get("net") for record in connection_records if isinstance(record, dict)
        }
        if connection_nets != budget_nets:
            raise AnalysisError(
                code="ROUTING_NET_SET_MISMATCH",
                message="Routing connections do not cover exactly the direct-measurement Pad nets.",
                details={
                    "missing_routing_nets": sorted(budget_nets.difference(connection_nets)),
                    "unexpected_routing_nets": sorted(connection_nets.difference(budget_nets)),
                },
                next_action="Provide at least one connection for every mapped net and no invented net.",
            )
    m1_report = (
        analyze_first_metal_feasibility(
            connection_records,
            boundary_um=routing_boundary_um or [0.0, 0.0, width, height],
            obstacles_um=routing_obstacles_um or (),
        )
        if connection_records
        else {
            "status": "not_evaluated_missing_connections",
            "feasible": None,
            "failure_proves_m1_impossible": False,
            "routes": [],
        }
    )
    geometry_ready = (
        not questions
        and topology_status == "primary_supported_profile"
        and m1_report["feasible"] is True
    )
    if m1_report["feasible"] is False:
        planning_status = "routing_revision_required"
    elif geometry_ready:
        planning_status = "ready_for_geometry"
    else:
        planning_status = "questions_required"
    return {
        "ok": True,
        "contract_version": 2,
        "planning_status": planning_status,
        "stop_before_drawing": planning_status != "ready_for_geometry",
        "assistant_action": (
            "continue_to_geometry_planning"
            if planning_status == "ready_for_geometry"
            else "return_required_questions_verbatim_and_wait_for_user"
        ),
        "confirmation_values_must_come_from_user": True,
        "invented_defaults_are_forbidden": True,
        "approved_organization_defaults_are_allowed": True,
        "phase": phase,
        "request": {
            "device_families": families,
            "process_profile": process_profile,
            "process_profile_version": process_profile_version,
            "frame_um": [width, height],
            "frame_is_parameterized": True,
            "pads": {
                "count": pads,
                "rows": rows,
                "outline_um": [pad_width, pad_height],
                "topology_status": topology_status,
            },
            "dut_count": dut_count,
            "measurement_mode": measurement_mode,
            "dimension_semantics": dimension_semantics,
            "dimension_semantics_by_family": family_semantics,
        },
        "routing_policy": {
            "preferred_layer": "first_metal" if prefer_first_metal else "process_selected",
            "first_metal_only_is_preference_not_unproven_result": True,
            "allow_additional_metals_if_unavoidable": allow_additional_metals_if_unavoidable,
            "orthogonal_only": True,
            "m1_feasibility": (
                m1_report["status"]
            ),
            "m1_feasibility_report": m1_report,
            "escalation_rule": (
                "prove_M1_failure_then_use_the_minimum_additional_layer_count_and_report_reason"
            ),
            "layer_escalation_status": (
                "not_needed"
                if m1_report["feasible"] is True
                else (
                    "candidate_only_because_bounded_search_failure_is_not_an_impossibility_proof"
                    if m1_report["feasible"] is False
                    and allow_additional_metals_if_unavoidable
                    else "not_authorized_or_not_evaluated"
                )
            ),
        },
        "direct_measurement_contract": {
            "enabled": measurement_mode == "direct",
            "dedicated_terminal_paths_required": measurement_mode == "direct",
            "implicit_mux_or_switch_allowed": False,
            "pad_budget_status": (
                pad_budget["status"]
            ),
            "pad_budget": pad_budget,
        },
        "transistor_context_contract": (
            load_organization_preset()["transistor_context_defaults"]
            if "transistor" in families
            else None
        ),
        "phase1_template_scope": {
            "transistor": [
                "W_L_and_device_flavor_baseline",
                "diffusion_edge_SA_SB_or_LOD",
                "well_edge_distance_or_WPE",
                "STI_active_spacing_and_orientation",
                "finger_count_gate_pitch_and_dummy_gates",
                "source_drain_contact_arrangement_and_asymmetry",
                "guard_ring_type_and_distance",
            ],
            "resistor": [
                "two_terminal_direct",
                "four_terminal_kelvin_sheet_or_line",
                "cross_bridge_kelvin_contact",
                "length_width_and_contact_count_splits",
            ],
            "capacitor": [
                "two_terminal_MIM_MOM_or_MOSCAP",
                "area_and_perimeter_splits",
                "orientation_and_matching_dummies_when_supported",
                "optional_open_short_deembedding_structures",
            ],
            "activation_rule": "enable_only_devices_and_LDE_axes_supported_by_the_selected_process",
        },
        "required_questions": questions,
        "required_question_ids": [item["id"] for item in questions],
        "deferred_phase2": ["ring_oscillator", "mux", "decoder", "output_buffer"],
    }
