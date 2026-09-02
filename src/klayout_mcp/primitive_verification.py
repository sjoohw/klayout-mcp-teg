"""Pure box-level verification for Phase 1 DUT-local primitives."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from .errors import AnalysisError


Box = tuple[float, float, float, float]


def _box(operation: Mapping[str, Any]) -> Box:
    values = operation.get("bbox_um")
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise AnalysisError(
            code="INVALID_PRIMITIVE_VERIFICATION_INPUT",
            message="Every primitive operation must contain bbox_um.",
            details={"operation": dict(operation)},
            next_action="Verify an axis-aligned box-only primitive.",
        )
    return tuple(float(value) for value in values)  # type: ignore[return-value]


def _positive_overlap(first: Box, second: Box, epsilon: float = 1e-12) -> bool:
    return (
        min(first[2], second[2]) - max(first[0], second[0]) > epsilon
        and min(first[3], second[3]) - max(first[1], second[1]) > epsilon
    )


def _touch_or_overlap(first: Box, second: Box, epsilon: float = 1e-12) -> bool:
    return not (
        first[2] < second[0] - epsilon
        or second[2] < first[0] - epsilon
        or first[3] < second[1] - epsilon
        or second[3] < first[1] - epsilon
    )


def _distance(first: Box, second: Box) -> float:
    dx = max(second[0] - first[2], first[0] - second[2], 0.0)
    dy = max(second[1] - first[3], first[1] - second[3], 0.0)
    return math.hypot(dx, dy)


def _component_count(boxes: Sequence[Box]) -> int:
    remaining = set(range(len(boxes)))
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            connected = {
                candidate
                for candidate in remaining
                if _positive_overlap(boxes[current], boxes[candidate])
            }
            remaining.difference_update(connected)
            stack.extend(connected)
    return components


def geometry_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def terminal_component_manifest(
    operations: Iterable[Mapping[str, Any]],
    terminals_um: Mapping[str, Sequence[float]],
    *,
    layer_role: str,
) -> dict[str, Any]:
    """Map every terminal point to one positive-overlap conductor component."""

    records = [operation for operation in operations if operation.get("layer") == layer_role]
    boxes = [_box(operation) for operation in records]
    if not boxes:
        raise AnalysisError(
            code="PRIMITIVE_TERMINAL_LAYER_EMPTY",
            message="The primitive has no boxes on its declared terminal layer.",
            details={"layer_role": layer_role},
            next_action="Add terminal conductor geometry before external routing.",
        )
    component_by_box: dict[int, int] = {}
    remaining = set(range(len(boxes)))
    component_id = 0
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        stack = [seed]
        component_by_box[seed] = component_id
        while stack:
            current = stack.pop()
            connected = {
                candidate
                for candidate in remaining
                if _positive_overlap(boxes[current], boxes[candidate])
            }
            for candidate in connected:
                component_by_box[candidate] = component_id
            remaining.difference_update(connected)
            stack.extend(sorted(connected, reverse=True))
        component_id += 1

    terminal_components: dict[str, int] = {}
    for terminal, raw_point in terminals_um.items():
        if (
            not isinstance(terminal, str)
            or not terminal
            or not isinstance(raw_point, (list, tuple))
            or len(raw_point) != 2
        ):
            raise AnalysisError(
                code="INVALID_PRIMITIVE_TERMINAL_POINT",
                message="Every primitive terminal must be a named [x, y] point.",
                details={"terminal": terminal, "point_um": raw_point},
                next_action="Correct the DUT-local terminal manifest.",
            )
        point = (float(raw_point[0]), float(raw_point[1]))
        containing = {
            component_by_box[index]
            for index, box in enumerate(boxes)
            if box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]
        }
        if len(containing) != 1:
            raise AnalysisError(
                code="PRIMITIVE_TERMINAL_COMPONENT_AMBIGUOUS",
                message="A terminal point must land on exactly one conductor component.",
                details={
                    "terminal": terminal,
                    "point_um": list(point),
                    "component_ids": sorted(containing),
                    "layer_role": layer_role,
                },
                next_action="Move the terminal landing or repair an unintended local short/open.",
            )
        terminal_components[terminal] = next(iter(containing))
    return {
        "layer_role": layer_role,
        "component_count": component_id,
        "terminal_component_ids": terminal_components,
        "component_id_by_box_index": [component_by_box[index] for index in range(len(boxes))],
    }


def verify_single_conductor_primitive(
    operations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    records = list(operations)
    boxes = [_box(operation) for operation in records]
    components = _component_count(boxes)
    if components != 1:
        raise AnalysisError(
            code="PRIMITIVE_CONNECTIVITY_FAILED",
            message="The resistor primitive is not one positive-overlap conductor.",
            details={"component_count": components},
            next_action="Add full-width positive-area junctions between DUT-local boxes.",
        )
    return {
        "box_only": all(operation.get("type") == "add_box" for operation in records),
        "component_count": components,
        "positive_overlap_connectivity": True,
        "geometry_fingerprint_sha256": geometry_fingerprint({"operations": records}),
    }


def verify_two_net_primitive(
    operations: Iterable[Mapping[str, Any]],
    *,
    required_clear_space_um: float,
) -> dict[str, Any]:
    records = list(operations)
    grouped: dict[str, list[Box]] = {}
    for operation in records:
        net = operation.get("net")
        if not isinstance(net, str) or not net:
            raise AnalysisError(
                code="INVALID_PRIMITIVE_VERIFICATION_INPUT",
                message="Every two-net primitive box needs an explicit net.",
                details={"operation": dict(operation)},
                next_action="Label each local conductor box with P or N.",
            )
        grouped.setdefault(net, []).append(_box(operation))
    if len(grouped) != 2:
        raise AnalysisError(
            code="PRIMITIVE_NET_COUNT_MISMATCH",
            message="The capacitor primitive must contain exactly two nets.",
            details={"nets": sorted(grouped)},
            next_action="Generate exactly the two direct capacitance terminals.",
        )
    component_counts = {net: _component_count(boxes) for net, boxes in grouped.items()}
    if any(count != 1 for count in component_counts.values()):
        raise AnalysisError(
            code="PRIMITIVE_CONNECTIVITY_FAILED",
            message="A capacitor terminal net contains disconnected boxes.",
            details={"component_counts": component_counts},
            next_action="Connect every finger to its bus with positive-area overlap.",
        )
    nets = sorted(grouped)
    pairs = [
        (first, second)
        for first in grouped[nets[0]]
        for second in grouped[nets[1]]
    ]
    if any(_touch_or_overlap(first, second) for first, second in pairs):
        raise AnalysisError(
            code="PRIMITIVE_CROSS_NET_SHORT",
            message="The two capacitor terminal nets touch or overlap.",
            details={"nets": nets},
            next_action="Increase finger/bus tip separation.",
        )
    minimum_space = min(_distance(first, second) for first, second in pairs)
    if minimum_space + 1e-12 < required_clear_space_um:
        raise AnalysisError(
            code="PRIMITIVE_CROSS_NET_SPACING_FAILED",
            message="The capacitor terminal nets violate declared clear space.",
            details={
                "actual_minimum_space_um": minimum_space,
                "required_clear_space_um": required_clear_space_um,
            },
            next_action="Increase the MOM finger/bus spacing.",
        )
    return {
        "box_only": all(operation.get("type") == "add_box" for operation in records),
        "net_count": 2,
        "component_counts": component_counts,
        "cross_net_short": False,
        "cross_net_minimum_space_um": round(minimum_space, 12),
        "required_clear_space_um": required_clear_space_um,
        "geometry_fingerprint_sha256": geometry_fingerprint({"operations": records}),
    }
