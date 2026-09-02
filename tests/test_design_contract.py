from __future__ import annotations

import pytest

from klayout_mcp.design_contract import (
    DEVICE_SPECIFIC_W_L,
    TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
    confirm_dimension_semantics,
    layout_contract_status,
    validate_orthogonal_m1_shapes,
)
from klayout_mcp.errors import AnalysisError


def test_dimension_semantics_must_be_explicit() -> None:
    with pytest.raises(AnalysisError) as caught:
        confirm_dimension_semantics(None)

    assert caught.value.code == "DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED"
    assert caught.value.details["automatic_axis_inference"] is False
    assert caught.value.next_action


def test_directional_axis_contract_allows_width_greater_than_length() -> None:
    assert (
        confirm_dimension_semantics(TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH)
        == TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
    )


def test_device_specific_w_l_requires_exact_token_but_allows_w_greater_than_l() -> None:
    assert confirm_dimension_semantics(DEVICE_SPECIFIC_W_L) == DEVICE_SPECIFIC_W_L


def test_orthogonal_routing_validator_rejects_polygon_or_path() -> None:
    with pytest.raises(AnalysisError) as caught:
        validate_orthogonal_m1_shapes(
            [
                {
                    "type": "polygon",
                    "points_um": [[0.0, 0.0], [1.0, 1.0], [1.0, 0.0]],
                }
            ]
        )

    assert caught.value.code == "NON_ORTHOGONAL_ROUTING_FORBIDDEN"
    assert "90-degree Manhattan" in caught.value.next_action


def test_layout_contract_does_not_claim_unproven_parasitic_optimum() -> None:
    contract = layout_contract_status(
        dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
    )

    assert contract["dimension_semantics"]["numeric_order_required"] is False
    assert contract["routing"]["style"] == "orthogonal_only"
    assert contract["routing"]["diagonal_segments_allowed"] is False
    assert contract["metal_spacing"]["equal_width_space_to_width_ratio_minimum"] == 1.0
    assert contract["metal_spacing"]["example_um"] == {
        "metal_width": 0.300,
        "minimum_clear_space": 0.300,
    }
    assert contract["metal_spacing"]["intentional_electrical_junctions_excluded"] is True
    assert (
        contract["kelvin_m1_routing"]["all_other_added_m1_routing"]
        == "terminal_access_then_orthogonal_mesh_required"
    )
    assert contract["kelvin_m1_routing"]["measured_line_orientation"] == "horizontal"
    terminal_square = contract["kelvin_m1_routing"]["terminal_square"]
    assert terminal_square["size_um"] == pytest.approx(0.300)
    assert terminal_square["measured_length_excludes_terminal_squares"] is True
    assert terminal_square["force_and_sense_join_only_at_terminal_square"] is True
    keepout = contract["kelvin_m1_routing"]["measured_line_routing_keepout"]
    assert keepout["routing_above_or_below_measured_line_allowed"] is False
    assert keepout["routing_across_measured_line_allowed"] is False
    assert (
        contract["kelvin_m1_routing"]["solid_trunk_or_solid_sheet_outside_dut_allowed"]
        is False
    )
    assert (
        contract["kelvin_m1_routing"]["unnecessary_down_side_up_detour_allowed"]
        is False
    )
    assert (
        contract["kelvin_m1_routing"]["mesh_definition"]
        ["merged_route_component_must_contain_holes"]
        is True
    )
    assert contract["kelvin_m1_routing"]["mesh_definition"]["expansion_stage_count"] == 4
    assert contract["kelvin_m1_routing"]["mesh_definition"]["expansion_rail_counts"] == [
        1,
        2,
        4,
        6,
    ]
    assert (
        contract["kelvin_m1_routing"]["mesh_definition"]["expansion_style"]
        == "one_sided_from_persistent_baseline_rail"
    )
    assert (
        contract["kelvin_m1_routing"]["mesh_definition"]
        ["original_single_rail_continues_through_all_stages"]
        is True
    )
    assert (
        contract["kelvin_m1_routing"]["mesh_definition"]
        ["new_rails_may_be_added_on_both_sides"]
        is False
    )
    assert (
        contract["kelvin_m1_routing"]["mesh_definition"]
        ["jagged_or_protruding_stage_endcaps_allowed"]
        is False
    )
    interface = contract["kelvin_m1_routing"]["mesh_structure_interface_rule"]
    assert interface["preserve_intermediate_mesh_topology"] is True
    assert interface["modify_only_interface_end_geometry_when_possible"] is True
    assert interface["end_tie_must_align_to_receiving_rail_centerline"] is True
    assert interface["adjacent_parallel_end_tie_beside_receiving_rail_allowed"] is False
    assert interface["merged_interface_width_must_not_exceed_confirmed_maximum"] is True
    assert interface["current_sln001_current_force_pad_frame_rail_center_abs_x_um"] == pytest.approx(20.15)
    assert interface["current_sln001_maximum_interface_width_um"] == pytest.approx(0.3)
    bend = contract["kelvin_m1_routing"]["orthogonal_bend_rule"]
    assert bend["full_width_corner_overlap_required"] is True
    assert bend["outer_faces_must_align"] is True
    assert bend["half_width_overhang_or_recess_allowed"] is False
    kelvin = contract["kelvin_m1_routing"]
    assert kelvin["pad_roles_left_to_right"] == [
        "SENSE+",
        "FORCE+",
        "FORCE-",
        "SENSE-",
    ]
    assert kelvin["voltage_sense_terminal_route"]["direction"] == "straight_vertical"
    assert kelvin["voltage_sense_terminal_route"]["horizontal_jog_allowed"] is False
    sense_joint = kelvin["voltage_sense_vertical_horizontal_joint"]
    assert sense_joint["edge_only_or_single_rail_overlap_allowed"] is False
    assert sense_joint["topology"] == "pitch_aligned_natural_90_degree_mesh_corner"
    assert (
        sense_joint[
            "last_vertical_cross_tie_must_be_one_mesh_pitch_below_horizontal_rail"
        ]
        is True
    )
    assert sense_joint["vertical_rails_must_continue_through_full_horizontal_rail_width"] is True
    assert sense_joint["horizontal_mesh_must_extend_to_persistent_baseline_rail"] is True
    assert sense_joint["innermost_corner_requires_full_width_overlap"] is True
    assert sense_joint["current_sln001_horizontal_inner_edge_abs_x_um"] == pytest.approx(1.0)
    assert sense_joint["current_sln001_last_vertical_cross_tie_center_y_um"] == pytest.approx(19.85)
    assert sense_joint["current_sln001_horizontal_corner_rail_center_y_um"] == pytest.approx(20.85)
    assert sense_joint["current_sln001_last_row_clear_space_um"] == pytest.approx(0.7)
    assert sense_joint["current_sln001_minimum_corner_overlap_area_um2"] == pytest.approx(0.09)
    assert kelvin["outer_voltage_sense_pad_landing"][
        "current_sln001_example_ties_per_pad"
    ] == 39
    assert contract["parasitic_resistance"]["optimized"] is False
    assert contract["parasitic_resistance"]["unproven_optimum_must_not_be_claimed"] is True
    assert contract["parasitic_resistance"]["evidence_required_to_claim_optimized"]


def test_direct_measurement_contract_forbids_long_single_rail_fallback() -> None:
    contract = layout_contract_status()
    mesh = contract["direct_measurement_mesh_routing"]

    assert mesh["long_single_rail_allowed"] is False
    assert mesh["parallel_rails_required"] is True
    assert mesh["cross_ties_required"] is True
    assert mesh["merged_mesh_component_must_contain_holes"] is True
    assert mesh["silent_single_rail_fallback_allowed"] is False
    assert mesh["maximize_mesh_envelope_within_confirmed_boundary"] is True
    assert mesh["nominal_narrow_mesh_when_wider_mesh_is_feasible_allowed"] is False
    assert mesh["contact_count_must_scale_with_available_terminal_width"] is True
    assert mesh["fixed_contact_count_across_width_requires_constraint_evidence"] is True
