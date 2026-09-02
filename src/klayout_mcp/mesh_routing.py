"""Process-agnostic deterministic contact and orthogonal mesh synthesis.

The compiler works in integer database units after one exact micron boundary
conversion.  Process adapters provide rules and port/corridor geometry; they do
not provide routing algorithms.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal, Sequence

from .dbu_grid import DbuGridError, micron_to_dbu
from .errors import AnalysisError


Direction = Literal["+x", "-x", "+y", "-y"]
ArrayAxis = Literal["x", "y"]
Alignment = Literal["centered", "away_from_positive", "away_from_negative"]


def _dbu(value_um: object, dbu_um: float, *, field: str) -> int:
    if (
        isinstance(value_um, bool)
        or not isinstance(value_um, (int, float))
        or not math.isfinite(float(value_um))
    ):
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message=f"{field} must be a finite micron value.",
            details={"field": field, "value": value_um},
            next_action="Provide a finite value on the confirmed process DBU grid.",
        )
    try:
        return micron_to_dbu(value_um, dbu_um)
    except DbuGridError as exc:
        raise AnalysisError(
            code="MESH_SYNTHESIS_OFF_GRID",
            message=f"{field} is not exactly representable on the process DBU grid.",
            details={"field": field, "value_um": value_um, "dbu_um": dbu_um},
            next_action="Use exact integer-DBU geometry; do not round materially off-grid values.",
        ) from exc


def _positive_dbu(value_um: object, dbu_um: float, *, field: str) -> int:
    value = _dbu(value_um, dbu_um, field=field)
    if value <= 0:
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message=f"{field} must be positive.",
            details={"field": field, "value_um": value_um},
            next_action="Provide a positive process-legal value.",
        )
    return value


def _point_dbu(value: object, dbu_um: float, *, field: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message=f"{field} must be [x, y].",
            details={"field": field, "value": value},
            next_action="Provide one explicit orthogonal point.",
        )
    return (
        _dbu(value[0], dbu_um, field=f"{field}[0]"),
        _dbu(value[1], dbu_um, field=f"{field}[1]"),
    )


def _box_dbu(value: object, dbu_um: float, *, field: str) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message=f"{field} must be [x1, y1, x2, y2].",
            details={"field": field, "value": value},
            next_action="Provide a positive-area axis-aligned corridor.",
        )
    box = tuple(
        _dbu(item, dbu_um, field=f"{field}[{index}]")
        for index, item in enumerate(value)
    )
    if box[0] >= box[2] or box[1] >= box[3]:
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message=f"{field} must have positive area.",
            details={"field": field, "value": list(box)},
            next_action="Ensure x1 < x2 and y1 < y2.",
        )
    return box  # type: ignore[return-value]


def _centered_interval(center: int, width: int) -> tuple[int, int]:
    left = center - width // 2
    return left, left + width


def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def _um_box(box: Sequence[int], dbu_um: float) -> list[float]:
    return [round(int(value) * dbu_um, 12) for value in box]


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _global_box(
    *,
    start: tuple[int, int],
    direction: Direction,
    u1: int,
    v1: int,
    u2: int,
    v2: int,
) -> tuple[int, int, int, int]:
    sx, sy = start
    if direction == "+x":
        return sx + u1, sy + v1, sx + u2, sy + v2
    if direction == "-x":
        return sx - u2, sy + v1, sx - u1, sy + v2
    if direction == "+y":
        return sx + v1, sy + u1, sx + v2, sy + u2
    return sx + v1, sy - u2, sx + v2, sy - u1


def synthesize_staged_mesh_segment(
    *,
    dbu_um: float,
    start_um: Sequence[float],
    end_um: Sequence[float],
    corridor_um: Sequence[float],
    rail_width_um: float,
    rail_space_um: float,
    landing_span_um: float,
    transition_guard_um: float = 0.0,
    cross_tie_pitch_um: float | None = None,
    final_tie_reserve_um: float | None = None,
    receiving_tie_present: bool = True,
    minimum_rail_count: int = 2,
    cell: str = "ROUTE",
    layer_role: str = "m1",
) -> dict[str, Any]:
    """Compile one maximum-envelope orthogonal staircase mesh segment."""

    if (
        not isinstance(dbu_um, (int, float))
        or isinstance(dbu_um, bool)
        or not math.isfinite(float(dbu_um))
        or dbu_um <= 0
    ):
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_DBU",
            message="dbu_um must be finite and positive.",
            details={"dbu_um": dbu_um},
            next_action="Use the confirmed layout DBU.",
        )
    dbu_value = float(dbu_um)
    start = _point_dbu(start_um, dbu_value, field="start_um")
    end = _point_dbu(end_um, dbu_value, field="end_um")
    corridor = _box_dbu(corridor_um, dbu_value, field="corridor_um")
    rail_width = _positive_dbu(rail_width_um, dbu_value, field="rail_width_um")
    rail_space = _positive_dbu(rail_space_um, dbu_value, field="rail_space_um")
    landing_span = _positive_dbu(landing_span_um, dbu_value, field="landing_span_um")
    guard = _dbu(transition_guard_um, dbu_value, field="transition_guard_um")
    if guard < 0:
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message="transition_guard_um must be non-negative.",
            details={"transition_guard_um": transition_guard_um},
            next_action="Use zero or a positive guard distance.",
        )
    if start[1] == end[1] and start[0] != end[0]:
        direction: Direction = "+x" if end[0] > start[0] else "-x"
        length = abs(end[0] - start[0])
        transverse_min = corridor[1] - start[1]
        transverse_max = corridor[3] - start[1]
    elif start[0] == end[0] and start[1] != end[1]:
        direction = "+y" if end[1] > start[1] else "-y"
        length = abs(end[1] - start[1])
        transverse_min = corridor[0] - start[0]
        transverse_max = corridor[2] - start[0]
    else:
        raise AnalysisError(
            code="NON_ORTHOGONAL_ROUTING_FORBIDDEN",
            message="A staged mesh segment requires one non-zero Manhattan segment.",
            details={"start_um": list(start_um), "end_um": list(end_um)},
            next_action="Split bends into explicit horizontal and vertical mesh segments.",
        )
    if not (
        corridor[0] <= start[0] <= corridor[2]
        and corridor[1] <= start[1] <= corridor[3]
        and corridor[0] <= end[0] <= corridor[2]
        and corridor[1] <= end[1] <= corridor[3]
    ):
        raise AnalysisError(
            code="MESH_ENDPOINT_OUTSIDE_CORRIDOR",
            message="The staged mesh endpoints must lie inside the declared corridor.",
            details={"start_dbu": list(start), "end_dbu": list(end), "corridor_dbu": list(corridor)},
            next_action="Provide a confirmed obstacle-free corridor containing both endpoints.",
        )

    pitch = rail_width + rail_space
    half_low = rail_width // 2
    half_high = rail_width - half_low
    min_index = _ceil_div(transverse_min + half_low, pitch)
    max_index = (transverse_max - half_high) // pitch
    offsets = tuple(index * pitch for index in range(min_index, max_index + 1))
    if not offsets or 0 not in offsets:
        raise AnalysisError(
            code="MESH_BASELINE_DOES_NOT_FIT",
            message="The corridor cannot contain the baseline rail on the declared pitch grid.",
            details={
                "corridor_dbu": list(corridor),
                "rail_width_dbu": rail_width,
                "rail_space_dbu": rail_space,
            },
            next_action="Move the port baseline or enlarge the confirmed corridor.",
        )
    if (
        isinstance(minimum_rail_count, bool)
        or not isinstance(minimum_rail_count, int)
        or minimum_rail_count < 2
    ):
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_INPUT",
            message="minimum_rail_count must be an integer of at least two.",
            details={"minimum_rail_count": minimum_rail_count},
            next_action="Require at least two parallel rails for a hole-bearing mesh.",
        )
    if len(offsets) < minimum_rail_count:
        raise AnalysisError(
            code="MESH_CORRIDOR_TOO_NARROW",
            message="The corridor cannot contain the required parallel mesh rails.",
            details={
                "available_rail_count": len(offsets),
                "minimum_rail_count": minimum_rail_count,
            },
            next_action="Enlarge or reposition the corridor; do not emit a token single rail.",
        )

    start_distance_by_offset: dict[int, int] = {}
    effective_landing_span = max(landing_span, rail_width)
    for offset in offsets:
        if offset == 0:
            # The persistent baseline is the terminal-access conductor.  A
            # narrower centered landing still has positive-area overlap; moving
            # this rail away from u=0 would create an electrical open.
            start_distance_by_offset[offset] = 0
            continue
        excess_twice = max(
            0, 2 * abs(offset) + rail_width - effective_landing_span
        )
        expansion_steps = _ceil_div(excess_twice, 2 * pitch)
        start_distance_by_offset[offset] = expansion_steps * pitch + guard
    reserve = (
        pitch
        if final_tie_reserve_um is None
        else _positive_dbu(final_tie_reserve_um, dbu_value, field="final_tie_reserve_um")
    )
    max_start = max(start_distance_by_offset.values())
    if max_start + reserve >= length:
        raise AnalysisError(
            code="INSUFFICIENT_CORRIDOR_FOR_MESH",
            message="The corridor is too short for its maximum-envelope staged transition.",
            details={
                "length_dbu": length,
                "maximum_stage_start_dbu": max_start,
                "final_tie_reserve_dbu": reserve,
                "rail_count": len(offsets),
            },
            next_action=(
                "Enlarge or reposition the corridor, or explicitly confirm another metal; "
                "do not silently reduce the mesh."
            ),
        )

    boxes: set[tuple[int, int, int, int]] = set()
    for offset in offsets:
        v1, v2 = _centered_interval(offset, rail_width)
        boxes.add(
            _global_box(
                start=start,
                direction=direction,
                u1=start_distance_by_offset[offset],
                v1=v1,
                u2=length,
                v2=v2,
            )
        )

    tie_distances = set(start_distance_by_offset.values()) - {0}
    if cross_tie_pitch_um is not None:
        tie_pitch = _positive_dbu(
            cross_tie_pitch_um, dbu_value, field="cross_tie_pitch_um"
        )
        distance = tie_pitch
        while distance < length - reserve:
            tie_distances.add(distance)
            distance += tie_pitch
    if not receiving_tie_present:
        tie_distances.add(length)
    for distance in sorted(tie_distances):
        active = [
            offset
            for offset, start_distance in start_distance_by_offset.items()
            if start_distance <= distance
        ]
        if not active:
            continue
        if distance == length and not receiving_tie_present:
            # No receiving conductor exists beyond the endpoint. Keep the tie
            # inside the confirmed corridor with its outer face flush to it.
            u1, u2 = length - rail_width, length
        else:
            u1, u2 = _centered_interval(distance, rail_width)
        v1 = _centered_interval(min(active), rail_width)[0]
        v2 = _centered_interval(max(active), rail_width)[1]
        boxes.add(
            _global_box(
                start=start,
                direction=direction,
                u1=u1,
                v1=v1,
                u2=u2,
                v2=v2,
            )
        )

    ordered_boxes = sorted(boxes)
    operations = [
        {"type": "add_box", "cell": cell, "layer": layer_role, "bbox_um": _um_box(box, dbu_value)}
        for box in ordered_boxes
    ]
    evidence = {
        "contract_version": 1,
        "optimization_status": "geometry_maximized_not_pex_proven",
        "single_rail_fallback_allowed": False,
        "direction": direction,
        "dbu_um": dbu_value,
        "start_dbu": list(start),
        "end_dbu": list(end),
        "corridor_dbu": list(corridor),
        "rail_width_dbu": rail_width,
        "rail_space_dbu": rail_space,
        "rail_pitch_dbu": pitch,
        "rail_offsets_dbu": list(offsets),
        "rail_count": len(offsets),
        "minimum_rail_count": minimum_rail_count,
        "used_transverse_span_dbu": max(offsets) - min(offsets) + rail_width,
        "stage_start_dbu_by_offset": [
            [offset, start_distance_by_offset[offset]] for offset in offsets
        ],
        "declared_landing_span_dbu": landing_span,
        "effective_transition_landing_span_dbu": effective_landing_span,
        "cross_tie_count": len(tie_distances),
        "receiving_interface_landing_count": len(offsets),
        "operation_count": len(operations),
    }
    evidence["synthesis_fingerprint_sha256"] = _fingerprint(
        {"evidence": evidence, "boxes_dbu": [list(box) for box in ordered_boxes]}
    )
    return {"ok": True, "operations": operations, "boxes_dbu": [list(box) for box in ordered_boxes], "evidence": evidence}


def synthesize_maximum_contact_array(
    *,
    dbu_um: float,
    array_center_um: Sequence[float],
    array_axis: ArrayAxis,
    available_width_um: float,
    contact_size_um: float,
    contact_space_um: float,
    active_enclosure_um: float,
    metal_enclosure_um: float,
    metal_space_um: float,
    alignment: Alignment = "centered",
    neighbor_metal_near_edge_um: float | None = None,
    neighbor_side: Literal["positive", "negative"] | None = None,
    neighbor_clearance_um: float | None = None,
    cell: str = "DUT",
    contact_layer_role: str = "contact",
    metal_layer_role: str = "m1",
) -> dict[str, Any]:
    """Pack the maximum legal 1-D cut array and individual metal landings."""

    if (
        not isinstance(dbu_um, (int, float))
        or isinstance(dbu_um, bool)
        or not math.isfinite(float(dbu_um))
        or dbu_um <= 0
    ):
        raise AnalysisError(
            code="INVALID_MESH_SYNTHESIS_DBU",
            message="dbu_um must be finite and positive.",
            details={"dbu_um": dbu_um},
            next_action="Use the confirmed positive layout DBU.",
        )
    if array_axis not in {"x", "y"}:
        raise AnalysisError(
            code="INVALID_CONTACT_ARRAY_AXIS",
            message="array_axis must be x or y.",
            details={"array_axis": array_axis},
            next_action="Confirm the terminal contact-array direction.",
        )
    if alignment not in {
        "centered",
        "away_from_positive",
        "away_from_negative",
    }:
        raise AnalysisError(
            code="INVALID_CONTACT_ARRAY_ALIGNMENT",
            message="The contact-array alignment token is unsupported.",
            details={"alignment": alignment},
            next_action="Use centered, away_from_positive, or away_from_negative.",
        )
    if neighbor_side not in {None, "positive", "negative"}:
        raise AnalysisError(
            code="INVALID_CONTACT_NEIGHBOR_SIDE",
            message="neighbor_side must be positive or negative.",
            details={"neighbor_side": neighbor_side},
            next_action="Confirm which side of the contact-array axis contains the neighbor.",
        )
    dbu_value = float(dbu_um)
    center = _point_dbu(array_center_um, dbu_value, field="array_center_um")
    available_width = _positive_dbu(
        available_width_um, dbu_value, field="available_width_um"
    )
    cut = _positive_dbu(contact_size_um, dbu_value, field="contact_size_um")
    cut_space = _positive_dbu(contact_space_um, dbu_value, field="contact_space_um")
    active_enclosure = _positive_dbu(
        active_enclosure_um, dbu_value, field="active_enclosure_um"
    )
    metal_enclosure = _positive_dbu(
        metal_enclosure_um, dbu_value, field="metal_enclosure_um"
    )
    metal_space = _positive_dbu(metal_space_um, dbu_value, field="metal_space_um")
    metal_landing = cut + 2 * metal_enclosure
    pitch = max(cut + cut_space, metal_landing + metal_space)
    axis_center = center[0] if array_axis == "x" else center[1]
    low_edge, high_edge = _centered_interval(axis_center, available_width)
    low_center = low_edge + active_enclosure + cut // 2
    high_center = high_edge - active_enclosure - (cut - cut // 2)
    if low_center > high_center:
        raise AnalysisError(
            code="CONTACT_ARRAY_DOES_NOT_FIT",
            message="The terminal cannot contain one rule-compliant contact.",
            details={"available_width_dbu": available_width, "contact_size_dbu": cut},
            next_action="Increase terminal width or use a separately verified contact topology.",
        )
    maximum_count = (high_center - low_center) // pitch + 1

    if (neighbor_metal_near_edge_um is None) != (neighbor_side is None) or (
        neighbor_side is not None and neighbor_clearance_um is None
    ):
        raise AnalysisError(
            code="INCOMPLETE_CONTACT_NEIGHBOR_CONSTRAINT",
            message="Neighbor edge, side, and clearance must be provided together.",
            details={
                "neighbor_metal_near_edge_um": neighbor_metal_near_edge_um,
                "neighbor_side": neighbor_side,
                "neighbor_clearance_um": neighbor_clearance_um,
            },
            next_action="Provide all neighbor fields or omit all of them.",
        )
    neighbor_edge = (
        None
        if neighbor_metal_near_edge_um is None
        else _dbu(
            neighbor_metal_near_edge_um,
            dbu_value,
            field="neighbor_metal_near_edge_um",
        )
    )
    neighbor_clearance = (
        0
        if neighbor_clearance_um is None
        else _positive_dbu(
            neighbor_clearance_um, dbu_value, field="neighbor_clearance_um"
        )
    )

    selected: tuple[int, ...] | None = None
    for count in range(maximum_count, 0, -1):
        span = (count - 1) * pitch
        if alignment == "away_from_positive":
            first = low_center
        elif alignment == "away_from_negative":
            first = high_center - span
        else:
            first = axis_center - span // 2
            first = min(max(first, low_center), high_center - span)
        centers = tuple(first + index * pitch for index in range(count))
        if neighbor_edge is not None and neighbor_side == "positive":
            metal_outer = _centered_interval(centers[-1], metal_landing)[1]
            if neighbor_edge - metal_outer < neighbor_clearance:
                continue
        if neighbor_edge is not None and neighbor_side == "negative":
            metal_outer = _centered_interval(centers[0], metal_landing)[0]
            if metal_outer - neighbor_edge < neighbor_clearance:
                continue
        selected = centers
        break
    if selected is None:
        raise AnalysisError(
            code="CONTACT_ARRAY_DOES_NOT_FIT",
            message="No contact satisfies the confirmed terminal and neighbor-metal rules.",
            details={"maximum_cut_only_count": maximum_count},
            next_action="Change the terminal topology or use a separately verified process adapter.",
        )

    cut_boxes: list[tuple[int, int, int, int]] = []
    metal_boxes: list[tuple[int, int, int, int]] = []
    for axis_value in selected:
        if array_axis == "x":
            cx, cy = axis_value, center[1]
        else:
            cx, cy = center[0], axis_value
        x1, x2 = _centered_interval(cx, cut)
        y1, y2 = _centered_interval(cy, cut)
        cut_boxes.append((x1, y1, x2, y2))
        mx1, mx2 = _centered_interval(cx, metal_landing)
        my1, my2 = _centered_interval(cy, metal_landing)
        metal_boxes.append((mx1, my1, mx2, my2))
    operations = [
        {"type": "add_box", "cell": cell, "layer": contact_layer_role, "bbox_um": _um_box(box, dbu_value)}
        for box in sorted(cut_boxes)
    ] + [
        {"type": "add_box", "cell": cell, "layer": metal_layer_role, "bbox_um": _um_box(box, dbu_value)}
        for box in sorted(metal_boxes)
    ]
    evidence = {
        "contract_version": 1,
        "optimization_status": "geometry_maximized_not_pex_proven",
        "dbu_um": dbu_value,
        "array_axis": array_axis,
        "alignment": alignment,
        "contact_pitch_dbu": pitch,
        "contact_size_dbu": cut,
        "metal_landing_size_dbu": metal_landing,
        "cut_only_maximum_count": maximum_count,
        "legal_contact_count": len(selected),
        "contact_centers_dbu": list(selected),
        "neighbor_constraint_reduced_count": len(selected) < maximum_count,
        "fixed_count_policy": "maximum_legal_after_all_declared_constraints",
    }
    evidence["synthesis_fingerprint_sha256"] = _fingerprint(
        {
            "evidence": evidence,
            "contact_boxes_dbu": [list(box) for box in sorted(cut_boxes)],
            "metal_boxes_dbu": [list(box) for box in sorted(metal_boxes)],
        }
    )
    return {
        "ok": True,
        "operations": operations,
        "contact_boxes_dbu": [list(box) for box in sorted(cut_boxes)],
        "metal_boxes_dbu": [list(box) for box in sorted(metal_boxes)],
        "evidence": evidence,
    }
