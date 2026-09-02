import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.teg_planning import plan_teg_measurement_request


DEVICE_SPECIFIC_W_L = "device_specific_w_l"


def test_default_plan_exposes_all_missing_questions_without_drawing() -> None:
    result = plan_teg_measurement_request()

    assert result["ok"] is True
    assert result["planning_status"] == "questions_required"
    assert result["stop_before_drawing"] is True
    assert result["assistant_action"] == "return_required_questions_verbatim_and_wait_for_user"
    assert result["confirmation_values_must_come_from_user"] is True
    assert result["request"]["frame_um"] == [2000.0, 54.0]
    assert result["request"]["pads"] == {
        "count": 25,
        "rows": 1,
        "outline_um": [40.0, 40.0],
        "topology_status": "primary_supported_profile",
    }
    assert result["routing_policy"]["preferred_layer"] == "first_metal"
    assert "process_profile" in result["required_question_ids"]
    assert "process_profile_version" in result["required_question_ids"]
    assert "terminal_mapping" in result["required_question_ids"]
    assert "measurement_bias" in result["required_question_ids"]


def test_confirmed_primary_request_becomes_geometry_ready() -> None:
    assignments = [
        {"dut": "M1", "family": "transistor", "terminal": "G", "net": "G", "pad": 1},
        {"dut": "M1", "family": "transistor", "terminal": "D", "net": "D", "pad": 2},
        {"dut": "M1", "family": "transistor", "terminal": "S", "net": "S", "pad": 3},
        {"dut": "M1", "family": "transistor", "terminal": "B", "net": "B", "pad": 4},
    ]
    contracts = [
        {
            "dut": "M1",
            "family": "transistor",
            "measurement": "dc_4t",
            "required_terminals": ["G", "D", "S", "B"],
        }
    ]
    routing_connections = [
        {
            "net": net,
            "start_um": [100.0, y],
            "end_um": [1900.0, y],
            "width_um": 0.3,
            "clear_space_um": 0.3,
        }
        for net, y in zip(("G", "D", "S", "B"), (10.0, 20.0, 30.0, 40.0))
    ]
    result = plan_teg_measurement_request(
        device_families=["transistor"],
        process_profile="approved-pdk-v1",
        process_profile_version="v1",
        dut_count=1,
        approved_layermap=True,
        approved_design_rules=True,
        terminal_mapping_confirmed=True,
        measurement_bias_confirmed=True,
        routing_obstacles_confirmed=True,
        terminal_assignments=assignments,
        dut_terminal_contracts=contracts,
        routing_connections=routing_connections,
        dimension_semantics=DEVICE_SPECIFIC_W_L,
    )

    assert result["planning_status"] == "ready_for_geometry"
    assert result["required_questions"] == []
    assert result["routing_policy"]["m1_feasibility"] == "feasible_on_first_metal"
    assert result["direct_measurement_contract"]["implicit_mux_or_switch_allowed"] is False
    assert result["direct_measurement_contract"]["pad_budget"]["status"] == "fits"
    assert result["approved_organization_defaults_are_allowed"] is True
    assert result["transistor_context_contract"] == {
        "fill_dut_window": True,
        "measured_device_selection": "balanced_central_region",
        "default_measured_device_count": 1,
        "measurement_edge_inset_um": 5.0,
        "surrounding_device_routing": "none",
        "diffusion_sharing": "compatible_neighbors",
        "default_fill_style": "same_as_measured",
        "allowed_fill_styles": ["same_as_measured", "standard_cell_like"],
        "standard_cell_sequence": ["nmos", "pmos", "pmos", "nmos"],
        "sequence_axis": "x",
        "standard_cell_height_required": True,
    }


def test_confirmation_flag_without_assignment_records_is_not_geometry_ready() -> None:
    result = plan_teg_measurement_request(terminal_mapping_confirmed=True)

    assert "terminal_assignment_records" in result["required_question_ids"]
    assert result["direct_measurement_contract"]["pad_budget"]["status"].startswith(
        "not_evaluated"
    )


def test_assignments_without_terminal_contracts_are_not_geometry_ready() -> None:
    result = plan_teg_measurement_request(
        device_families=["transistor"],
        terminal_assignments=[
            {"dut": "M1", "family": "transistor", "terminal": "G", "net": "G", "pad": 1}
        ],
    )

    assert "dut_terminal_contracts" in result["required_question_ids"]


def test_confirmed_obstacles_without_connection_geometry_remain_a_question() -> None:
    result = plan_teg_measurement_request(routing_obstacles_confirmed=True)

    assert "routing_connection_geometry" in result["required_question_ids"]


def test_first_metal_failure_is_not_misreported_as_impossibility_proof() -> None:
    result = plan_teg_measurement_request(
        routing_obstacles_confirmed=True,
        routing_connections=[
            {
                "net": "N",
                "start_um": [1.0, 27.0],
                "end_um": [1999.0, 27.0],
                "width_um": 0.3,
                "clear_space_um": 0.3,
            }
        ],
        routing_obstacles_um=[[900.0, 0.0, 1100.0, 54.0]],
    )

    report = result["routing_policy"]["m1_feasibility_report"]
    assert result["planning_status"] == "routing_revision_required"
    assert report["feasible"] is False
    assert report["failure_proves_m1_impossible"] is False


def test_abnormal_sixteen_pad_two_row_profile_is_recognized_but_deferred() -> None:
    result = plan_teg_measurement_request(pad_count=16, pad_rows=2)

    assert (
        result["request"]["pads"]["topology_status"]
        == "recognized_abnormal_profile_deferred"
    )
    assert "abnormal_pad_profile" in result["required_question_ids"]
    assert result["planning_status"] == "questions_required"


def test_multiplexed_mode_is_phase2_and_requires_architecture() -> None:
    result = plan_teg_measurement_request(measurement_mode="multiplexed")

    assert result["phase"] == 2
    assert "phase2_mux_architecture" in result["required_question_ids"]


def test_unknown_family_is_rejected() -> None:
    try:
        plan_teg_measurement_request(device_families=["ring_oscillator"])
    except ValueError as exc:
        assert getattr(exc, "code") == "UNSUPPORTED_TEG_DEVICE_FAMILY"
    else:
        raise AssertionError("unsupported family must fail")


@pytest.mark.parametrize("field", ["process_profile", "process_profile_version"])
def test_blank_process_identity_is_rejected(field: str) -> None:
    with pytest.raises(AnalysisError) as caught:
        plan_teg_measurement_request(**{field: "   "})

    assert caught.value.code == "INVALID_TEG_PLANNING_INPUT"
