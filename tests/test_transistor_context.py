from __future__ import annotations

import math

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.transistor_context import plan_single_transistor_context


def test_default_context_max_fills_even_grid_and_routes_one_central_region_site() -> None:
    result = plan_single_transistor_context(
        dut_window_um=[-14.0, -14.0, 14.0, 14.0],
        device_footprint_um=[1.0, 1.0],
        pitch_x_um=2.0,
        pitch_y_um=2.0,
        measured_device_type="nmos",
    )

    assert (result["rows"], result["columns"], result["site_count"]) == (14, 14, 196)
    assert result["fill_style"] == "same_as_measured"
    assert result["measurement_edge_inset_um"] == 5.0
    measured = [site for site in result["sites"] if site["is_measured_dut"]]
    assert len(measured) == 1
    assert math.hypot(*measured[0]["origin_um"]) <= math.sqrt(2.0)
    assert measured[0]["terminal_routing"] == "measured_only"
    assert all(
        site["terminal_routing"] == "none"
        for site in result["sites"]
        if not site["is_measured_dut"]
    )


@pytest.mark.parametrize("measured_device_count", [5, 10])
def test_multiple_measured_devices_are_balanced_and_stay_inside_array_inset(
    measured_device_count: int,
) -> None:
    result = plan_single_transistor_context(
        dut_window_um=[-20.0, -20.0, 20.0, 20.0],
        device_footprint_um=[1.0, 1.0],
        pitch_x_um=2.0,
        pitch_y_um=2.0,
        measured_device_type="nmos",
        measured_device_count=measured_device_count,
    )

    measured = [site for site in result["sites"] if site["is_measured_dut"]]
    assert len(measured) == measured_device_count
    assert result["measured_site_count"] == measured_device_count
    selection_region = result["measurement_selection"]["selection_region_um"]
    assert all(
        selection_region[0] <= site["origin_um"][0] <= selection_region[2]
        and selection_region[1] <= site["origin_um"][1] <= selection_region[3]
        for site in measured
    )
    assert "measured_site" not in result


def test_standard_cell_context_requires_height_and_phases_sequence_to_measured_type() -> None:
    result = plan_single_transistor_context(
        dut_window_um=[-5.0, -1.0, 5.0, 1.0],
        device_footprint_um=[1.0, 1.0],
        pitch_x_um=2.0,
        measured_device_type="pmos",
        fill_style="standard_cell_like",
        standard_cell_height_um=2.0,
        measurement_edge_inset_um=0.0,
    )

    row = [site for site in result["sites"] if site["row"] == 0]
    assert [site["device_type"] for site in row] == [
        "nmos",
        "nmos",
        "pmos",
        "pmos",
        "nmos",
    ]
    assert result["standard_cell_height_um"] == 2.0
    assert row[2]["is_measured_dut"] is True
    assert row[2]["share_diffusion_with_left"] is False
    assert row[3]["share_diffusion_with_left"] is True


def test_standard_cell_context_rejects_missing_cell_height() -> None:
    with pytest.raises(AnalysisError) as caught:
        plan_single_transistor_context(
            dut_window_um=[-5.0, -5.0, 5.0, 5.0],
            device_footprint_um=[1.0, 1.0],
            pitch_x_um=2.0,
            measured_device_type="nmos",
            fill_style="standard_cell_like",
        )

    assert caught.value.code == "STANDARD_CELL_HEIGHT_REQUIRED"
