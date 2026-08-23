from __future__ import annotations

import pytest

from klayout_mcp.dut_geometry import (
    DutParameters,
    build_dut_geometry,
    describe_dut_pcell_contract,
)
from klayout_mcp.errors import AnalysisError
from klayout_mcp.geometry import Box


def test_default_dut_geometry_contract() -> None:
    result = build_dut_geometry()

    assert result.total_units == 32
    assert len(result.routed_indices) == 10
    assert len(result.active_boxes_um) == 32
    assert len(result.poly_boxes_um) == 32
    assert len(result.contact_boxes_um) == 64

    # Check 4 terminal contracts
    terminals = result.terminals
    assert "source" in terminals
    assert "drain" in terminals
    assert "gate" in terminals
    assert "body" in terminals

    assert terminals["source"]["boundary_side"] == "left"
    assert terminals["source"]["anchor_um"][0] == pytest.approx(-20.0)
    assert terminals["source"]["name"] == "S"
    assert terminals["source"]["layer_role"] == "m1"
    assert terminals["source"]["direction_vector"] == [-1, 0]
    assert terminals["source"]["minimum_overlap_um"] == pytest.approx(0.2)

    assert terminals["drain"]["boundary_side"] == "right"
    assert terminals["drain"]["anchor_um"][0] == pytest.approx(20.0)

    assert terminals["gate"]["boundary_side"] == "top"
    assert terminals["gate"]["anchor_um"][1] == pytest.approx(20.0)

    assert terminals["body"]["boundary_side"] == "bottom"
    assert terminals["body"]["anchor_um"][1] == pytest.approx(-20.0)

    payload = result.to_dict()
    assert payload["production_ready"] is False
    assert payload["geometry_status"] == "conceptual_scaffold"
    assert payload["parameters"]["routed_device_count"] == 10


def test_single_transistor_unit() -> None:
    params = DutParameters(array_rows=1, array_cols=1, routed_device_count=1)
    result = build_dut_geometry(params)

    assert result.total_units == 1
    assert result.routed_indices == [1]
    assert len(result.active_boxes_um) == 1


def test_routed_device_count_changes_m1_inventory() -> None:
    one = build_dut_geometry(
        DutParameters(array_rows=2, array_cols=2, routed_device_count=1)
    )
    four = build_dut_geometry(
        DutParameters(array_rows=2, array_cols=2, routed_device_count=4)
    )

    assert one.routed_indices != four.routed_indices
    assert len(one.m1_shapes_um) == 10
    assert len(four.m1_shapes_um) == 16
    assert len(four.m1_shapes_um) - len(one.m1_shapes_um) == 2 * (4 - 1)


def test_device_exceeds_window_raises_structured_error() -> None:
    # 20 rows x 20 cols with 2.0 pitch exceeds 35x40 um device window
    params = DutParameters(
        array_rows=20,
        array_cols=20,
        pitch_x_um=2.0,
        pitch_y_um=2.0,
        routed_device_count=10,
    )

    with pytest.raises(AnalysisError) as exc:
        build_dut_geometry(params)

    assert exc.value.code == "DEVICE_EXCEEDS_WINDOW"
    assert "array_bbox_um" in exc.value.details


def test_invalid_parameters_raise_error() -> None:
    with pytest.raises(AnalysisError) as exc:
        DutParameters(w_um=-1.0).validate()
    assert exc.value.code == "INVALID_DUT_DIMENSIONS"

    with pytest.raises(AnalysisError) as exc:
        DutParameters(array_rows=0).validate()
    assert exc.value.code == "INVALID_ARRAY_SIZE"

    with pytest.raises(AnalysisError) as exc:
        DutParameters(array_rows=2, array_cols=2, routed_device_count=10).validate()
    assert exc.value.code == "INVALID_ROUTED_COUNT"

    with pytest.raises(AnalysisError) as exc:
        DutParameters(pitch_x_um=0.0).validate()
    assert exc.value.code == "INVALID_PITCH"
    assert exc.value.next_action


def test_custom_device_window_and_routing_boundary() -> None:
    params = DutParameters(
        w_um=2.0,
        l_um=0.2,
        array_rows=2,
        array_cols=4,
        pitch_x_um=3.0,
        pitch_y_um=3.0,
        routed_device_count=4,
        device_window_um=Box(-15.0, -15.0, 15.0, 15.0),
        routing_boundary_um=Box(-18.0, -18.0, 18.0, 18.0),
        m1_overlap_um=0.3,
    )
    result = build_dut_geometry(params)


    assert result.total_units == 8
    assert result.terminals["source"]["anchor_um"] == [-18.0, 0.0]
    assert result.terminals["drain"]["anchor_um"] == [18.0, 0.0]
    assert result.terminals["gate"]["anchor_um"] == [0.0, 18.0]
    assert result.terminals["body"]["anchor_um"] == [0.0, -18.0]


def test_box_sequences_are_normalized_at_parameter_boundary() -> None:
    params = DutParameters(
        device_window_um=[-17.5, -20.0, 17.5, 20.0],
        routing_boundary_um=(-20.0, -20.0, 20.0, 20.0),
    )

    params.validate()

    assert isinstance(params.device_window_um, Box)
    assert isinstance(params.routing_boundary_um, Box)
    assert params.to_dict()["device_window_um"] == [-17.5, -20.0, 17.5, 20.0]


def test_vertical_collectors_stay_inside_routing_boundary() -> None:
    params = DutParameters(array_rows=20, array_cols=4, routed_device_count=10)
    result = build_dut_geometry(params)
    named = {
        shape.get("name"): shape["bbox_um"]
        for shape in result.m1_shapes_um
        if shape.get("name")
    }

    assert named["gate_collector"][3] <= params.routing_boundary_um.y2
    assert named["body_collector"][1] >= params.routing_boundary_um.y1
    assert named["gate_landing_stub"][1] < named["gate_landing_stub"][3]
    assert named["body_landing_stub"][1] < named["body_landing_stub"][3]


def test_contract_is_explicit_about_missing_production_inputs() -> None:
    contract = describe_dut_pcell_contract()

    assert contract["contract_version"] == 1
    assert contract["pcell_name"] == "DutTransistorArray"
    assert contract["production_ready"] is False
    assert [item["name"] for item in contract["parameter_schema"]] == [
        "w_um",
        "l_um",
        "array_rows",
        "array_cols",
        "pitch_x_um",
        "pitch_y_um",
        "routed_device_count",
        "m1_width_um",
        "m1_overlap_um",
        "device_window_um",
        "routing_boundary_um",
    ]
    assert set(contract["terminals"]) == {"source", "drain", "gate", "body"}
    assert "sample DUT GDS/OAS and parameter explanation" in contract["required_production_inputs"]
    assert contract["next_action"]


@pytest.mark.parametrize("value", [0.0, float("nan"), float("inf")])
def test_landing_overlap_must_be_positive_and_finite(value: float) -> None:
    with pytest.raises(AnalysisError) as exc:
        DutParameters(m1_overlap_um=value).validate()

    assert exc.value.code in {"INVALID_DUT_PARAMETER", "INVALID_ROUTING_PARAMETERS"}


def test_device_window_must_fit_routing_boundary() -> None:
    with pytest.raises(AnalysisError) as exc:
        DutParameters(
            device_window_um=Box(-21.0, -20.0, 17.5, 20.0),
        ).validate()

    assert exc.value.code == "DEVICE_WINDOW_OUTSIDE_ROUTING_BOUNDARY"


def test_selecting_all_units_does_not_relax_five_micron_inset() -> None:
    with pytest.raises(AnalysisError) as exc:
        build_dut_geometry(
            DutParameters(
                array_rows=1,
                array_cols=3,
                pitch_x_um=14.0,
                routed_device_count=3,
            )
        )

    assert exc.value.code == "ROUTED_DEVICE_COUNT_EXCEEDS_ELIGIBLE"
