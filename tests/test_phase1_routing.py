from klayout_mcp.phase1_routing import plan_phase1_terminal_routes
from klayout_mcp.errors import AnalysisError
from conftest import SYNTHETIC_PROCESS_CAPABILITY, synthetic_transistor_primitive
import pytest


def test_derives_nmos_endpoints_and_avoids_twenty_four_non_target_pads() -> None:
    primitive = synthetic_transistor_primitive()
    assignments = [
        {"dut": "M1", "family": "transistor", "terminal": "S", "net": "MS", "pad": 12},
        {"dut": "M1", "family": "transistor", "terminal": "D", "net": "MD", "pad": 13},
        {"dut": "M1", "family": "transistor", "terminal": "G", "net": "MG", "pad": 11},
        {"dut": "M1", "family": "transistor", "terminal": "B", "net": "MB", "pad": 14},
    ]
    result = plan_phase1_terminal_routes(
        process_capability=SYNTHETIC_PROCESS_CAPABILITY,
        primitive_instances=[{"dut": "M1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
        terminal_assignments=assignments,
        route_specs=[
            {"connection_id": "M1:S", "width_um": 0.3, "clear_space_um": 0.3},
            {"connection_id": "M1:D", "width_um": 0.3, "clear_space_um": 0.3},
            {"connection_id": "M1:G", "width_um": 0.1, "clear_space_um": 0.1, "preferred_waypoints_um": [[840.0, 52.0]]},
            {"connection_id": "M1:B", "width_um": 0.3, "clear_space_um": 0.3, "preferred_waypoints_um": [[960.0, 3.0], [1080.0, 3.0]]},
        ],
    )

    assert result["ready_for_direct_measurement_planner"] is True
    assert result["m1_feasibility_report"]["status"] == "feasible_on_first_metal"
    assert result["routing_layer_roles_used"] == ["m1"]
    assert result["additional_metals_generated"] == []
    assert result["layer_escalation_performed"] is False
    assert all(
        route["connection_obstacle_count"] == 24
        for route in result["m1_feasibility_report"]["routes"]
    )
    starts = {
        connection["connection_id"]: connection["start_um"]
        for connection in result["routing_connections"]
    }
    assert starts == {
        "M1:S": [940.0, 27.0],
        "M1:D": [980.0, 27.0],
        "M1:G": [960.0, 52.0],
        "M1:B": [960.0, 22.0],
    }


def test_rejects_route_half_width_that_is_not_on_the_process_grid() -> None:
    primitive = synthetic_transistor_primitive()

    with pytest.raises(AnalysisError) as caught:
        plan_phase1_terminal_routes(
            process_capability=SYNTHETIC_PROCESS_CAPABILITY,
            primitive_instances=[{"dut": "M1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
            terminal_assignments=[
                {"dut": "M1", "terminal": "S", "net": "MS", "pad": 12}
            ],
            route_specs=[
                {"connection_id": "M1:S", "width_um": 0.101, "clear_space_um": 0.3}
            ],
        )

    assert caught.value.code == "PHASE1_ROUTE_OFF_GRID"
