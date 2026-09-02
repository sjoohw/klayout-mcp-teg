from __future__ import annotations

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.mesh_routing import (
    synthesize_maximum_contact_array,
    synthesize_staged_mesh_segment,
)


def _mesh(**overrides):
    request = {
        "dbu_um": 0.0025,
        "start_um": [0.0, 0.0],
        "end_um": [20.0, 0.0],
        "corridor_um": [0.0, -2.0, 20.0, 2.0],
        "rail_width_um": 0.3,
        "rail_space_um": 0.3,
        "landing_span_um": 0.3,
        "cross_tie_pitch_um": 1.2,
    }
    request.update(overrides)
    return synthesize_staged_mesh_segment(**request)


def test_staged_mesh_is_maximum_envelope_and_deterministic() -> None:
    first = _mesh()
    second = _mesh()

    assert first == second
    evidence = first["evidence"]
    assert evidence["rail_count"] == 7
    assert evidence["used_transverse_span_dbu"] == 1560
    assert evidence["single_rail_fallback_allowed"] is False
    assert evidence["optimization_status"] == "geometry_maximized_not_pex_proven"
    assert evidence["cross_tie_count"] > 2


def test_staged_mesh_rotates_without_changing_topology() -> None:
    horizontal = _mesh()
    vertical = _mesh(
        start_um=[0.0, 0.0],
        end_um=[0.0, 20.0],
        corridor_um=[-2.0, 0.0, 2.0, 20.0],
    )

    for field in ("rail_count", "used_transverse_span_dbu", "cross_tie_count"):
        assert horizontal["evidence"][field] == vertical["evidence"][field]
    assert horizontal["evidence"]["direction"] == "+x"
    assert vertical["evidence"]["direction"] == "+y"


@pytest.mark.parametrize(
    ("end_um", "corridor_um", "direction"),
    [
        ([-20.0, 0.0], [-20.0, -2.0, 0.0, 2.0], "-x"),
        ([0.0, -20.0], [-2.0, -20.0, 2.0, 0.0], "-y"),
    ],
)
def test_staged_mesh_reverse_directions_preserve_topology(
    end_um, corridor_um, direction
) -> None:
    result = _mesh(end_um=end_um, corridor_um=corridor_um)

    assert result["evidence"]["direction"] == direction
    assert result["evidence"]["rail_count"] == 7
    assert result["evidence"]["cross_tie_count"] > 2


def test_staged_mesh_is_not_bound_to_one_process_grid_or_dimensions() -> None:
    result = synthesize_staged_mesh_segment(
        dbu_um=0.001,
        start_um=[1.0, 2.0],
        end_um=[13.0, 2.0],
        corridor_um=[1.0, 1.0, 13.0, 3.0],
        rail_width_um=0.2,
        rail_space_um=0.2,
        landing_span_um=0.6,
        transition_guard_um=0.4,
        cross_tie_pitch_um=0.8,
    )

    assert result["evidence"]["dbu_um"] == 0.001
    assert result["evidence"]["rail_count"] == 5
    assert result["evidence"]["rail_width_dbu"] == 200


def test_staged_mesh_without_receiver_keeps_end_tie_inside_corridor() -> None:
    result = _mesh(receiving_tie_present=False)

    assert all(box[0] >= 0 and box[2] <= 8000 for box in result["boxes_dbu"])
    assert any(box[2] == 8000 and box[2] - box[0] == 120 for box in result["boxes_dbu"])


def test_staged_mesh_rejects_nonfinite_dbu() -> None:
    with pytest.raises(AnalysisError) as caught:
        _mesh(dbu_um=float("nan"))
    assert caught.value.code == "INVALID_MESH_SYNTHESIS_DBU"


def test_staged_mesh_fails_closed_when_corridor_is_short_or_narrow() -> None:
    with pytest.raises(AnalysisError) as short:
        _mesh(end_um=[2.0, 0.0])
    assert short.value.code == "INSUFFICIENT_CORRIDOR_FOR_MESH"

    with pytest.raises(AnalysisError) as narrow:
        _mesh(corridor_um=[0.0, -0.15, 20.0, 0.15])
    assert narrow.value.code == "MESH_CORRIDOR_TOO_NARROW"


def test_narrow_terminal_landing_keeps_baseline_electrically_attached() -> None:
    result = _mesh(landing_span_um=0.135)

    assert [0, 0] in result["evidence"]["stage_start_dbu_by_offset"]
    assert result["evidence"]["effective_transition_landing_span_dbu"] == 120
    baseline_boxes = [
        box
        for box in result["boxes_dbu"]
        if box[0] == 0 and box[1] <= 0 < box[3]
    ]
    assert baseline_boxes


@pytest.mark.parametrize(
    ("width_um", "expected_count"),
    [(0.5, 2), (1.0, 5), (2.0, 10)],
)
def test_maximum_contact_array_scales_with_terminal_width(
    width_um: float, expected_count: int
) -> None:
    result = synthesize_maximum_contact_array(
        dbu_um=0.0025,
        array_center_um=[0.0, 0.0],
        array_axis="y",
        available_width_um=width_um,
        contact_size_um=0.065,
        contact_space_um=0.075,
        active_enclosure_um=0.005,
        metal_enclosure_um=0.035,
        metal_space_um=0.065,
        alignment="away_from_positive",
        neighbor_metal_near_edge_um=width_um / 2.0 + 0.025,
        neighbor_side="positive",
        neighbor_clearance_um=0.065,
    )

    assert result["evidence"]["legal_contact_count"] == expected_count
    assert len(result["contact_boxes_dbu"]) == expected_count
    assert len(result["metal_boxes_dbu"]) == expected_count


def test_contact_array_requires_complete_neighbor_constraint() -> None:
    with pytest.raises(AnalysisError) as caught:
        synthesize_maximum_contact_array(
            dbu_um=0.0025,
            array_center_um=[0.0, 0.0],
            array_axis="y",
            available_width_um=1.0,
            contact_size_um=0.065,
            contact_space_um=0.075,
            active_enclosure_um=0.005,
            metal_enclosure_um=0.035,
            metal_space_um=0.065,
            neighbor_metal_near_edge_um=0.525,
        )
    assert caught.value.code == "INCOMPLETE_CONTACT_NEIGHBOR_CONSTRAINT"


def test_horizontal_contact_array_honors_negative_neighbor_clearance() -> None:
    result = synthesize_maximum_contact_array(
        dbu_um=0.0025,
        array_center_um=[0.0, 0.0],
        array_axis="x",
        available_width_um=1.0,
        contact_size_um=0.065,
        contact_space_um=0.075,
        active_enclosure_um=0.005,
        metal_enclosure_um=0.035,
        metal_space_um=0.065,
        alignment="away_from_negative",
        neighbor_metal_near_edge_um=-0.525,
        neighbor_side="negative",
        neighbor_clearance_um=0.065,
    )

    assert result["evidence"]["array_axis"] == "x"
    assert result["evidence"]["alignment"] == "away_from_negative"
    assert result["evidence"]["legal_contact_count"] == 5
    assert result["evidence"]["optimization_status"] == (
        "geometry_maximized_not_pex_proven"
    )


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"array_axis": "diagonal"}, "INVALID_CONTACT_ARRAY_AXIS"),
        ({"alignment": "nearest"}, "INVALID_CONTACT_ARRAY_ALIGNMENT"),
        (
            {"neighbor_metal_near_edge_um": 0.5, "neighbor_side": "top", "neighbor_clearance_um": 0.1},
            "INVALID_CONTACT_NEIGHBOR_SIDE",
        ),
    ],
)
def test_contact_array_rejects_ambiguous_direction_tokens(overrides, code) -> None:
    request = {
        "dbu_um": 0.0025,
        "array_center_um": [0.0, 0.0],
        "array_axis": "y",
        "available_width_um": 1.0,
        "contact_size_um": 0.065,
        "contact_space_um": 0.075,
        "active_enclosure_um": 0.005,
        "metal_enclosure_um": 0.035,
        "metal_space_um": 0.065,
    }
    request.update(overrides)

    with pytest.raises(AnalysisError) as caught:
        synthesize_maximum_contact_array(**request)
    assert caught.value.code == code
