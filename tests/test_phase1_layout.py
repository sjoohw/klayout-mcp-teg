import copy
import hashlib
import json

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.phase1_layout import compose_phase1_direct_layout
from klayout_mcp.phase1_primitives import (
    plan_metal_resistor_primitive,
    plan_mom_capacitor_primitive,
)
from klayout_mcp.phase1_routing import plan_phase1_terminal_routes
from klayout_mcp.teg_planning import plan_teg_measurement_request
from conftest import SYNTHETIC_PROCESS_CAPABILITY, synthetic_transistor_primitive


def _refresh_route_fingerprint(request_plan) -> None:
    report = request_plan["routing_policy"]["m1_feasibility_report"]
    report["route_fingerprint_sha256"] = hashlib.sha256(
        json.dumps(report["routes"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ready_mom_inputs():
    profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    profile["devices"]["example_capacitor"] = {
        "family": "capacitor",
        "terminals": ["P", "N"],
        "measurements": ["capacitance_2t"],
        "doe_axes": ["area_um2", "perimeter_um"],
        "required_layers": ["m1"],
        "geometry_source": "rule_synthesized",
    }
    primitive = plan_mom_capacitor_primitive(
        process_capability=profile,
        device_name="example_capacitor",
        layer_role="m1",
        finger_width_um=0.1,
        finger_space_um=0.1,
        finger_length_um=2.0,
        finger_count=6,
        bus_width_um=0.3,
    )
    request_plan = plan_teg_measurement_request(
        device_families=["capacitor"],
        process_profile="synthetic_test_process",
        process_profile_version="test-v1",
        dut_count=1,
        approved_layermap=True,
        approved_design_rules=True,
        terminal_mapping_confirmed=True,
        measurement_bias_confirmed=True,
        routing_obstacles_confirmed=True,
        dimension_semantics="device_specific_w_l",
        terminal_assignments=[
            {"dut": "C1", "family": "capacitor", "terminal": "P", "net": "CP", "pad": 12},
            {"dut": "C1", "family": "capacitor", "terminal": "N", "net": "CN", "pad": 13},
        ],
        dut_terminal_contracts=[
            {
                "dut": "C1",
                "family": "capacitor",
                "measurement": "capacitance_2t",
                "required_terminals": ["P", "N"],
            }
        ],
        routing_connections=[
            {
                "connection_id": "C1:P",
                "net": "CP",
                "start_um": [959.0, 27.0],
                "end_um": [920.0, 27.0],
                "width_um": 0.3,
                "clear_space_um": 0.3,
            },
            {
                "connection_id": "C1:N",
                "net": "CN",
                "start_um": [961.7, 27.0],
                "end_um": [1000.0, 27.0],
                "width_um": 0.3,
                "clear_space_um": 0.3,
            },
        ],
    )
    assert request_plan["planning_status"] == "ready_for_geometry"
    return profile, primitive, request_plan


def test_composes_25_pad_direct_resistor_atomic_drawing_request(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    result = compose_phase1_direct_layout(
        output_layout_path=str(tmp_path / "resistor-teg.gds"),
        top_cell="PHASE1_DIRECT_TEG",
        process_capability=profile,
        request_plan=request_plan,
        primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
        pad_rail_width_um=0.3,
    )

    assert result["ready_for_klayout_generation"] is True
    assert result["fresh_reload_verified"] is False
    assert result["frame_um"] == [2000.0, 54.0]
    assert result["pad_count"] == 25
    assert result["pad_rail_clear_space_um"] == 0.3
    assert result["pad_centers_um"]["12"] == [920.0, 27.0]
    assert result["pad_centers_um"]["13"] == [1000.0, 27.0]
    assert {item["connection_id"] for item in result["terminal_routes"]} == {"R1:N", "R1:P"}
    assert result["connectivity_projection"]["primitive_terminal_component_overlap_verified"] is True
    assert all(
        item["positive_area_terminal_overlap_verified"]
        for item in result["terminal_routes"]
    )
    assert result["drawing_request"]["top_cell"] == "PHASE1_DIRECT_TEG"


def test_semantic_fingerprint_does_not_depend_on_output_path(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    common = {
        "top_cell": "PHASE1_DIRECT_TEG",
        "process_capability": profile,
        "request_plan": request_plan,
        "primitive_instances": [{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
        "pad_rail_width_um": 0.3,
    }
    first = compose_phase1_direct_layout(
        output_layout_path=str(tmp_path / "first.gds"), **common
    )
    second = compose_phase1_direct_layout(
        output_layout_path=str(tmp_path / "second.gds"), **common
    )

    assert first["drawing_plan_fingerprint_sha256"] == second["drawing_plan_fingerprint_sha256"]


def test_route_crossing_an_unassigned_pad_is_rejected(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    corrupted = copy.deepcopy(request_plan)
    route = next(
        item
        for item in corrupted["routing_policy"]["m1_feasibility_report"]["routes"]
        if item["connection_id"] == "R1:P"
    )
    route["points_um"] = [[960.65, 27.0], [840.0, 27.0], [1000.0, 27.0]]
    _refresh_route_fingerprint(corrupted)

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "bad.gds"),
            top_cell="PHASE1_DIRECT_TEG",
            process_capability=profile,
            request_plan=corrupted,
            primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "ROUTE_INTERSECTS_UNASSIGNED_PAD"


def test_route_clear_space_is_rechecked_against_process_table(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    corrupted = copy.deepcopy(request_plan)
    for route in corrupted["routing_policy"]["m1_feasibility_report"]["routes"]:
        route["clear_space_um"] = 0.05
    _refresh_route_fingerprint(corrupted)

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "bad-space.gds"),
            top_cell="PHASE1_DIRECT_TEG",
            process_capability=profile,
            request_plan=corrupted,
            primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "ROUTE_CLEAR_SPACE_BELOW_PROCESS_RULE"
    assert caught.value.details["required_clear_space_um"] == 0.1


def test_bounded_search_evidence_tamper_is_rejected(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    corrupted = copy.deepcopy(request_plan)
    report = corrupted["routing_policy"]["m1_feasibility_report"]
    report["search_evidence"]["max_candidates_per_connection"] = 1

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "tampered-search.gds"),
            top_cell="PHASE1_DIRECT_TEG",
            process_capability=profile,
            request_plan=corrupted,
            primitive_instances=[
                {"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}
            ],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "M1_SEARCH_EVIDENCE_FINGERPRINT_MISMATCH"


def test_rejects_route_mutation_after_feasibility_verification(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    corrupted = copy.deepcopy(request_plan)
    corrupted["routing_policy"]["m1_feasibility_report"]["routes"][0]["width_um"] = 0.2

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "tampered-route.gds"),
            top_cell="PHASE1_DIRECT_TEG",
            process_capability=profile,
            request_plan=corrupted,
            primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "M1_ROUTE_FINGERPRINT_MISMATCH"


def test_rejects_primitive_mutation_after_local_verification(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    corrupted = copy.deepcopy(primitive)
    corrupted["operations"][0]["bbox_um"][2] += 0.1

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "tampered-primitive.gds"),
            top_cell="PHASE1_DIRECT_TEG",
            process_capability=profile,
            request_plan=request_plan,
            primitive_instances=[{"dut": "R1", "primitive": corrupted, "origin_um": [960.0, 27.0]}],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "PRIMITIVE_GEOMETRY_FINGERPRINT_MISMATCH"


def test_rejects_request_planned_for_a_different_process_version(tmp_path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    mismatched = copy.deepcopy(request_plan)
    mismatched["request"]["process_profile_version"] = "stale-version"

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "wrong-version.gds"),
            top_cell="PHASE1_DIRECT_TEG",
            process_capability=profile,
            request_plan=mismatched,
            primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "REQUEST_PROCESS_CAPABILITY_MISMATCH"


def test_composes_mom_capacitor_through_the_same_25_pad_flow(tmp_path) -> None:
    profile, primitive, request_plan = _ready_mom_inputs()
    result = compose_phase1_direct_layout(
        output_layout_path=str(tmp_path / "mom-teg.gds"),
        top_cell="PHASE1_MOM_TEG",
        process_capability=profile,
        request_plan=request_plan,
        primitive_instances=[
            {"dut": "C1", "primitive": primitive, "origin_um": [959.0, 26.45]}
        ],
        pad_rail_width_um=0.3,
    )

    assert result["ready_for_klayout_generation"] is True
    assert result["primitive_duts"] == ["C1"]
    assert {item["pad"] for item in result["terminal_routes"]} == {12, 13}


def test_route_cannot_cross_another_terminal_component_of_its_own_dut(tmp_path) -> None:
    profile, primitive, request_plan = _ready_mom_inputs()
    corrupted = copy.deepcopy(request_plan)
    route = next(
        item
        for item in corrupted["routing_policy"]["m1_feasibility_report"]["routes"]
        if item["connection_id"] == "C1:P"
    )
    route["points_um"] = [[959.0, 27.0], [961.7, 27.0], [920.0, 27.0]]
    _refresh_route_fingerprint(corrupted)

    with pytest.raises(AnalysisError) as caught:
        compose_phase1_direct_layout(
            output_layout_path=str(tmp_path / "mom-short.gds"),
            top_cell="PHASE1_MOM_TEG",
            process_capability=profile,
            request_plan=corrupted,
            primitive_instances=[
                {"dut": "C1", "primitive": primitive, "origin_um": [959.0, 26.45]}
            ],
            pad_rail_width_um=0.3,
        )

    assert caught.value.code == "ROUTE_SHORTS_DUT_TERMINAL_COMPONENTS"


def test_composes_four_terminal_nmos_through_25_pad_m1_flow(tmp_path) -> None:
    primitive = synthetic_transistor_primitive()
    assignments = [
        {"dut": "M1", "family": "transistor", "terminal": "S", "net": "MS", "pad": 12},
        {"dut": "M1", "family": "transistor", "terminal": "D", "net": "MD", "pad": 13},
        {"dut": "M1", "family": "transistor", "terminal": "G", "net": "MG", "pad": 11},
        {"dut": "M1", "family": "transistor", "terminal": "B", "net": "MB", "pad": 14},
    ]
    placement = {"dut": "M1", "primitive": primitive, "origin_um": [960.0, 27.0]}
    route_plan = plan_phase1_terminal_routes(
        process_capability=SYNTHETIC_PROCESS_CAPABILITY,
        primitive_instances=[placement],
        terminal_assignments=assignments,
        route_specs=[
            {"connection_id": "M1:S", "width_um": 0.3, "clear_space_um": 0.3},
            {"connection_id": "M1:D", "width_um": 0.3, "clear_space_um": 0.3},
            {"connection_id": "M1:G", "width_um": 0.1, "clear_space_um": 0.1, "preferred_waypoints_um": [[840.0, 52.0]]},
            {"connection_id": "M1:B", "width_um": 0.3, "clear_space_um": 0.3, "preferred_waypoints_um": [[960.0, 3.0], [1080.0, 3.0]]},
        ],
    )
    request_plan = plan_teg_measurement_request(
        device_families=["transistor"],
        process_profile="synthetic_test_process",
        process_profile_version="test-v1",
        dut_count=1,
        approved_layermap=True,
        approved_design_rules=True,
        terminal_mapping_confirmed=True,
        measurement_bias_confirmed=True,
        routing_obstacles_confirmed=True,
        dimension_semantics="device_specific_w_l",
        terminal_assignments=assignments,
        dut_terminal_contracts=[
            {"dut": "M1", "family": "transistor", "measurement": "dc_4t", "required_terminals": ["G", "D", "S", "B"]}
        ],
        routing_connections=route_plan["routing_connections"],
    )
    assert request_plan["planning_status"] == "ready_for_geometry"

    result = compose_phase1_direct_layout(
        output_layout_path=str(tmp_path / "nmos-teg.gds"),
        top_cell="PHASE1_NMOS_TEG",
        process_capability=SYNTHETIC_PROCESS_CAPABILITY,
        request_plan=request_plan,
        primitive_instances=[placement],
        pad_rail_width_um=0.3,
    )

    assert result["ready_for_klayout_generation"] is True
    assert result["primitive_duts"] == ["M1"]
    assert {item["terminal"] for item in result["terminal_routes"]} == {"G", "D", "S", "B"}
    assert all(
        len(item["route_touched_component_ids"]) == 1
        for item in result["terminal_routes"]
    )
    route_report = request_plan["routing_policy"]["m1_feasibility_report"]
    used = {route["connection_id"]: route["preferred_waypoints_used"] for route in route_report["routes"]}
    assert used["M1:G"] is True
    assert used["M1:B"] is True
