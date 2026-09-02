import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.routing_feasibility import analyze_first_metal_feasibility


def test_preferred_waypoints_are_selected_before_a_shorter_route() -> None:
    result = analyze_first_metal_feasibility(
        [
            {
                "connection_id": "M1:B",
                "net": "B",
                "start_um": [5.0, 5.0],
                "end_um": [15.0, 5.0],
                "preferred_waypoints_um": [[5.0, 2.0], [15.0, 2.0]],
                "width_um": 0.3,
                "clear_space_um": 0.3,
            }
        ],
        boundary_um=[0.0, 0.0, 20.0, 10.0],
    )

    assert result["feasible"] is True
    assert result["routes"][0]["points_um"] == [
        [5.0, 5.0],
        [5.0, 2.0],
        [15.0, 2.0],
        [15.0, 5.0],
    ]
    assert result["routes"][0]["preferred_waypoints_used"] is True


def test_diagonal_preferred_waypoint_is_rejected() -> None:
    with pytest.raises(AnalysisError) as caught:
        analyze_first_metal_feasibility(
            [
                {
                    "net": "B",
                    "start_um": [5.0, 5.0],
                    "end_um": [15.0, 5.0],
                    "preferred_waypoints_um": [[10.0, 2.0]],
                    "width_um": 0.3,
                    "clear_space_um": 0.3,
                }
            ],
            boundary_um=[0.0, 0.0, 20.0, 10.0],
        )

    assert caught.value.code == "NON_ORTHOGONAL_PREFERRED_WAYPOINTS"


def _connection(net: str, start: list[float], end: list[float]) -> dict[str, object]:
    return {
        "net": net,
        "start_um": start,
        "end_um": end,
        "width_um": 0.3,
        "clear_space_um": 0.3,
    }


def test_parallel_direct_routes_are_first_metal_feasible() -> None:
    result = analyze_first_metal_feasibility(
        [
            _connection("A", [1.0, 2.0], [9.0, 2.0]),
            _connection("B", [1.0, 4.0], [9.0, 4.0]),
        ],
        boundary_um=[0.0, 0.0, 10.0, 6.0],
    )

    assert result["status"] == "feasible_on_first_metal"
    assert result["feasible"] is True
    assert [route["bend_count"] for route in result["routes"]] == [0, 0]
    assert result["net_component_counts"] == {"A": 1, "B": 1}


def test_obstacle_causes_a_deterministic_dogleg() -> None:
    result = analyze_first_metal_feasibility(
        [_connection("A", [1.0, 3.0], [9.0, 3.0])],
        boundary_um=[0.0, 0.0, 10.0, 8.0],
        obstacles_um=[[4.0, 2.0, 6.0, 4.0]],
    )

    assert result["feasible"] is True
    assert result["routes"][0]["bend_count"] == 2
    assert result["routes"][0]["length_um"] > 8.0


def test_full_height_wall_returns_bounded_failure_not_false_proof() -> None:
    result = analyze_first_metal_feasibility(
        [_connection("A", [1.0, 3.0], [9.0, 3.0])],
        boundary_um=[0.0, 0.0, 10.0, 6.0],
        obstacles_um=[[4.0, 0.0, 6.0, 6.0]],
    )

    assert result["status"] == "not_found_bounded_search"
    assert result["feasible"] is False
    assert result["failure_proves_m1_impossible"] is False
    assert result["search_scope_is_universal_m1_proof"] is False
    assert result["retained_candidate_space_exhausted"] is True
    assert result["search_geometry"]["global_obstacles_um"] == [
        [4.0, 0.0, 6.0, 6.0]
    ]
    assert result["blockers"][0]["reason"] == (
        "no_candidate_within_declared_search_scope"
    )


def test_empty_connections_are_not_claimed_feasible() -> None:
    result = analyze_first_metal_feasibility([], boundary_um=[0.0, 0.0, 10.0, 6.0])

    assert result["status"] == "not_evaluated_missing_connections"
    assert result["feasible"] is None


def test_shared_net_branches_form_one_connected_tree() -> None:
    connections = [
        {
            **_connection("BODY", [1.0, 2.0], [5.0, 3.0]),
            "connection_id": "M1:B",
        },
        {
            **_connection("BODY", [1.0, 4.0], [5.0, 3.0]),
            "connection_id": "M2:B",
        },
    ]
    result = analyze_first_metal_feasibility(
        connections,
        boundary_um=[0.0, 0.0, 6.0, 6.0],
    )
    repeated = analyze_first_metal_feasibility(
        connections,
        boundary_um=[0.0, 0.0, 6.0, 6.0],
    )

    assert result["feasible"] is True
    assert result["net_component_counts"] == {"BODY": 1}
    assert [route["connection_id"] for route in result["routes"]] == ["M1:B", "M2:B"]
    assert result["route_fingerprint_sha256"] == repeated["route_fingerprint_sha256"]
    assert result["search_evidence_fingerprint_sha256"] == repeated[
        "search_evidence_fingerprint_sha256"
    ]
    assert result["search_configuration"]["routing_layer_scope"] == "first_metal_only"


def test_candidate_cap_is_explicit_in_failure_evidence() -> None:
    result = analyze_first_metal_feasibility(
        [
            _connection("A", [1.0, 2.0], [9.0, 2.0]),
            _connection("B", [1.0, 2.4], [9.0, 2.4]),
        ],
        boundary_um=[0.0, 0.0, 10.0, 6.0],
        max_candidates_per_connection=1,
    )

    assert result["candidate_cap_truncated"] is True
    assert result["search_configuration"]["max_candidates_per_connection"] == 1
    assert len(result["search_evidence_fingerprint_sha256"]) == 64
