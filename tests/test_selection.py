import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.selection import plan_transistor_array, select_routed_units


def _grid(rows: int, columns: int, pitch: float = 5.0) -> list[list[float]]:
    x0 = -(columns - 1) * pitch / 2.0
    y0 = -(rows - 1) * pitch / 2.0
    return [
        [x0 + column * pitch, y0 + row * pitch]
        for row in range(rows)
        for column in range(columns)
    ]


def test_one_routed_unit_selects_center_nearest_with_index_tie_break() -> None:
    centers = [[-1.0, 0.0], [1.0, 0.0], [9.0, 9.0]]

    result = select_routed_units(centers, [-10.0, -10.0, 10.0, 10.0], 1, 5.0)

    assert result["selected_unit_indices"] == [1]
    assert result["assignments"][0]["target_um"] == [0.0, 0.0]


def test_ten_routed_units_are_deterministic_and_spread_across_array() -> None:
    centers = _grid(7, 7)

    first = select_routed_units(centers, [-17.5, -20.0, 17.5, 20.0], 10)
    second = select_routed_units(centers, [-17.5, -20.0, 17.5, 20.0], 10)

    assert first == second
    assert len(first["selected_unit_indices"]) == 10
    selected_centers = [item["unit_center_um"] for item in first["assignments"]]
    assert min(point[0] for point in selected_centers) < 0 < max(
        point[0] for point in selected_centers
    )
    assert min(point[1] for point in selected_centers) < 0 < max(
        point[1] for point in selected_centers
    )


def test_selection_excludes_units_in_outer_five_microns() -> None:
    centers = [[-14.0, 0.0], [-12.5, 0.0], [0.0, 0.0], [12.5, 0.0], [14.0, 0.0]]

    result = select_routed_units(centers, [-17.5, -20.0, 17.5, 20.0], 3)

    assert result["eligible_unit_indices"] == [2, 3, 4]
    assert result["selected_unit_indices"] == [2, 3, 4]


def test_selection_does_not_relax_inset_when_count_is_too_large() -> None:
    with pytest.raises(AnalysisError) as caught:
        select_routed_units(
            [[-14.0, 0.0], [0.0, 0.0], [14.0, 0.0]],
            [-17.5, -20.0, 17.5, 20.0],
            2,
        )

    assert caught.value.code == "ROUTED_DEVICE_COUNT_EXCEEDS_ELIGIBLE"
    assert caught.value.details["eligible_unit_indices"] == [2]


@pytest.mark.parametrize("count", [0, -1, 1.5, True])
def test_selection_rejects_invalid_count(count) -> None:
    with pytest.raises(AnalysisError) as caught:
        select_routed_units([[0.0, 0.0]], [-10.0, -10.0, 10.0, 10.0], count)

    assert caught.value.code == "INVALID_ROUTED_DEVICE_COUNT"


def test_selection_rejects_inset_that_consumes_window() -> None:
    with pytest.raises(AnalysisError) as caught:
        select_routed_units([[0.0, 0.0]], [-5.0, -5.0, 5.0, 5.0], 1, 5.0)

    assert caught.value.code == "EDGE_INSET_CONSUMES_DEVICE_WINDOW"


def test_plan_transistor_array_centers_grid_and_reuses_selector() -> None:
    result = plan_transistor_array(7, 7, 5.0, 5.0, 10)

    assert result["array"]["unit_centers_um"][0] == [-15.0, -15.0]
    assert result["array"]["unit_centers_um"][-1] == [15.0, 15.0]
    assert len(result["selected_unit_indices"]) == 10
    assert result == plan_transistor_array(7, 7, 5.0, 5.0, 10)


def test_plan_transistor_array_rejects_centers_outside_window() -> None:
    with pytest.raises(AnalysisError) as caught:
        plan_transistor_array(3, 3, 20.0, 20.0, 1)

    assert caught.value.code == "ARRAY_CENTERS_OUTSIDE_DEVICE_WINDOW"


def test_selection_rejects_nonfinite_device_window() -> None:
    with pytest.raises(AnalysisError) as caught:
        select_routed_units([[0.0, 0.0]], [float("nan"), -10.0, 10.0, 10.0], 1)

    assert caught.value.code == "INVALID_BOX"


@pytest.mark.parametrize(
    ("centers", "edge_inset"),
    [
        ([[True, 0.0]], 0.0),
        ([[0.0, 0.0]], True),
        (["12"], 0.0),
    ],
)
def test_selection_rejects_boolean_or_text_geometry(centers, edge_inset) -> None:
    with pytest.raises(AnalysisError):
        select_routed_units(centers, [-10.0, -10.0, 10.0, 10.0], 1, edge_inset)


def test_array_planner_rejects_boolean_pitch() -> None:
    with pytest.raises(AnalysisError) as caught:
        plan_transistor_array(3, 3, True, 2.0, 1)

    assert caught.value.code == "INVALID_ARRAY_PITCH"
