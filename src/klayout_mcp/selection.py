"""Deterministic routed-unit selection for DUT transistor arrays."""

from __future__ import annotations

import math
from typing import Sequence

from .errors import AnalysisError
from .geometry import Box, Point


def _unit_centers(values: Sequence[Sequence[float]]) -> list[Point]:
    centers: list[Point] = []
    for index, value in enumerate(values, start=1):
        if (
            isinstance(value, (str, bytes, bytearray))
            or not isinstance(value, Sequence)
            or len(value) != 2
        ):
            raise AnalysisError(
                code="INVALID_UNIT_CENTER",
                message="Each transistor center must contain two coordinates.",
                details={"unit_index": index, "center": str(value)},
                next_action="Provide each center as [x_um, y_um].",
            )
        if any(isinstance(coordinate, bool) for coordinate in value):
            raise AnalysisError(
                code="INVALID_UNIT_CENTER",
                message="Transistor center coordinates must be finite numbers.",
                details={"unit_index": index, "center": list(value)},
                next_action="Replace boolean coordinates with numeric micron values.",
            )
        try:
            center = Point(float(value[0]), float(value[1]))
        except (TypeError, ValueError) as exc:
            raise AnalysisError(
                code="INVALID_UNIT_CENTER",
                message="Transistor center coordinates must be finite numbers.",
                details={"unit_index": index, "center": list(value)},
                next_action="Provide numeric coordinates in microns.",
            ) from exc
        if not math.isfinite(center.x) or not math.isfinite(center.y):
            raise AnalysisError(
                code="INVALID_UNIT_CENTER",
                message="Transistor center coordinates must be finite numbers.",
                details={"unit_index": index, "center": list(value)},
                next_action="Remove NaN or infinite coordinates.",
            )
        centers.append(center)
    if not centers:
        raise AnalysisError(
            code="EMPTY_TRANSISTOR_ARRAY",
            message="At least one transistor center is required.",
            next_action="Provide the row-major transistor center list.",
        )
    return centers


def _balanced_target_points(region: Box, count: int) -> list[Point]:
    if count == 1:
        return [region.center]

    row_count = max(
        1,
        min(count, int(round(math.sqrt(count * region.height / region.width)))),
    )
    base_count, extra_rows = divmod(count, row_count)
    row_order = sorted(
        range(row_count),
        key=lambda row: (abs((row + 0.5) - row_count / 2.0), row),
    )
    counts = [base_count] * row_count
    for row in row_order[:extra_rows]:
        counts[row] += 1

    targets: list[Point] = []
    for row, column_count in enumerate(counts):
        y = region.y1 + (row + 0.5) * region.height / row_count
        for column in range(column_count):
            x = region.x1 + (column + 0.5) * region.width / column_count
            targets.append(Point(x, y))
    return targets


def select_routed_units(
    unit_centers_um: Sequence[Sequence[float]],
    device_window_um: Sequence[float],
    routed_device_count: int,
    edge_inset_um: float = 5.0,
) -> dict[str, object]:
    """Select evenly distributed units by center without relaxing the inset."""

    if isinstance(routed_device_count, bool) or not isinstance(routed_device_count, int):
        raise AnalysisError(
            code="INVALID_ROUTED_DEVICE_COUNT",
            message="routed_device_count must be an integer.",
            details={"routed_device_count": routed_device_count},
            next_action="Provide a positive integer selection count.",
        )
    if routed_device_count <= 0:
        raise AnalysisError(
            code="INVALID_ROUTED_DEVICE_COUNT",
            message="routed_device_count must be positive.",
            details={"routed_device_count": routed_device_count},
            next_action="Provide a selection count of at least one.",
        )
    if isinstance(edge_inset_um, bool):
        raise AnalysisError(
            code="INVALID_EDGE_INSET",
            message="edge_inset_um must be a finite nonnegative number.",
            details={"edge_inset_um": edge_inset_um},
            next_action="Replace the boolean with a numeric micron value.",
        )
    try:
        inset = float(edge_inset_um)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(
            code="INVALID_EDGE_INSET",
            message="edge_inset_um must be a finite nonnegative number.",
            details={"edge_inset_um": edge_inset_um},
            next_action="Provide a finite nonnegative edge inset in microns.",
        ) from exc
    if not math.isfinite(inset) or inset < 0:
        raise AnalysisError(
            code="INVALID_EDGE_INSET",
            message="edge_inset_um must be a finite nonnegative number.",
            details={"edge_inset_um": edge_inset_um},
            next_action="Provide a finite nonnegative edge inset in microns.",
        )

    window = Box.from_sequence(device_window_um)
    if 2.0 * inset >= window.width or 2.0 * inset >= window.height:
        raise AnalysisError(
            code="EDGE_INSET_CONSUMES_DEVICE_WINDOW",
            message="The edge inset leaves no positive transistor selection region.",
            details={
                "device_window_um": window.to_list(),
                "edge_inset_um": inset,
            },
            next_action="Reduce the inset or enlarge the device window.",
        )
    selection_region = Box(
        window.x1 + inset,
        window.y1 + inset,
        window.x2 - inset,
        window.y2 - inset,
    )
    centers = _unit_centers(unit_centers_um)
    eligible = [
        (index, center)
        for index, center in enumerate(centers, start=1)
        if selection_region.x1 <= center.x <= selection_region.x2
        and selection_region.y1 <= center.y <= selection_region.y2
    ]
    if routed_device_count > len(eligible):
        raise AnalysisError(
            code="ROUTED_DEVICE_COUNT_EXCEEDS_ELIGIBLE",
            message="Requested routed transistor count exceeds the inset-eligible units.",
            details={
                "routed_device_count": routed_device_count,
                "eligible_unit_count": len(eligible),
                "edge_inset_um": inset,
                "eligible_unit_indices": [index for index, _ in eligible],
            },
            next_action="Reduce routed_device_count or change the array topology.",
        )

    targets = _balanced_target_points(selection_region, routed_device_count)
    available = dict(eligible)
    assignments: list[dict[str, object]] = []
    for target_number, target in enumerate(targets, start=1):
        selected_index = min(
            available,
            key=lambda index: (
                (available[index].x - target.x) ** 2
                + (available[index].y - target.y) ** 2,
                index,
            ),
        )
        selected_center = available.pop(selected_index)
        assignments.append(
            {
                "target_number": target_number,
                "target_um": target.to_list(),
                "unit_index": selected_index,
                "unit_center_um": selected_center.to_list(),
            }
        )

    selected_indices = sorted(item["unit_index"] for item in assignments)
    return {
        "ok": True,
        "units": "um",
        "device_window_um": window.to_list(),
        "edge_inset_um": inset,
        "selection_region_um": selection_region.to_list(),
        "unit_count": len(centers),
        "eligible_unit_count": len(eligible),
        "eligible_unit_indices": [index for index, _ in eligible],
        "routed_device_count": routed_device_count,
        "selected_unit_indices": selected_indices,
        "assignments": assignments,
        "selection_method": "balanced_target_nearest_unselected",
        "tie_break": "lowest_1_based_input_index",
    }


def plan_transistor_array(
    array_rows: int,
    array_cols: int,
    pitch_x_um: float,
    pitch_y_um: float,
    routed_device_count: int,
    device_window_um: Sequence[float] = (-17.5, -20.0, 17.5, 20.0),
    edge_inset_um: float = 5.0,
) -> dict[str, object]:
    """Create a centered row-major array plan and its reusable routed pattern."""

    dimensions = {"array_rows": array_rows, "array_cols": array_cols}
    invalid_dimensions = {
        name: value
        for name, value in dimensions.items()
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0
    }
    if invalid_dimensions:
        raise AnalysisError(
            code="INVALID_ARRAY_DIMENSIONS",
            message="Array row and column counts must be positive integers.",
            details=invalid_dimensions,
            next_action="Provide array_rows and array_cols of at least one.",
        )
    if isinstance(pitch_x_um, bool) or isinstance(pitch_y_um, bool):
        raise AnalysisError(
            code="INVALID_ARRAY_PITCH",
            message="Array pitches must be finite positive numbers.",
            details={"pitch_x_um": pitch_x_um, "pitch_y_um": pitch_y_um},
            next_action="Replace boolean pitches with positive numeric micron values.",
        )
    try:
        pitch_x = float(pitch_x_um)
        pitch_y = float(pitch_y_um)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(
            code="INVALID_ARRAY_PITCH",
            message="Array pitches must be finite positive numbers.",
            details={"pitch_x_um": pitch_x_um, "pitch_y_um": pitch_y_um},
            next_action="Provide finite positive array pitches in microns.",
        ) from exc
    if (
        not math.isfinite(pitch_x)
        or not math.isfinite(pitch_y)
        or pitch_x <= 0
        or pitch_y <= 0
    ):
        raise AnalysisError(
            code="INVALID_ARRAY_PITCH",
            message="Array pitches must be finite positive numbers.",
            details={"pitch_x_um": pitch_x_um, "pitch_y_um": pitch_y_um},
            next_action="Provide finite positive array pitches in microns.",
        )

    window = Box.from_sequence(device_window_um)
    center = window.center
    x0 = center.x - (array_cols - 1) * pitch_x / 2.0
    y0 = center.y - (array_rows - 1) * pitch_y / 2.0
    unit_centers = [
        [x0 + column * pitch_x, y0 + row * pitch_y]
        for row in range(array_rows)
        for column in range(array_cols)
    ]
    outside = [
        index
        for index, (x, y) in enumerate(unit_centers, start=1)
        if not (window.x1 <= x <= window.x2 and window.y1 <= y <= window.y2)
    ]
    if outside:
        raise AnalysisError(
            code="ARRAY_CENTERS_OUTSIDE_DEVICE_WINDOW",
            message="The centered array places transistor centers outside the device window.",
            details={
                "outside_unit_indices": outside,
                "device_window_um": window.to_list(),
                "array_rows": array_rows,
                "array_cols": array_cols,
                "pitch_x_um": pitch_x,
                "pitch_y_um": pitch_y,
            },
            next_action="Reduce array dimensions or pitch, or enlarge the device window.",
        )

    result = select_routed_units(
        unit_centers,
        window.to_list(),
        routed_device_count,
        edge_inset_um,
    )
    result["array"] = {
        "rows": array_rows,
        "columns": array_cols,
        "pitch_x_um": pitch_x,
        "pitch_y_um": pitch_y,
        "origin": "device_window_center",
        "index_order": "y_ascending_then_x_ascending",
        "unit_centers_um": unit_centers,
    }
    return result
