"""Deterministic SLN001 Kelvin M1 routing plans and integer-grid geometry."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .design_contract import TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
from .errors import AnalysisError
from .dbu_grid import DbuGridError, micron_to_dbu


DEFAULT_SPLITS: tuple[tuple[int, int], ...] = (
    (22, 300),
    (100, 1000),
    (300, 1000),
    (300, 300),
    (100, 300),
    (22, 1000),
)
DEFAULT_SITE_ORIGINS_UM: tuple[tuple[float, float], ...] = (
    (160.0, 27.0),
    (480.0, 27.0),
    (800.0, 27.0),
    (1120.0, 27.0),
    (1440.0, 27.0),
    (1760.0, 27.0),
)


@dataclass(frozen=True, slots=True)
class KelvinSplit:
    key: str
    width_nm: int
    length_nm: int
    origin_um: tuple[float, float]

    @property
    def cell_name(self) -> str:
        return (
            f"KELVIN_{self.key}_W{self.width_nm:03d}NM_"
            f"L{self.length_nm:04d}NM"
        )

    @property
    def local_routing_cell_name(self) -> str:
        return f"KELVIN_{self.key}_LOCAL_ROUTING_MESH"


@dataclass(frozen=True, slots=True)
class KelvinRoutingSpec:
    splits: tuple[KelvinSplit, ...]
    m1_layer: int = 15
    m1_datatype: int = 0
    measured_width_min_nm: int = 22
    measured_width_max_nm: int = 300
    terminal_square_nm: int = 300
    routing_width_nm: int = 300
    mesh_pitch_nm: int = 1000
    expansion_rail_counts: tuple[int, ...] = (1, 2, 4, 6)
    force_pad_rail_center_abs_um: float = 20.15
    sense_common_inner_edge_abs_um: float = 1.0
    sense_common_outer_edge_abs_um: float = 139.0
    sense_last_cross_tie_y_um: float = 19.85
    sense_corner_rail_y_um: float = 20.85
    sense_common_top_um: float = 26.0


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError(
            code="INVALID_KELVIN_ROUTING_SPEC",
            message=f"{field} must be a finite number.",
            details={field: value},
            next_action="Provide a finite numeric routing parameter.",
        )
    result = float(value)
    if not math.isfinite(result):
        raise AnalysisError(
            code="INVALID_KELVIN_ROUTING_SPEC",
            message=f"{field} must be finite.",
            details={field: value},
            next_action="Provide a finite numeric routing parameter.",
        )
    return result


def _normalize_split_records(
    splits: Sequence[Mapping[str, Any]] | None,
    site_origins_um: Sequence[Sequence[float]] | None,
) -> tuple[KelvinSplit, ...]:
    raw_splits: Sequence[Mapping[str, Any]]
    if splits is None:
        raw_splits = [
            {"width_nm": width_nm, "length_nm": length_nm}
            for width_nm, length_nm in DEFAULT_SPLITS
        ]
    else:
        raw_splits = splits
    raw_origins = site_origins_um or DEFAULT_SITE_ORIGINS_UM
    if len(raw_splits) != 6 or len(raw_origins) != 6:
        raise AnalysisError(
            code="KELVIN_SIX_SPLITS_REQUIRED",
            message="The SLN001 Kelvin routing profile requires exactly six splits and sites.",
            details={
                "split_count": len(raw_splits),
                "site_origin_count": len(raw_origins),
            },
            next_action="Provide six width/length records and six matching site origins.",
        )

    normalized: list[KelvinSplit] = []
    pairs: list[tuple[int, int]] = []
    for index, (record, origin) in enumerate(zip(raw_splits, raw_origins), start=1):
        try:
            width_nm = int(record["width_nm"])
            length_nm = int(record["length_nm"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise AnalysisError(
                code="INVALID_KELVIN_SPLIT",
                message="Each Kelvin split needs integer width_nm and length_nm.",
                details={"split_index": index, "split": dict(record)},
                next_action="Provide width_nm and length_nm for every split.",
            ) from exc
        if width_nm < 22 or width_nm > 300:
            raise AnalysisError(
                code="KELVIN_MEASURED_WIDTH_OUT_OF_RANGE",
                message="Measured M1 width must remain within the confirmed 22–300 nm range.",
                details={"split_index": index, "width_nm": width_nm},
                next_action="Use a measured width from 22 nm through 300 nm.",
            )
        if length_nm <= 0:
            raise AnalysisError(
                code="INVALID_KELVIN_SPLIT",
                message="Measured M1 length must be positive.",
                details={"split_index": index, "length_nm": length_nm},
                next_action="Provide a positive longitudinal measured length.",
            )
        if len(origin) != 2:
            raise AnalysisError(
                code="INVALID_KELVIN_SITE_ORIGIN",
                message="Each Kelvin site origin must contain x and y coordinates.",
                details={"split_index": index, "origin_um": list(origin)},
                next_action="Provide every origin as [x_um, y_um].",
            )
        origin_xy = (
            _finite_number(origin[0], field="origin_x_um"),
            _finite_number(origin[1], field="origin_y_um"),
        )
        pairs.append((width_nm, length_nm))
        normalized.append(
            KelvinSplit(
                key=f"K{index}",
                width_nm=width_nm,
                length_nm=length_nm,
                origin_um=origin_xy,
            )
        )

    unique_widths = sorted({width_nm for width_nm, _ in pairs})
    unique_lengths = sorted({length_nm for _, length_nm in pairs})
    expected_pairs = {
        (width_nm, length_nm)
        for width_nm in unique_widths
        for length_nm in unique_lengths
    }
    if (
        len(unique_widths) != 3
        or len(unique_lengths) != 2
        or set(pairs) != expected_pairs
        or len(set(pairs)) != 6
    ):
        raise AnalysisError(
            code="KELVIN_CARTESIAN_SPLIT_SET_REQUIRED",
            message=(
                "The confirmed six-split test requires a complete Cartesian product of "
                "three widths and two lengths."
            ),
            details={
                "received_pairs_nm": [list(pair) for pair in pairs],
                "unique_widths_nm": unique_widths,
                "unique_lengths_nm": unique_lengths,
            },
            next_action=(
                "Provide each combination of exactly three distinct widths and two "
                "distinct lengths once."
            ),
        )
    return tuple(normalized)


def build_kelvin_routing_spec(
    *,
    dimension_semantics: str | None,
    confirm_routing_contract: bool,
    splits: Sequence[Mapping[str, Any]] | None = None,
    site_origins_um: Sequence[Sequence[float]] | None = None,
) -> KelvinRoutingSpec:
    """Validate the user-confirmed geometry meaning and return the SLN001 profile."""

    if dimension_semantics != TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH:
        raise AnalysisError(
            code="KELVIN_DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED",
            message=(
                "Kelvin routing requires explicit confirmation that width is transverse "
                "and length is longitudinal to current flow."
            ),
            details={
                "required_dimension_semantics": TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
                "received_dimension_semantics": dimension_semantics,
            },
            next_action=(
                "Confirm the W/L axis meaning with the user and pass the required token exactly."
            ),
        )
    if not confirm_routing_contract:
        raise AnalysisError(
            code="KELVIN_ROUTING_CONTRACT_CONFIRMATION_REQUIRED",
            message="The Kelvin force/sense role and orthogonal mesh contract is not confirmed.",
            details={
                "measured_line_orientation": "horizontal",
                "pad_roles_left_to_right": ["SENSE+", "FORCE+", "FORCE-", "SENSE-"],
                "expansion_rail_counts": [1, 2, 4, 6],
            },
            next_action=(
                "Confirm the horizontal measured line, direct left/right force routes, "
                "straight-up sense routes, and one-sided 1→2→4→6 mesh."
            ),
        )
    return KelvinRoutingSpec(
        splits=_normalize_split_records(splits, site_origins_um)
    )


def kelvin_routing_plan_result(spec: KelvinRoutingSpec) -> dict[str, Any]:
    """Return a structured, model-readable routing plan without drawing a layout."""

    return {
        "ok": True,
        "profile": "sln001_kelvin_m1_six_split_v1",
        "production_ready": False,
        "optimization_status": "minimize_with_available_constraints_not_rc_proven",
        "m1": {"layer": spec.m1_layer, "datatype": spec.m1_datatype},
        "measured_width_range_nm": [
            spec.measured_width_min_nm,
            spec.measured_width_max_nm,
        ],
        "measured_line_orientation": "horizontal",
        "routing_geometry": "axis_aligned_boxes_only",
        "pad_roles_left_to_right": ["SENSE+", "FORCE+", "FORCE-", "SENSE-"],
        "mesh": {
            "routing_width_nm": spec.routing_width_nm,
            "pitch_nm": spec.mesh_pitch_nm,
            "clear_space_nm": spec.mesh_pitch_nm - spec.routing_width_nm,
            "expansion_rail_counts": list(spec.expansion_rail_counts),
            "expansion_style": "one_sided_from_persistent_baseline",
            "interface_policy": "preserve_mesh_modify_aligned_end_geometry_only",
        },
        "terminal_square_nm": spec.terminal_square_nm,
        "force": {
            "direction": "direct_horizontal_left_and_right",
            "receiving_pad_frame_rail_center_abs_um": spec.force_pad_rail_center_abs_um,
        },
        "sense": {
            "direction_from_terminal": "straight_vertical_without_horizontal_jog",
            "last_cross_tie_y_um": spec.sense_last_cross_tie_y_um,
            "corner_rail_y_um": spec.sense_corner_rail_y_um,
            "corner": "full_width_pitch_aligned_90_degree",
        },
        "splits": [
            {
                "key": split.key,
                "width_nm": split.width_nm,
                "length_nm": split.length_nm,
                "origin_um": list(split.origin_um),
                "cell_name": split.cell_name,
                "local_routing_cell_name": split.local_routing_cell_name,
            }
            for split in spec.splits
        ],
        "verification_required": [
            "fresh_reload",
            "recursive_m1_geometry_xor",
            "m1_connected_components",
            "orthogonal_box_inventory",
            "pad_group_isolation",
        ],
    }


def _exact_grid(value_um: float, dbu_um: float, *, field: str) -> int:
    try:
        return micron_to_dbu(value_um, dbu_um)
    except DbuGridError as exc:
        raise AnalysisError(
            code="KELVIN_PARAMETER_OFF_DBU_GRID",
            message=f"{field} is not exactly representable on the layout DBU grid.",
            details={field: value_um, "dbu_um": dbu_um},
            next_action="Use a padset DBU that exactly represents all Kelvin dimensions.",
        ) from exc


def _mirror_x(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (-box[2], box[1], -box[0], box[3])


def _sense_boxes(
    *, half_length: int, width: int, pitch: int, dbu_um: float, spec: KelvinRoutingSpec
) -> list[tuple[int, int, int, int]]:
    half = width // 2
    overlap = 1
    baseline = half_length + half
    start_y = half - overlap
    stage_height = 2 * pitch
    boxes: list[tuple[int, int, int, int]] = []

    def horizontal_tie(rail_count: int, center_y: int) -> None:
        boxes.append(
            (
                baseline - half,
                center_y - half,
                baseline + (rail_count - 1) * pitch + half,
                center_y + half,
            )
        )

    y = start_y
    rail_count = 1
    for next_count in (2, 4, 6):
        next_y = y + stage_height
        for rail_index in range(rail_count):
            center_x = baseline + rail_index * pitch
            boxes.append((center_x - half, y, center_x + half, next_y))
        horizontal_tie(rail_count, y + pitch)
        horizontal_tie(next_count, next_y)
        y = next_y
        rail_count = next_count

    final_cross_y = _exact_grid(
        spec.sense_last_cross_tie_y_um, dbu_um, field="sense_last_cross_tie_y_um"
    )
    for rail_index in range(rail_count):
        center_x = baseline + rail_index * pitch
        boxes.append((center_x - half, y, center_x + half, final_cross_y))
    tie_y = y + pitch
    while tie_y < final_cross_y - pitch:
        horizontal_tie(rail_count, tie_y)
        tie_y += pitch
    horizontal_tie(rail_count, final_cross_y)

    corner_top = _exact_grid(
        spec.sense_corner_rail_y_um + spec.routing_width_nm / 2000.0,
        dbu_um,
        field="sense_corner_rail_top_um",
    )
    for rail_index in range(rail_count):
        center_x = baseline + rail_index * pitch
        boxes.append((center_x - half, final_cross_y, center_x + half, corner_top))
    common_inner = _exact_grid(
        spec.sense_common_inner_edge_abs_um, dbu_um, field="sense_common_inner_edge_abs_um"
    )
    corner_center = _exact_grid(
        spec.sense_corner_rail_y_um, dbu_um, field="sense_corner_rail_y_um"
    )
    if baseline - half < common_inner:
        boxes.append(
            (
                baseline - half,
                corner_center - half,
                common_inner + half,
                corner_center + half,
            )
        )
    return boxes + [_mirror_x(box) for box in boxes]


def _force_boxes(
    *, half_length: int, width: int, pitch: int, dbu_um: float, spec: KelvinRoutingSpec
) -> list[tuple[int, int, int, int]]:
    half = width // 2
    terminal_outer = half_length + width
    grid_start = terminal_outer - 1
    boxes: list[tuple[int, int, int, int]] = []
    counts = (1, 2, 4, 6)
    stage_start = grid_start
    for count in counts:
        stage_end = stage_start + 2 * pitch
        for rail_index in range(count):
            center_y = -rail_index * pitch
            boxes.append(
                (stage_start, center_y - half, stage_end, center_y + half)
            )
        stage_start = stage_end

    long_half = _exact_grid(0.5, dbu_um, field="long_length_half_um")
    staged_ties = (
        (1, 1),
        (2, 2),
        (3, 2),
        (4, 4),
        (5, 4),
        (6, 6),
    )
    for grid_offset, count in staged_ties:
        # For the 1 um specimen, this one interior tie is intentionally omitted;
        # that is part of the accepted v15 geometry and leaves both rail ends tied.
        if half_length == long_half and grid_offset == 3:
            continue
        center_x = grid_start + grid_offset * pitch
        depth = -(count - 1) * pitch - half
        boxes.append((center_x - half, depth, center_x + half, half))

    landing_center = _exact_grid(
        spec.force_pad_rail_center_abs_um,
        dbu_um,
        field="force_pad_rail_center_abs_um",
    )
    last_stage_start = grid_start + 6 * pitch
    # Replace the provisional last-stage endpoints with the aligned Pad rail center.
    first_last_stage = sum(counts[:-1])
    for index in range(first_last_stage, first_last_stage + counts[-1]):
        x1, y1, _, y2 = boxes[index]
        boxes[index] = (x1, y1, landing_center, y2)
    center_x = last_stage_start + pitch
    while center_x <= landing_center - pitch:
        boxes.append(
            (
                center_x - half,
                -(counts[-1] - 1) * pitch - half,
                center_x + half,
                half,
            )
        )
        center_x += pitch
    boxes.append(
        (
            landing_center - half,
            -(counts[-1] - 1) * pitch - half,
            landing_center + half,
            half,
        )
    )
    return boxes + [_mirror_x(box) for box in boxes]


def _common_sense_boxes(
    *, width: int, pitch: int, dbu_um: float, spec: KelvinRoutingSpec
) -> list[tuple[int, int, int, int]]:
    half = width // 2
    inner = _exact_grid(
        spec.sense_common_inner_edge_abs_um, dbu_um, field="sense_common_inner_edge_abs_um"
    )
    outer = _exact_grid(
        spec.sense_common_outer_edge_abs_um, dbu_um, field="sense_common_outer_edge_abs_um"
    )
    corner_center = _exact_grid(
        spec.sense_corner_rail_y_um, dbu_um, field="sense_corner_rail_y_um"
    )
    top = _exact_grid(spec.sense_common_top_um, dbu_um, field="sense_common_top_um")
    lower_extension = corner_center - pitch - half
    boxes: list[tuple[int, int, int, int]] = []
    for rail_index in range(6):
        center_y = corner_center + rail_index * pitch
        boxes.append((inner, center_y - half, outer, center_y + half))
    center_x = inner
    while center_x <= outer:
        bottom = corner_center - half
        if center_x >= _exact_grid(101.0, dbu_um, field="sense_pad_lower_extension_x_um"):
            bottom = lower_extension
        boxes.append((center_x - half, bottom, center_x + half, top))
        center_x += pitch
    return boxes + [_mirror_x(box) for box in boxes]


def build_kelvin_geometry_dbu(
    spec: KelvinRoutingSpec, *, dbu_um: float
) -> dict[str, Any]:
    """Build only orthogonal integer-coordinate boxes for one deterministic layout DBU."""

    if dbu_um <= 0 or not math.isfinite(dbu_um):
        raise AnalysisError(
            code="INVALID_LAYOUT_DBU",
            message="Layout DBU must be finite and positive.",
            details={"dbu_um": dbu_um},
            next_action="Use the positive DBU reported by KLayout.",
        )
    route_width = _exact_grid(
        spec.routing_width_nm / 1000.0, dbu_um, field="routing_width_um"
    )
    pitch = _exact_grid(spec.mesh_pitch_nm / 1000.0, dbu_um, field="mesh_pitch_um")
    if route_width % 2:
        raise AnalysisError(
            code="KELVIN_ROUTING_WIDTH_ODD_DBU",
            message="Routing width must occupy an even number of DBU units.",
            details={"routing_width_dbu": route_width, "dbu_um": dbu_um},
            next_action="Use a finer DBU grid that centers the routing width exactly.",
        )

    common_name = "KELVIN_COMMON_VOLTAGE_SENSE_MESH"
    cells: dict[str, dict[str, Any]] = {
        common_name: {
            "boxes_dbu": _common_sense_boxes(
                width=route_width, pitch=pitch, dbu_um=dbu_um, spec=spec
            ),
            "instances": [],
        }
    }
    top_instances: list[dict[str, Any]] = []
    for split in spec.splits:
        length = _exact_grid(
            split.length_nm / 1000.0,
            dbu_um,
            field=f"{split.key}_length_um",
        )
        measured_width = _exact_grid(
            split.width_nm / 1000.0,
            dbu_um,
            field=f"{split.key}_width_um",
        )
        if length % 2 or measured_width % 2:
            raise AnalysisError(
                code="KELVIN_SPLIT_ODD_DBU",
                message="Measured width and length must have exact half-grid coordinates.",
                details={
                    "split": split.key,
                    "width_dbu": measured_width,
                    "length_dbu": length,
                },
                next_action="Use a finer layout DBU for the requested split dimensions.",
            )
        half_length = length // 2
        half_measured_width = measured_width // 2
        half_route = route_width // 2
        terminal_outer = half_length + route_width
        local_boxes = _sense_boxes(
            half_length=half_length,
            width=route_width,
            pitch=pitch,
            dbu_um=dbu_um,
            spec=spec,
        ) + _force_boxes(
            half_length=half_length,
            width=route_width,
            pitch=pitch,
            dbu_um=dbu_um,
            spec=spec,
        )
        cells[split.local_routing_cell_name] = {
            "boxes_dbu": local_boxes,
            "instances": [],
        }
        cells[split.cell_name] = {
            "boxes_dbu": [
                (-half_length, -half_measured_width, half_length, half_measured_width),
                (-terminal_outer, -half_route, -half_length, half_route),
                (half_length, -half_route, terminal_outer, half_route),
            ],
            "instances": [
                {"cell_name": common_name, "dx_dbu": 0, "dy_dbu": 0},
                {
                    "cell_name": split.local_routing_cell_name,
                    "dx_dbu": 0,
                    "dy_dbu": 0,
                },
            ],
        }
        top_instances.append(
            {
                "cell_name": split.cell_name,
                "dx_dbu": _exact_grid(
                    split.origin_um[0], dbu_um, field=f"{split.key}_origin_x_um"
                ),
                "dy_dbu": _exact_grid(
                    split.origin_um[1], dbu_um, field=f"{split.key}_origin_y_um"
                ),
            }
        )
    return {
        "cells": cells,
        "top_instances": top_instances,
        "m1": {"layer": spec.m1_layer, "datatype": spec.m1_datatype},
    }


def geometry_box_counts(geometry: Mapping[str, Any]) -> dict[str, int]:
    return {
        name: len(cell["boxes_dbu"])
        for name, cell in geometry["cells"].items()
    }
