"""User-confirmed dimension, routing, and parasitic-resistance contracts."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .errors import AnalysisError


TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH = (
    "width_is_transverse_axis_length_is_longitudinal_axis"
)
DEVICE_SPECIFIC_W_L = "device_specific_w_l"
SUPPORTED_DIMENSION_SEMANTICS = (
    TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
    DEVICE_SPECIFIC_W_L,
)

ORTHOGONAL_ROUTING_POLICY: dict[str, Any] = {
    "style": "orthogonal_only",
    "allowed_segment_directions": ["horizontal", "vertical"],
    "diagonal_segments_allowed": False,
    "arbitrary_angle_segments_allowed": False,
    "corner_style": "90_degree_manhattan",
}

PROVISIONAL_METAL_SPACING_POLICY: dict[str, Any] = {
    "applies_when_approved_foundry_rule_is_unavailable": True,
    "preferred_minimum_clear_space_rule": "at_least_maximum_adjacent_metal_width",
    "equal_width_space_to_width_ratio_minimum": 1.0,
    "example_um": {"metal_width": 0.300, "minimum_clear_space": 0.300},
    "intentional_electrical_junctions_excluded": True,
    "approved_foundry_rule_overrides_this_assumption": True,
}

DIRECT_MEASUREMENT_MESH_POLICY: dict[str, Any] = {
    "scope": "direct_measurement_teg_terminal_and_pad_routing",
    "optimization_objective": "minimize_routing_ir_drop_subject_to_confirmed_rules",
    "long_single_rail_allowed": False,
    "measured_dut_conductor_is_not_routing": True,
    "terminal_access_single_rail_allowed_only_during_bounded_mesh_transition": True,
    "parallel_rails_required": True,
    "cross_ties_required": True,
    "merged_mesh_component_must_contain_holes": True,
    "multiple_positive_area_pad_landings_required": True,
    "maximize_mesh_envelope_within_confirmed_boundary": True,
    "nominal_narrow_mesh_when_wider_mesh_is_feasible_allowed": False,
    "staged_aligned_transition_required": True,
    "natural_full_width_orthogonal_joints_required": True,
    "preserve_intermediate_mesh_modify_interface_only": True,
    "rail_count_and_pitch_source": "confirmed_process_and_project_rules",
    "applies_to": ["source", "drain", "gate", "body", "force", "sense", "pad_bus"],
    "infeasible_first_metal_action": "stop_or_request_confirmed_multimetal_escalation",
    "silent_single_rail_fallback_allowed": False,
    "source_drain_contact_array_policy": "maximum_legal_count_after_all_enclosure_and_routing_rules",
    "contact_count_must_scale_with_available_terminal_width": True,
    "fixed_contact_count_across_width_requires_constraint_evidence": True,
    "fresh_reload_evidence_required": [
        "separate_terminal_net_components",
        "mesh_holes",
        "mesh_corridor_spans",
        "multiple_pad_landings",
        "declared_vs_actual_contact_counts",
    ],
}

KELVIN_M1_ROUTING_POLICY: dict[str, Any] = {
    "scope": "kelvin_m1_metal_line_measurement",
    "measured_line_orientation": "horizontal",
    "direct_dut_shapes": [
        "measured_metal_line",
        "left_terminal_square",
        "right_terminal_square",
    ],
    "terminal_square": {
        "size_um": 0.300,
        "placement": "appended_outside_each_measured_line_end",
        "measured_length_excludes_terminal_squares": True,
        "force_and_sense_join_only_at_terminal_square": True,
    },
    "measured_line_routing_keepout": {
        "longitudinal_interval": "between_terminal_square_inner_faces",
        "routing_above_or_below_measured_line_allowed": False,
        "routing_across_measured_line_allowed": False,
    },
    "all_other_added_m1_routing": "terminal_access_then_orthogonal_mesh_required",
    "mesh_definition": {
        "parallel_rails_required": True,
        "cross_ties_required": True,
        "merged_route_component_must_contain_holes": True,
        "expansion_stage_count": 4,
        "expansion_rail_counts": [1, 2, 4, 6],
        "expansion_style": "one_sided_from_persistent_baseline_rail",
        "original_single_rail_continues_through_all_stages": True,
        "new_rails_may_be_added_on_both_sides": False,
        "stage_boundaries_must_be_aligned": True,
        "jagged_or_protruding_stage_endcaps_allowed": False,
    },
    "mesh_structure_interface_rule": {
        "preserve_intermediate_mesh_topology": True,
        "modify_only_interface_end_geometry_when_possible": True,
        "end_tie_must_align_to_receiving_rail_centerline": True,
        "adjacent_parallel_end_tie_beside_receiving_rail_allowed": False,
        "merged_interface_width_must_not_exceed_confirmed_maximum": True,
        "natural_full_width_junction_required": True,
        "current_sln001_current_force_pad_frame_rail_center_abs_x_um": 20.15,
        "current_sln001_maximum_interface_width_um": 0.300,
    },
    "force_route_priority": (
        "leave each terminal square directly along the measured-line axis toward the "
        "immediately adjacent current-force Pad, then expand as one-sided mesh"
    ),
    "sense_route_priority": (
        "leave each terminal square straight upward without a horizontal jog, retain the "
        "vertical baseline rail, and expand outward toward the outer voltage-sense Pad mesh"
    ),
    "terminal_access_rule": (
        "force and sense each leave the 0.300 um terminal square as one 0.300 um line; "
        "keep that baseline rail continuous and add new rails on one side only in four "
        "aligned stages with 1, 2, 4, then 6 rails"
    ),
    "orthogonal_bend_rule": {
        "horizontal_and_vertical_centerlines_must_intersect": True,
        "full_width_corner_overlap_required": True,
        "outer_faces_must_align": True,
        "half_width_overhang_or_recess_allowed": False,
    },
    "adjacent_pad_route": (
        "assign the immediately adjacent Pads to current forcing and use direct lateral "
        "orthogonal mesh when no confirmed obstacle requires a detour"
    ),
    "pad_roles_left_to_right": ["SENSE+", "FORCE+", "FORCE-", "SENSE-"],
    "voltage_sense_terminal_route": {
        "direction": "straight_vertical",
        "horizontal_jog_allowed": False,
        "one_sided_mesh_expansion": "outward_from_dut",
    },
    "voltage_sense_vertical_horizontal_joint": {
        "edge_only_or_single_rail_overlap_allowed": False,
        "topology": "pitch_aligned_natural_90_degree_mesh_corner",
        "last_vertical_cross_tie_must_be_one_mesh_pitch_below_horizontal_rail": True,
        "vertical_rails_must_continue_through_full_horizontal_rail_width": True,
        "horizontal_mesh_must_extend_to_persistent_baseline_rail": True,
        "innermost_corner_requires_full_width_overlap": True,
        "positive_area_overlap_required": True,
        "current_sln001_horizontal_inner_edge_abs_x_um": 1.0,
        "current_sln001_last_vertical_cross_tie_center_y_um": 19.85,
        "current_sln001_horizontal_corner_rail_center_y_um": 20.85,
        "current_sln001_last_row_clear_space_um": 0.700,
        "current_sln001_minimum_corner_overlap_area_um2": 0.090,
    },
    "outer_voltage_sense_pad_landing": {
        "parallel_ties": "maximize_across_available_pad_width",
        "tie_pitch_must_respect_provisional_or_foundry_spacing": True,
        "current_sln001_example_ties_per_pad": 39,
        "current_sln001_example_pitch_um": 1.0,
    },
    "unnecessary_down_side_up_detour_allowed": False,
    "terminal_access_single_line_width_um": 0.300,
    "solid_trunk_or_solid_sheet_outside_dut_allowed": False,
}

PARASITIC_RESISTANCE_POLICY: dict[str, Any] = {
    "objective": "minimize_routing_parasitic_resistance",
    "unproven_optimum_must_not_be_claimed": True,
    "default_status_without_rc_evidence": "not_proven_optimal",
    "geometry_priorities": [
        "shortest feasible Manhattan route",
        "widest allowed conductor within the confirmed M1 width limit",
        "maximum feasible parallel orthogonal mesh within spacing and density rules",
        "symmetric force routes and symmetric sense routes",
        "one reusable external-route cell for matched DUTs",
        "multiple positive-area pad landings and frequent cross ties",
    ],
    "evidence_required_to_claim_optimized": [
        "approved layer sheet/contact resistance data",
        "approved width, spacing, density, enclosure, and overlap rules",
        "current and current-density or EM limits",
        "available routing boundary and pad/probe constraints",
        "extracted-RC comparison against feasible candidate topologies",
    ],
}


def confirm_dimension_semantics(
    value: object,
) -> str:
    """Require an explicit, machine-readable user confirmation of W/L meaning."""

    if value is None:
        raise AnalysisError(
            code="DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED",
            message=(
                "Width/length meaning must be explicitly confirmed before geometry "
                "planning, generation, or export."
            ),
            details={
                "supported_dimension_semantics": list(SUPPORTED_DIMENSION_SEMANTICS),
                "generic_geometry_default": TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
                "automatic_axis_inference": False,
                "numeric_order_required": False,
            },
            next_action=(
                "Ask the user whether width is transverse to current flow and length is "
                "longitudinal to current flow. Pass the exact confirmed "
                "dimension_semantics token; do not infer or swap axes."
            ),
        )
    if not isinstance(value, str) or value not in SUPPORTED_DIMENSION_SEMANTICS:
        raise AnalysisError(
            code="INVALID_DIMENSION_SEMANTICS",
            message="dimension_semantics is not a supported explicit confirmation token.",
            details={
                "dimension_semantics": value,
                "supported_dimension_semantics": list(SUPPORTED_DIMENSION_SEMANTICS),
            },
            next_action=(
                "After confirming the meaning with the user, pass one supported token exactly."
            ),
        )

    return value


def validate_orthogonal_m1_shapes(shapes: Iterable[Mapping[str, Any]]) -> None:
    """Reject any generated M1 representation other than axis-aligned boxes."""

    for index, shape in enumerate(shapes, start=1):
        if shape.get("type") != "box":
            raise AnalysisError(
                code="NON_ORTHOGONAL_ROUTING_FORBIDDEN",
                message="Generated routing must contain horizontal/vertical geometry only.",
                details={"shape_index": index, "shape": dict(shape)},
                next_action=(
                    "Replace diagonal, tapered, or arbitrary-angle routing with axis-aligned "
                    "rectangles and 90-degree Manhattan jogs."
                ),
            )
        bbox = shape.get("bbox_um")
        if (
            not isinstance(bbox, (list, tuple))
            or len(bbox) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in bbox
            )
            or float(bbox[0]) >= float(bbox[2])
            or float(bbox[1]) >= float(bbox[3])
        ):
            raise AnalysisError(
                code="INVALID_ORTHOGONAL_ROUTING_BOX",
                message="Generated M1 routing contains an invalid axis-aligned box.",
                details={"shape_index": index, "shape": dict(shape)},
                next_action="Provide a finite positive-area [x1,y1,x2,y2] routing box.",
            )


def layout_contract_status(*, dimension_semantics: str | None = None) -> dict[str, Any]:
    """Return the non-negotiable geometry contract and honest optimization state."""

    return {
        "dimension_semantics": {
            "confirmed": dimension_semantics is not None,
            "value": dimension_semantics,
            "confirmation_required_before_geometry": True,
            "automatic_axis_inference": False,
            "generic_geometry_default": TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
            "width_axis": "transverse_to_current_flow",
            "length_axis": "longitudinal_to_current_flow",
            "numeric_order_required": False,
        },
        "routing": dict(ORTHOGONAL_ROUTING_POLICY),
        "metal_spacing": dict(PROVISIONAL_METAL_SPACING_POLICY),
        "direct_measurement_mesh_routing": dict(DIRECT_MEASUREMENT_MESH_POLICY),
        "kelvin_m1_routing": dict(KELVIN_M1_ROUTING_POLICY),
        "parasitic_resistance": {
            **PARASITIC_RESISTANCE_POLICY,
            "optimized": False,
            "optimization_evidence_available": False,
        },
    }
