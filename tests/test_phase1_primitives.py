import copy

import pytest

from klayout_mcp.design_contract import TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
from klayout_mcp.errors import AnalysisError
from klayout_mcp.manhattan_drawing import build_manhattan_drawing_plan
from klayout_mcp.phase1_primitives import (
    plan_metal_resistor_primitive,
    plan_mom_capacitor_primitive,
)
from conftest import SYNTHETIC_PROCESS_CAPABILITY


def _passive_profile():
    return copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)


def test_kelvin_resistor_primitive_has_horizontal_body_and_four_terminals() -> None:
    result = plan_metal_resistor_primitive(
        process_capability=_passive_profile(),
        device_name="example_resistor",
        layer_role="m1",
        measurement="kelvin_4t",
        width_um=0.1,
        length_um=1.0,
        terminal_size_um=0.3,
        dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
    )

    assert result["measured_body_bbox_um"] == [-0.5, -0.05, 0.5, 0.05]
    assert set(result["terminals_um"]) == {"F+", "F-", "S+", "S-"}
    assert result["measured_length_excludes_terminal_landings"] is True
    assert result["terminal_junction_positive_overlap_um"] == 0.001
    assert len(result["operations"]) == 5
    assert result["external_routing_included"] is False
    assert result["verification"]["component_count"] == 1
    assert set(result["verification"]["terminal_components"]["terminal_component_ids"].values()) == {0}


def test_mom_capacitor_is_two_disjoint_interdigitated_nets() -> None:
    result = plan_mom_capacitor_primitive(
        process_capability=_passive_profile(),
        device_name="example_capacitor",
        layer_role="m1",
        finger_width_um=0.1,
        finger_space_um=0.1,
        finger_length_um=2.0,
        finger_count=6,
        bus_width_um=0.3,
    )

    assert result["bbox_um"] == [0.0, 0.0, 2.7, 1.1]
    assert len(result["operations"]) == 8
    assert {operation["net"] for operation in result["operations"]} == {"P", "N"}
    assert result["capacitance_value_claimed"] is False
    assert result["finger_to_bus_positive_overlap_um"] == 0.1
    assert result["verification"]["component_counts"] == {"N": 1, "P": 1}
    assert result["verification"]["cross_net_minimum_space_um"] == 0.1
    assert len(set(result["verification"]["terminal_components"]["terminal_component_ids"].values())) == 2


def test_primitive_fingerprints_are_deterministic() -> None:
    arguments = {
        "process_capability": _passive_profile(),
        "device_name": "example_capacitor",
        "layer_role": "m1",
        "finger_width_um": 0.1,
        "finger_space_um": 0.1,
        "finger_length_um": 2.0,
        "finger_count": 6,
        "bus_width_um": 0.3,
    }

    first = plan_mom_capacitor_primitive(**arguments)
    second = plan_mom_capacitor_primitive(**arguments)

    assert (
        first["verification"]["geometry_fingerprint_sha256"]
        == second["verification"]["geometry_fingerprint_sha256"]
    )


def test_mom_rejects_spacing_below_explicit_process_rule() -> None:
    with pytest.raises(AnalysisError) as caught:
        plan_mom_capacitor_primitive(
            process_capability=_passive_profile(),
            device_name="example_capacitor",
            layer_role="m1",
            finger_width_um=0.1,
            finger_space_um=0.05,
            finger_length_um=2.0,
            finger_count=6,
            bus_width_um=0.3,
        )

    assert caught.value.code == "PHASE1_PRIMITIVE_BELOW_PROCESS_MINIMUM"


@pytest.mark.parametrize("kind", ["resistor", "mom"])
def test_primitive_operations_feed_the_atomic_manhattan_drawer(tmp_path, kind: str) -> None:
    if kind == "resistor":
        primitive = plan_metal_resistor_primitive(
            process_capability=_passive_profile(),
            device_name="example_resistor",
            layer_role="m1",
            measurement="direct_2t",
            width_um=0.1,
            length_um=1.0,
            terminal_size_um=0.3,
            dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
        )
    else:
        primitive = plan_mom_capacitor_primitive(
            process_capability=_passive_profile(),
            device_name="example_capacitor",
            layer_role="m1",
            finger_width_um=0.1,
            finger_space_um=0.1,
            finger_length_um=2.0,
            finger_count=6,
            bus_width_um=0.3,
        )

    plan = build_manhattan_drawing_plan(
        output_layout_path=str(tmp_path / f"{kind}.gds"),
        dbu_um=primitive["dbu_um"],
        top_cell="DUT",
        cells=primitive["cells"],
        layers=primitive["layers"],
        operations=primitive["operations"],
    )

    assert plan["top_cell"] == "DUT"
    assert all(operation["type"] == "add_box" for operation in plan["operations"])
