"""Deterministic bounded-search feasibility check for first-metal Manhattan routes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from .errors import AnalysisError


@dataclass(frozen=True, slots=True)
class _RouteRequest:
    connection_id: str
    net: str
    start: tuple[float, float]
    end: tuple[float, float]
    width: float
    clear_space: float
    preferred_waypoints: tuple[tuple[float, float], ...]
    obstacles: tuple[tuple[float, float, float, float], ...]


def _number(value: object, *, field: str, positive: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or (positive and float(value) <= 0)
        or (not positive and float(value) < 0)
    ):
        qualifier = "positive" if positive else "non-negative"
        raise AnalysisError(
            code="INVALID_M1_FEASIBILITY_INPUT",
            message=f"{field} must be a finite {qualifier} number.",
            details={"field": field, "value": value},
            next_action=f"Provide a finite {qualifier} {field} value.",
        )
    return float(value)


def _point(value: object, *, field: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AnalysisError(
            code="INVALID_M1_FEASIBILITY_INPUT",
            message=f"{field} must be [x, y].",
            details={"field": field, "value": value},
            next_action="Provide a two-coordinate point in microns.",
        )
    return (
        _number(value[0], field=f"{field}[0]"),
        _number(value[1], field=f"{field}[1]"),
    )


def _box(value: object, *, field: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise AnalysisError(
            code="INVALID_M1_FEASIBILITY_INPUT",
            message=f"{field} must be [x1, y1, x2, y2].",
            details={"field": field, "value": value},
            next_action="Provide a positive-area rectangular boundary or obstacle.",
        )
    box = tuple(_number(item, field=f"{field}[{index}]") for index, item in enumerate(value))
    if box[0] >= box[2] or box[1] >= box[3]:
        raise AnalysisError(
            code="INVALID_M1_FEASIBILITY_INPUT",
            message=f"{field} must have positive area.",
            details={"field": field, "value": list(box)},
            next_action="Ensure x1 < x2 and y1 < y2.",
        )
    return box  # type: ignore[return-value]


def _segment_boxes(
    points: Sequence[tuple[float, float]], width: float
) -> list[tuple[float, float, float, float]]:
    half = width / 2.0
    boxes: list[tuple[float, float, float, float]] = []
    for first, second in zip(points, points[1:]):
        if first == second:
            continue
        if first[0] != second[0] and first[1] != second[1]:
            raise AssertionError("candidate route is not Manhattan")
        boxes.append(
            (
                min(first[0], second[0]) - half,
                min(first[1], second[1]) - half,
                max(first[0], second[0]) + half,
                max(first[1], second[1]) + half,
            )
        )
    return boxes


def _positive_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    *,
    epsilon: float = 1e-12,
) -> bool:
    return (
        min(first[2], second[2]) - max(first[0], second[0]) > epsilon
        and min(first[3], second[3]) - max(first[1], second[1]) > epsilon
    )


def _expanded(
    box: tuple[float, float, float, float], amount: float
) -> tuple[float, float, float, float]:
    return (box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount)


def _dedupe_points(
    points: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    result: list[tuple[float, float]] = []
    for point in points:
        if not result or result[-1] != point:
            result.append(point)
    return tuple(result)


def _track_values(values: Iterable[float]) -> list[float]:
    ordered = sorted(set(round(value, 12) for value in values))
    midpoints = [
        (first + second) / 2.0
        for first, second in zip(ordered, ordered[1:])
        if second > first
    ]
    return sorted(set(ordered + midpoints))


def analyze_first_metal_feasibility(
    connections: Iterable[Mapping[str, Any]],
    *,
    boundary_um: Sequence[float],
    obstacles_um: Iterable[Sequence[float]] = (),
    max_candidates_per_connection: int = 96,
) -> dict[str, Any]:
    """Find non-conflicting zero/one/two-bend routes using a finite critical-track set.

    A successful result proves the returned geometry is feasible under this abstract
    rectangle model.  Failure is deliberately not called a proof of physical M1
    impossibility because paths with more bends or a different placement may exist.
    """

    boundary = _box(boundary_um, field="boundary_um")
    obstacles = [
        _box(value, field=f"obstacles_um[{index}]")
        for index, value in enumerate(obstacles_um)
    ]
    if isinstance(max_candidates_per_connection, bool) or max_candidates_per_connection < 1:
        raise AnalysisError(
            code="INVALID_M1_FEASIBILITY_INPUT",
            message="max_candidates_per_connection must be a positive integer.",
            details={"max_candidates_per_connection": max_candidates_per_connection},
            next_action="Provide a positive bounded-search candidate limit.",
        )

    requests: list[_RouteRequest] = []
    for index, record in enumerate(connections, start=1):
        net = record.get("net") if isinstance(record, Mapping) else None
        if not isinstance(net, str) or not net.strip():
            raise AnalysisError(
                code="INVALID_M1_FEASIBILITY_INPUT",
                message="Every routing connection requires a non-empty net.",
                details={"connection_index": index, "connection": record},
                next_action="Provide net, start_um, end_um, width_um, and clear_space_um.",
            )
        preferred_raw = record.get("preferred_waypoints_um", ())
        obstacles_raw = record.get("obstacles_um", ())
        if preferred_raw is None:
            preferred_raw = ()
        if obstacles_raw is None:
            obstacles_raw = ()
        if not isinstance(preferred_raw, (list, tuple)) or not isinstance(
            obstacles_raw, (list, tuple)
        ):
            raise AnalysisError(
                code="INVALID_M1_FEASIBILITY_INPUT",
                message="preferred_waypoints_um and obstacles_um must be lists when provided.",
                details={
                    "connection_index": index,
                    "preferred_waypoints_um": preferred_raw,
                    "obstacles_um": obstacles_raw,
                },
                next_action="Provide lists of [x,y] waypoints and [x1,y1,x2,y2] obstacles.",
            )
        request = _RouteRequest(
            connection_id=(
                str(record.get("connection_id")).strip()
                if record.get("connection_id") is not None
                else f"C{index:03d}"
            ),
            net=net.strip(),
            start=_point(record.get("start_um"), field=f"connections[{index}].start_um"),
            end=_point(record.get("end_um"), field=f"connections[{index}].end_um"),
            width=_number(record.get("width_um"), field=f"connections[{index}].width_um", positive=True),
            clear_space=_number(
                record.get("clear_space_um"),
                field=f"connections[{index}].clear_space_um",
            ),
            preferred_waypoints=tuple(
                _point(
                    waypoint,
                    field=f"connections[{index}].preferred_waypoints_um[{waypoint_index}]",
                )
                for waypoint_index, waypoint in enumerate(
                    preferred_raw
                )
            ),
            obstacles=tuple(
                _box(
                    obstacle,
                    field=f"connections[{index}].obstacles_um[{obstacle_index}]",
                )
                for obstacle_index, obstacle in enumerate(obstacles_raw)
            ),
        )
        if request.start == request.end:
            raise AnalysisError(
                code="INVALID_M1_FEASIBILITY_INPUT",
                message="A routing connection has identical start and end points.",
                details={"connection_index": index, "net": request.net},
                next_action="Provide two distinct landing center points.",
            )
        requests.append(request)

    connection_ids = [request.connection_id for request in requests]
    if any(not value for value in connection_ids) or len(set(connection_ids)) != len(connection_ids):
        raise AnalysisError(
            code="INVALID_M1_CONNECTION_ID",
            message="Routing connection IDs must be non-empty and unique.",
            details={"connection_ids": connection_ids},
            next_action="Use one stable DUT:terminal connection_id per direct route.",
        )

    if not requests:
        return {
            "status": "not_evaluated_missing_connections",
            "feasible": None,
            "failure_proves_m1_impossible": False,
            "routes": [],
        }

    all_x = [boundary[0], boundary[2]]
    all_y = [boundary[1], boundary[3]]
    for request in requests:
        all_x.extend([request.start[0], request.end[0]])
        all_y.extend([request.start[1], request.end[1]])
        offset = request.width / 2.0 + request.clear_space
        for obstacle in (*obstacles, *request.obstacles):
            all_x.extend([obstacle[0] - offset, obstacle[2] + offset])
            all_y.extend([obstacle[1] - offset, obstacle[3] + offset])
    x_tracks = _track_values(all_x)
    y_tracks = _track_values(all_y)

    candidate_sets: list[list[dict[str, Any]]] = []
    candidate_evidence: list[dict[str, Any]] = []
    for request in requests:
        point_sets: list[tuple[tuple[float, float], ...]] = []
        preferred_points: tuple[tuple[float, float], ...] | None = None
        sx, sy = request.start
        ex, ey = request.end
        if request.preferred_waypoints:
            preferred_points = _dedupe_points(
                (request.start, *request.preferred_waypoints, request.end)
            )
            for first, second in zip(preferred_points, preferred_points[1:]):
                if first[0] != second[0] and first[1] != second[1]:
                    raise AnalysisError(
                        code="NON_ORTHOGONAL_PREFERRED_WAYPOINTS",
                        message="Preferred routing waypoints must form a Manhattan path.",
                        details={
                            "connection_id": request.connection_id,
                            "first_um": list(first),
                            "second_um": list(second),
                        },
                        next_action="Align each consecutive preferred waypoint horizontally or vertically.",
                    )
            point_sets.append(preferred_points)
        if sx == ex or sy == ey:
            point_sets.append((request.start, request.end))
        point_sets.extend(
            [
                _dedupe_points((request.start, (ex, sy), request.end)),
                _dedupe_points((request.start, (sx, ey), request.end)),
            ]
        )
        for track in x_tracks:
            point_sets.append(
                _dedupe_points((request.start, (track, sy), (track, ey), request.end))
            )
        for track in y_tracks:
            point_sets.append(
                _dedupe_points((request.start, (sx, track), (ex, track), request.end))
            )

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[tuple[float, float], ...]] = set()
        rejection_counts = {
            "duplicate_or_degenerate": 0,
            "outside_boundary": 0,
            "obstacle_overlap": 0,
        }
        for points in point_sets:
            if points in seen or len(points) < 2:
                rejection_counts["duplicate_or_degenerate"] += 1
                continue
            seen.add(points)
            boxes = _segment_boxes(points, request.width)
            if any(
                box[0] < boundary[0]
                or box[1] < boundary[1]
                or box[2] > boundary[2]
                or box[3] > boundary[3]
                for box in boxes
            ):
                rejection_counts["outside_boundary"] += 1
                continue
            if any(
                _positive_overlap(_expanded(box, request.clear_space), obstacle)
                for box in boxes
                for obstacle in (*obstacles, *request.obstacles)
            ):
                rejection_counts["obstacle_overlap"] += 1
                continue
            length = sum(
                abs(second[0] - first[0]) + abs(second[1] - first[1])
                for first, second in zip(points, points[1:])
            )
            candidates.append(
                {
                    "points": points,
                    "boxes": boxes,
                    "length_um": length,
                    "preference_rank": 0 if points == preferred_points else 1,
                }
            )
        candidates.sort(
            key=lambda item: (
                item["preference_rank"],
                item["length_um"],
                len(item["points"]),
                item["points"],
            )
        )
        retained = candidates[:max_candidates_per_connection]
        candidate_sets.append(retained)
        candidate_evidence.append(
            {
                "connection_id": request.connection_id,
                "generated_point_set_count": len(point_sets),
                "unique_point_set_count": len(seen),
                "accepted_before_cap": len(candidates),
                "retained_candidate_count": len(retained),
                "candidate_cap_truncated": len(candidates) > len(retained),
                "rejection_counts": rejection_counts,
            }
        )

    order = sorted(range(len(requests)), key=lambda index: len(candidate_sets[index]))
    selected: dict[int, dict[str, Any]] = {}
    search_stats = {
        "nodes_visited": 0,
        "compatibility_rejections": 0,
        "backtracks": 0,
        "disconnected_complete_assignments": 0,
    }

    def compatible(index: int, candidate: dict[str, Any]) -> bool:
        request = requests[index]
        for other_index, other_candidate in selected.items():
            other = requests[other_index]
            if request.net == other.net:
                continue
            required_space = max(request.clear_space, other.clear_space)
            for first in candidate["boxes"]:
                for second in other_candidate["boxes"]:
                    if _positive_overlap(
                        _expanded(first, required_space / 2.0),
                        _expanded(second, required_space / 2.0),
                    ):
                        return False
        return True

    def selected_net_component_counts() -> dict[str, int]:
        grouped: dict[str, list[tuple[float, float, float, float]]] = {}
        for index, candidate in selected.items():
            grouped.setdefault(requests[index].net, []).extend(candidate["boxes"])
        counts: dict[str, int] = {}
        for net, boxes in grouped.items():
            remaining = set(range(len(boxes)))
            components = 0
            while remaining:
                components += 1
                stack = [remaining.pop()]
                while stack:
                    current = stack.pop()
                    connected = {
                        candidate_index
                        for candidate_index in remaining
                        if _positive_overlap(boxes[current], boxes[candidate_index])
                    }
                    remaining.difference_update(connected)
                    stack.extend(connected)
            counts[net] = components
        return counts

    def search(position: int) -> bool:
        search_stats["nodes_visited"] += 1
        if position == len(order):
            connected = all(
                count == 1 for count in selected_net_component_counts().values()
            )
            if not connected:
                search_stats["disconnected_complete_assignments"] += 1
            return connected
        index = order[position]
        for candidate in candidate_sets[index]:
            if not compatible(index, candidate):
                search_stats["compatibility_rejections"] += 1
                continue
            selected[index] = candidate
            if search(position + 1):
                return True
            selected.pop(index, None)
            search_stats["backtracks"] += 1
        return False

    feasible = all(candidate_sets) and search(0)
    routes: list[dict[str, Any]] = []
    net_component_counts: dict[str, int] = {}
    if feasible:
        net_component_counts = selected_net_component_counts()
        for index, request in enumerate(requests):
            candidate = selected[index]
            routes.append(
                {
                    "connection_id": request.connection_id,
                    "net": request.net,
                    "width_um": request.width,
                    "clear_space_um": request.clear_space,
                    "points_um": [list(point) for point in candidate["points"]],
                    "length_um": candidate["length_um"],
                    "bend_count": max(0, len(candidate["points"]) - 2),
                    "preferred_waypoints_used": candidate["preference_rank"] == 0,
                    "connection_obstacle_count": len(request.obstacles),
                }
            )

    any_truncated = any(item["candidate_cap_truncated"] for item in candidate_evidence)
    blockers = [
        {
            "connection_id": item["connection_id"],
            "reason": "no_candidate_within_declared_search_scope",
            "rejection_counts": item["rejection_counts"],
        }
        for item in candidate_evidence
        if item["retained_candidate_count"] == 0
    ]
    if not feasible and not blockers:
        blockers.append(
            {
                "reason": "no_mutually_compatible_route_assignment_within_retained_candidates",
                "compatibility_rejections": search_stats["compatibility_rejections"],
                "disconnected_complete_assignments": search_stats[
                    "disconnected_complete_assignments"
                ],
            }
        )
    evidence_payload = {
        "boundary_um": list(boundary),
        "global_obstacles_um": [list(box) for box in obstacles],
        "critical_x_tracks_um": x_tracks,
        "critical_y_tracks_um": y_tracks,
        "connection_order": connection_ids,
        "connections": [
            {
                "connection_id": request.connection_id,
                "net": request.net,
                "start_um": list(request.start),
                "end_um": list(request.end),
                "width_um": request.width,
                "clear_space_um": request.clear_space,
                "preferred_waypoints_um": [
                    list(point) for point in request.preferred_waypoints
                ],
                "obstacles_um": [list(box) for box in request.obstacles],
            }
            for request in requests
        ],
        "search_order": [connection_ids[index] for index in order],
        "max_candidates_per_connection": max_candidates_per_connection,
        "candidate_evidence": candidate_evidence,
        "search_stats": search_stats,
    }
    return {
        "status": "feasible_on_first_metal" if feasible else "not_found_bounded_search",
        "feasible": bool(feasible),
        "boundary_um": list(boundary),
        "obstacle_count": len(obstacles),
        "connection_count": len(requests),
        "candidate_counts": [len(candidates) for candidates in candidate_sets],
        "search_scope": "preferred_waypoints_then_critical_tracks_with_at_most_two_bends",
        "search_configuration": {
            "routing_layer_scope": "first_metal_only",
            "max_generated_bends_excluding_explicit_preferred": 2,
            "explicit_preferred_waypoints_are_user_supplied": True,
            "max_candidates_per_connection": max_candidates_per_connection,
            "candidate_order": "preferred_then_length_then_point_count_then_lexicographic",
            "critical_x_tracks_um": x_tracks,
            "critical_y_tracks_um": y_tracks,
        },
        "search_geometry": {
            "boundary_um": list(boundary),
            "global_obstacles_um": [list(box) for box in obstacles],
            "per_connection_obstacles_um": {
                request.connection_id: [list(box) for box in request.obstacles]
                for request in requests
            },
        },
        "candidate_evidence": candidate_evidence,
        "search_evidence": evidence_payload,
        "search_order": [connection_ids[index] for index in order],
        "search_stats": search_stats,
        "retained_candidate_space_exhausted": bool(not feasible),
        "candidate_cap_truncated": any_truncated,
        "search_scope_is_universal_m1_proof": False,
        "failure_proves_m1_impossible": False,
        "blockers": blockers,
        "routes": routes,
        "net_component_counts": net_component_counts,
        "route_fingerprint_sha256": (
            hashlib.sha256(
                json.dumps(routes, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if feasible
            else None
        ),
        "search_evidence_fingerprint_sha256": hashlib.sha256(
            json.dumps(
                evidence_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
