"""Process-neutral filled-array context planning for measured transistors."""

from __future__ import annotations

import math
from typing import Any, Sequence

from .errors import AnalysisError
from .geometry import Box
from .organization_presets import load_organization_preset
from .selection import select_routed_units


def _positive(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise AnalysisError(
            code="INVALID_TRANSISTOR_CONTEXT_GEOMETRY",
            message=f"{field} must be a finite positive micron value.",
            details={"field": field, "value": value},
            next_action="Provide legal device geometry from the process adapter.",
        )
    return float(value)


def _nonnegative(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise AnalysisError(
            code="INVALID_TRANSISTOR_CONTEXT_GEOMETRY",
            message=f"{field} must be a finite non-negative micron value.",
            details={"field": field, "value": value},
            next_action="Provide a non-negative context margin or selection inset.",
        )
    return float(value)


def _positive_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AnalysisError(
            code="INVALID_MEASURED_DEVICE_COUNT",
            message="measured_device_count must be a positive integer.",
            details={"measured_device_count": value},
            next_action="Use 1 for the default central DUT, or request another positive count.",
        )
    return value


def plan_single_transistor_context(
    *,
    dut_window_um: Sequence[float],
    device_footprint_um: Sequence[float],
    pitch_x_um: float,
    measured_device_type: str,
    pitch_y_um: float | None = None,
    edge_margin_um: float = 0.0,
    fill_style: str | None = None,
    measured_device_count: int | None = None,
    measurement_edge_inset_um: float | None = None,
    standard_cell_height_um: float | None = None,
) -> dict[str, Any]:
    """Fill the DUT and select one or more balanced, inset measurement devices."""

    window = Box.from_sequence(dut_window_um)
    if len(device_footprint_um) != 2:
        raise AnalysisError(
            code="INVALID_TRANSISTOR_CONTEXT_GEOMETRY",
            message="device_footprint_um must contain width and height.",
            details={"device_footprint_um": device_footprint_um},
            next_action="Provide [width_um, height_um] from the transistor adapter.",
        )
    footprint_x = _positive(device_footprint_um[0], field="device_footprint_um[0]")
    footprint_y = _positive(device_footprint_um[1], field="device_footprint_um[1]")
    pitch_x = _positive(pitch_x_um, field="pitch_x_um")
    edge_margin = _nonnegative(edge_margin_um, field="edge_margin_um")

    device_type = measured_device_type.strip().casefold() if isinstance(measured_device_type, str) else ""
    if device_type not in {"nmos", "pmos"}:
        raise AnalysisError(
            code="INVALID_TRANSISTOR_CONTEXT_DEVICE_TYPE",
            message="Measured device type must be nmos or pmos.",
            details={"measured_device_type": measured_device_type},
            next_action="Provide the measured transistor polarity.",
        )

    preset = load_organization_preset()["transistor_context_defaults"]
    style = preset["default_fill_style"] if fill_style is None else fill_style
    if style not in preset["allowed_fill_styles"]:
        raise AnalysisError(
            code="INVALID_TRANSISTOR_CONTEXT_FILL_STYLE",
            message="Transistor context fill style is unsupported.",
            details={"fill_style": style, "allowed": preset["allowed_fill_styles"]},
            next_action="Use same_as_measured or standard_cell_like.",
        )

    if style == "standard_cell_like":
        if standard_cell_height_um is None:
            raise AnalysisError(
                code="STANDARD_CELL_HEIGHT_REQUIRED",
                message="standard_cell_height_um is required for standard_cell_like context fill.",
                details={"fill_style": style},
                next_action="Provide the legal standard-cell row height in microns.",
            )
        pitch_y = _positive(standard_cell_height_um, field="standard_cell_height_um")
        if pitch_y_um is not None and not math.isclose(
            _positive(pitch_y_um, field="pitch_y_um"), pitch_y, rel_tol=0.0, abs_tol=1e-12
        ):
            raise AnalysisError(
                code="STANDARD_CELL_HEIGHT_PITCH_CONFLICT",
                message="pitch_y_um and standard_cell_height_um must match for standard_cell_like fill.",
                details={"pitch_y_um": pitch_y_um, "standard_cell_height_um": pitch_y},
                next_action="Use the standard-cell height as the row pitch.",
            )
        if footprint_y > pitch_y:
            raise AnalysisError(
                code="STANDARD_CELL_FOOTPRINT_EXCEEDS_HEIGHT",
                message="The device footprint height exceeds the standard-cell height.",
                details={"device_footprint_height_um": footprint_y, "standard_cell_height_um": pitch_y},
                next_action="Correct the footprint or provide a compatible cell height.",
            )
    else:
        if pitch_y_um is None:
            raise AnalysisError(
                code="TRANSISTOR_CONTEXT_PITCH_REQUIRED",
                message="pitch_y_um is required for same_as_measured context fill.",
                details={"fill_style": style},
                next_action="Provide the legal vertical placement pitch.",
            )
        pitch_y = _positive(pitch_y_um, field="pitch_y_um")

    measured_count = _positive_count(
        preset["default_measured_device_count"]
        if measured_device_count is None
        else measured_device_count
    )
    selection_inset = _nonnegative(
        preset["measurement_edge_inset_um"]
        if measurement_edge_inset_um is None
        else measurement_edge_inset_um,
        field="measurement_edge_inset_um",
    )

    usable_width = window.width - 2.0 * edge_margin
    usable_height = window.height - 2.0 * edge_margin
    if usable_width < footprint_x or usable_height < footprint_y:
        raise AnalysisError(
            code="TRANSISTOR_CONTEXT_DOES_NOT_FIT",
            message="One transistor footprint does not fit inside the margined DUT window.",
            details={"dut_window_um": window.to_list(), "device_footprint_um": [footprint_x, footprint_y]},
            next_action="Reduce the margin/footprint or enlarge the DUT window.",
        )

    columns = int(math.floor((usable_width - footprint_x) / pitch_x)) + 1
    rows = int(math.floor((usable_height - footprint_y) / pitch_y)) + 1
    site_count = rows * columns
    if site_count > 10_001:
        raise AnalysisError(
            code="TRANSISTOR_CONTEXT_TOO_LARGE",
            message="Context lattice exceeds the bounded planner inventory.",
            details={"rows": rows, "columns": columns, "site_count": site_count},
            next_action="Use a hierarchical/array representation or a coarser legal pitch.",
        )

    center_column = columns // 2
    sequence = preset["standard_cell_sequence"]
    phase = 0
    if style == "standard_cell_like":
        phase = next(index for index, value in enumerate(sequence) if value == device_type)

    center_x = (window.x1 + window.x2) / 2.0
    center_y = (window.y1 + window.y2) / 2.0
    x0 = center_x - (columns - 1) * pitch_x / 2.0
    y0 = center_y - (rows - 1) * pitch_y / 2.0
    sites: list[dict[str, Any]] = []
    candidate_site_indices: list[int] = []
    candidate_centers: list[list[float]] = []
    for row in range(rows):
        for column in range(columns):
            site_type = (
                device_type
                if style == "same_as_measured"
                else sequence[(column - center_column + phase) % len(sequence)]
            )
            left_type = None if column == 0 else (
                device_type
                if style == "same_as_measured"
                else sequence[(column - 1 - center_column + phase) % len(sequence)]
            )
            origin = [x0 + column * pitch_x, y0 + row * pitch_y]
            sites.append(
                {
                    "row": row,
                    "column": column,
                    "origin_um": origin,
                    "device_type": site_type,
                    "is_measured_dut": False,
                    "terminal_routing": "none",
                    "share_diffusion_with_left": left_type == site_type,
                }
            )
            if site_type == device_type:
                candidate_site_indices.append(len(sites) - 1)
                candidate_centers.append(origin)

    array_box = Box(
        x0 - footprint_x / 2.0,
        y0 - footprint_y / 2.0,
        x0 + (columns - 1) * pitch_x + footprint_x / 2.0,
        y0 + (rows - 1) * pitch_y + footprint_y / 2.0,
    )
    selection = select_routed_units(
        candidate_centers,
        array_box.to_list(),
        measured_count,
        selection_inset,
    )
    selected_candidate_indices = [index - 1 for index in selection["selected_unit_indices"]]
    selected_site_indices = [candidate_site_indices[index] for index in selected_candidate_indices]
    for site_index in selected_site_indices:
        sites[site_index]["is_measured_dut"] = True
        sites[site_index]["terminal_routing"] = "measured_only"

    measured_sites = [
        {
            "row": sites[index]["row"],
            "column": sites[index]["column"],
            "origin_um": sites[index]["origin_um"],
            "device_type": sites[index]["device_type"],
        }
        for index in selected_site_indices
    ]
    result: dict[str, Any] = {
        "ok": True,
        "policy_source": "organization_preset.transistor_context_defaults",
        "dut_window_um": window.to_list(),
        "array_bbox_um": array_box.to_list(),
        "rows": rows,
        "columns": columns,
        "site_count": site_count,
        "fill_style": style,
        "effective_pitch_y_um": pitch_y,
        "standard_cell_height_um": pitch_y if style == "standard_cell_like" else None,
        "measured_sites": measured_sites,
        "measured_site_count": len(measured_sites),
        "measurement_edge_inset_um": selection_inset,
        "measurement_selection": {
            "method": preset["measured_device_selection"],
            "eligible_candidate_count": selection["eligible_unit_count"],
            "selection_region_um": selection["selection_region_um"],
            "tie_break": selection["tie_break"],
        },
        "surrounding_device_routing": "none",
        "diffusion_sharing": "compatible_neighbors",
        "standard_cell_sequence": sequence if style == "standard_cell_like" else None,
        "standard_cell_sequence_phase": phase if style == "standard_cell_like" else None,
        "sites": sites,
        "geometry_adapter_must_verify": [
            "device footprint containment",
            "legal gate/active/contact spacing",
            "well and implant boundaries",
            "diffusion sharing compatibility",
            "terminal routing for every selected measured site",
        ],
        "production_ready": False,
    }
    if len(measured_sites) == 1:
        result["measured_site"] = measured_sites[0]
    return result
