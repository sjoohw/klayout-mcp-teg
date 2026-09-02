from __future__ import annotations

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.manhattan_drawing import build_manhattan_drawing_plan
from klayout_mcp.server import compare_layouts, draw_manhattan_layout, inspect_layout


def _request(tmp_path, name: str = "drawing.gds") -> dict:
    return {
        "output_layout_path": str(tmp_path / name),
        "dbu_um": 0.001,
        "top_cell": "TOP",
        "cells": ["TOP", "UNIT"],
        "layers": [
            {"name": "m1", "layer": 15, "datatype": 0},
            {"name": "mask", "layer": 16, "datatype": 0},
            {"name": "xor", "layer": 17, "datatype": 0},
            {"name": "text", "layer": 100, "datatype": 0},
        ],
        "operations": [
            {
                "type": "add_box",
                "cell": "UNIT",
                "layer": "m1",
                "bbox_um": [0.0, 0.0, 1.0, 0.3],
            },
            {
                "type": "add_box",
                "cell": "UNIT",
                "layer": "mask",
                "bbox_um": [0.5, 0.0, 1.5, 0.3],
            },
            {
                "type": "boolean",
                "cell": "UNIT",
                "operation": "xor",
                "input_layers": ["m1", "mask"],
                "output_layer": "xor",
            },
            {
                "type": "add_instance",
                "parent_cell": "TOP",
                "child_cell": "UNIT",
                "origin_um": [10.0, 5.0],
                "rotation_deg": 90,
                "mirror_x": False,
            },
            {
                "type": "add_text",
                "cell": "TOP",
                "layer": "text",
                "text": "MANHATTAN",
                "origin_um": [0.0, 0.0],
            },
        ],
    }


def test_manhattan_plan_normalizes_exact_integer_dbu(tmp_path) -> None:
    plan = build_manhattan_drawing_plan(**_request(tmp_path))

    assert plan["operation"] == "draw_manhattan_layout"
    assert plan["operations"][0]["bbox_dbu"] == [0, 0, 1000, 300]
    assert plan["operations"][3]["origin_dbu"] == [10000, 5000]


def test_manhattan_plan_rejects_off_grid_coordinate(tmp_path) -> None:
    request = _request(tmp_path)
    request["operations"][0]["bbox_um"][2] = 1.0005

    with pytest.raises(AnalysisError) as caught:
        build_manhattan_drawing_plan(**request)

    assert caught.value.code == "DRAWING_COORDINATE_OFF_DBU_GRID"


def test_manhattan_plan_normalizes_only_numeric_representation_drift(tmp_path) -> None:
    request = _request(tmp_path)
    request["operations"][0]["bbox_um"][3] = 0.1 + 0.2

    plan = build_manhattan_drawing_plan(**request)

    assert plan["operations"][0]["bbox_dbu"][3] == 300


def test_manhattan_tool_requires_nonproduction_confirmation(tmp_path) -> None:
    result = draw_manhattan_layout(**_request(tmp_path))

    assert result["ok"] is False
    assert result["code"] == "DRAWING_EXPORT_REQUIRES_OPT_IN"


def test_manhattan_drawing_roundtrip_and_repeatability(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    first_request = _request(tmp_path, "first.gds")
    second_request = _request(tmp_path, "second.gds")
    first = draw_manhattan_layout(
        **first_request,
        confirm_nonproduction=True,
        klayout_executable=str(executable),
    )
    second = draw_manhattan_layout(
        **second_request,
        confirm_nonproduction=True,
        klayout_executable=str(executable),
    )

    assert first["ok"] is True, first
    assert second["ok"] is True, second
    assert first["fresh_reload_verified"] is True
    assert first["integer_dbu_geometry_verified"] is True
    assert first["top_cell_count"] == 1
    assert first["cell_count"] == 2
    assert first["operation_counts"] == {
        "add_box": 2,
        "add_text": 1,
        "add_instance": 1,
        "boolean": 1,
    }
    inventory = inspect_layout(
        first["output_layout_path"],
        klayout_executable=str(executable),
    )
    assert inventory["ok"] is True
    assert inventory["layout"]["top_cell"] == "TOP"
    assert inventory["layout"]["cell_count"] == 2
    assert inventory["shape_totals"]["box"] == 4
    assert inventory["shape_totals"]["text"] == 1
    comparison = compare_layouts(
        first["output_layout_path"],
        second["output_layout_path"],
        klayout_executable=str(executable),
    )
    assert comparison["ok"] is True
    assert comparison["comparison"]["equivalent"] is True
    assert all(
        layer["geometry_xor_clean"]
        for layer in comparison["comparison"]["layers"]
    )
    for key in ("dbu_um", "bbox_um", "layers", "cell_count", "cells", "operation_counts"):
        assert first[key] == second[key]
