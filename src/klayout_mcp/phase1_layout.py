"""Compose injected primitives, synthetic PAD_MESH cells, and routed M1 mesh geometry.

Real pad-macro preservation is provided by the separate immutable pad-macro overlay path.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .errors import AnalysisError
from .manhattan_drawing import build_manhattan_drawing_plan
from .mesh_routing import synthesize_mesh_polyline
from .process_capability import required_metal_space_um, validate_process_capability
from .primitive_verification import geometry_fingerprint, terminal_component_manifest


def _q(value: float) -> float:
    return round(float(value), 12)


def _point(value: object, *, field: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in value
        )
    ):
        raise AnalysisError(
            code="INVALID_PHASE1_LAYOUT_INPUT",
            message=f"{field} must be a finite [x, y] micron point.",
            details={"field": field, "value": value},
            next_action="Provide explicit finite placement coordinates.",
        )
    return float(value[0]), float(value[1])


def _safe_cell(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return f"DUT_{normalized}"


def _bbox_intersects(first: Sequence[float], second: Sequence[float]) -> bool:
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def _route_fingerprint(routes: object) -> str:
    return hashlib.sha256(
        json.dumps(routes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _route_boxes(points: Sequence[Sequence[float]], width_um: float) -> list[list[float]]:
    half = width_um / 2.0
    boxes: list[list[float]] = []
    for first, second in zip(points, points[1:]):
        x1, y1 = float(first[0]), float(first[1])
        x2, y2 = float(second[0]), float(second[1])
        if x1 != x2 and y1 != y2:
            raise AnalysisError(
                code="NON_ORTHOGONAL_ROUTING_FORBIDDEN",
                message="A first-metal route segment is not Manhattan.",
                details={"first": list(first), "second": list(second)},
                next_action="Use the verified first-metal routing report unchanged.",
            )
        boxes.append(
            [
                _q(min(x1, x2) - half),
                _q(min(y1, y2) - half),
                _q(max(x1, x2) + half),
                _q(max(y1, y2) + half),
            ]
        )
    return boxes


def _mesh_segment_corridors(
    points: Sequence[Sequence[float]],
    envelope_width_um: float,
) -> list[list[float]]:
    half = envelope_width_um / 2.0
    corridors = []
    for first, second in zip(points, points[1:]):
        x1, y1 = float(first[0]), float(first[1])
        x2, y2 = float(second[0]), float(second[1])
        if x1 == x2 and y1 != y2:
            corridors.append([_q(x1 - half), _q(min(y1, y2) - half), _q(x1 + half), _q(max(y1, y2) + half)])
        elif y1 == y2 and x1 != x2:
            corridors.append([_q(min(x1, x2) - half), _q(y1 - half), _q(max(x1, x2) + half), _q(y1 + half)])
        else:
            raise AnalysisError(
                code="NON_ORTHOGONAL_ROUTING_FORBIDDEN",
                message="Mesh corridors require non-zero Manhattan route segments.",
                details={"first": list(first), "second": list(second)},
                next_action="Regenerate the verified Manhattan route polyline.",
            )
    return corridors


def _add_pad_mesh(
    operations: list[dict[str, Any]],
    *,
    cell: str,
    layer: str,
    pad_width_um: float,
    pad_height_um: float,
    rail_width_um: float,
    rail_space_um: float,
) -> None:
    half_x = pad_width_um / 2.0
    half_y = pad_height_um / 2.0
    rail = rail_width_um
    operations.extend(
        [
            {"type": "add_box", "cell": cell, "layer": layer, "bbox_um": [-half_x, -half_y, half_x, -half_y + rail]},
            {"type": "add_box", "cell": cell, "layer": layer, "bbox_um": [-half_x, half_y - rail, half_x, half_y]},
            {"type": "add_box", "cell": cell, "layer": layer, "bbox_um": [-half_x, -half_y + rail, -half_x + rail, half_y - rail]},
            {"type": "add_box", "cell": cell, "layer": layer, "bbox_um": [half_x - rail, -half_y + rail, half_x, half_y - rail]},
        ]
    )
    pitch = rail + rail_space_um
    max_x = half_x - rail
    max_y = half_y - rail
    index = 0
    while index * pitch + rail / 2.0 <= max(max_x, max_y) + 1e-12:
        offsets = [0.0] if index == 0 else [-index * pitch, index * pitch]
        for offset in offsets:
            if abs(offset) + rail / 2.0 <= max_x + 1e-12:
                operations.append(
                    {
                        "type": "add_box",
                        "cell": cell,
                        "layer": layer,
                        "bbox_um": [_q(offset - rail / 2.0), -half_y, _q(offset + rail / 2.0), half_y],
                    }
                )
            if abs(offset) + rail / 2.0 <= max_y + 1e-12:
                operations.append(
                    {
                        "type": "add_box",
                        "cell": cell,
                        "layer": layer,
                        "bbox_um": [-half_x, _q(offset - rail / 2.0), half_x, _q(offset + rail / 2.0)],
                    }
                )
        index += 1


def compose_phase1_direct_layout(
    *,
    output_layout_path: str,
    top_cell: str,
    process_capability: Mapping[str, Any],
    request_plan: Mapping[str, Any],
    primitive_instances: Iterable[Mapping[str, Any]],
    pad_rail_width_um: float,
) -> dict[str, Any]:
    """Build a validated atomic drawing request; KLayout execution remains a later gate."""

    process = validate_process_capability(process_capability)
    if request_plan.get("planning_status") != "ready_for_geometry":
        raise AnalysisError(
            code="PHASE1_REQUEST_PLAN_NOT_READY",
            message="The direct-measurement request has not closed every planning gate.",
            details={"planning_status": request_plan.get("planning_status")},
            next_action="Resolve questions and obtain a feasible first-metal routing report.",
        )
    request = request_plan.get("request")
    direct = request_plan.get("direct_measurement_contract")
    routing_policy = request_plan.get("routing_policy")
    if not isinstance(request, Mapping) or not isinstance(direct, Mapping) or not isinstance(routing_policy, Mapping):
        raise AnalysisError(
            code="INVALID_PHASE1_REQUEST_PLAN",
            message="The request plan is incomplete.",
            details={},
            next_action="Use the unmodified plan_direct_measurement_teg result.",
        )
    if request.get("measurement_mode") != "direct" or request["pads"].get("topology_status") != "primary_supported_profile":
        raise AnalysisError(
            code="UNSUPPORTED_PHASE1_LAYOUT_TOPOLOGY",
            message="Atomic Phase 1 composition currently requires direct 25-Pad single-row topology.",
            details={"request": dict(request)},
            next_action="Use the primary 25-Pad direct profile or implement a separate topology adapter.",
        )
    if (
        request.get("process_profile") != process["process"]["name"]
        or request.get("process_profile_version") != process["process"]["version"]
    ):
        raise AnalysisError(
            code="REQUEST_PROCESS_CAPABILITY_MISMATCH",
            message="The direct-measurement request and drawing capability name/version differ.",
            details={
                "request_process_profile": request.get("process_profile"),
                "request_process_profile_version": request.get("process_profile_version"),
                "capability_process": process["process"],
            },
            next_action="Replan with the exact process capability name and version used for drawing.",
        )
    frame_width, frame_height = [float(value) for value in request["frame_um"]]
    pad_count = int(request["pads"]["count"])
    pad_width, pad_height = [float(value) for value in request["pads"]["outline_um"]]
    pad_pitch = frame_width / pad_count
    if pad_width >= pad_pitch or pad_height >= frame_height:
        raise AnalysisError(
            code="PAD_TOPOLOGY_DOES_NOT_FIT_FRAME",
            message="The declared Pad outline does not fit the parameterized single-row frame.",
            details={"frame_um": [frame_width, frame_height], "pad_pitch_um": pad_pitch, "pad_outline_um": [pad_width, pad_height]},
            next_action="Increase the frame or reduce the Pad outline/count.",
        )
    pad_centers = {
        pad: [_q((pad - 0.5) * pad_pitch), _q(frame_height / 2.0)]
        for pad in range(1, pad_count + 1)
    }
    pad_boxes = {
        pad: [
            _q(center[0] - pad_width / 2.0),
            _q(center[1] - pad_height / 2.0),
            _q(center[0] + pad_width / 2.0),
            _q(center[1] + pad_height / 2.0),
        ]
        for pad, center in pad_centers.items()
    }

    first_metal_role = process["first_metal_role"]
    first_metal = next(
        metal for metal in process["routing_metals"] if metal["layer_role"] == first_metal_role
    )
    rail = float(pad_rail_width_um)
    if isinstance(pad_rail_width_um, bool) or not math.isfinite(rail) or rail < first_metal["min_width_um"]:
        raise AnalysisError(
            code="INVALID_PAD_RAIL_WIDTH",
            message="Pad rail width is below the first-metal process minimum.",
            details={"pad_rail_width_um": rail, "first_metal": first_metal},
            next_action="Use a process-legal positive Pad rail width.",
        )
    maximum = first_metal.get("profile_max_width_um")
    if maximum is not None and rail > maximum:
        raise AnalysisError(
            code="INVALID_PAD_RAIL_WIDTH",
            message="Pad rail width exceeds the first-metal profile maximum.",
            details={"pad_rail_width_um": rail, "maximum_um": maximum},
            next_action="Reduce Pad rail width or explicitly revise the process profile.",
        )
    pad_rail_space = required_metal_space_um(
        first_metal,
        width_um=rail,
        parallel_length_um=max(pad_width, pad_height),
    )

    instances = list(primitive_instances)
    instance_by_dut: dict[str, dict[str, Any]] = {}
    operations: list[dict[str, Any]] = []
    primitive_boxes: dict[str, list[float]] = {}
    cells = [top_cell, "PAD_MESH"]
    used_layer_roles = {first_metal_role}
    for index, entry in enumerate(instances, start=1):
        dut = entry.get("dut") if isinstance(entry, Mapping) else None
        primitive = entry.get("primitive") if isinstance(entry, Mapping) else None
        if not isinstance(dut, str) or not dut or not isinstance(primitive, Mapping):
            raise AnalysisError(
                code="INVALID_PRIMITIVE_INSTANCE",
                message="Each primitive instance requires dut, primitive, and origin_um.",
                details={"instance_index": index},
                next_action="Provide one verified primitive for each mapped DUT.",
            )
        if dut in instance_by_dut:
            raise AnalysisError(
                code="DUPLICATE_PRIMITIVE_INSTANCE",
                message="A DUT is instantiated more than once.",
                details={"dut": dut},
                next_action="Keep exactly one primitive instance per DUT.",
            )
        origin = _point(entry.get("origin_um"), field=f"primitive_instances[{index}].origin_um")
        if primitive.get("geometry_status") != "process_gated_primitive_not_routed" or not primitive.get("verification"):
            raise AnalysisError(
                code="UNVERIFIED_PRIMITIVE_INSTANCE",
                message="The DUT-local primitive lacks its pure verification report.",
                details={"dut": dut},
                next_action="Use a verified Phase 1 resistor or MOM primitive result.",
            )
        expected_primitive_fingerprint = primitive["verification"].get(
            "geometry_fingerprint_sha256"
        )
        actual_primitive_fingerprint = geometry_fingerprint(
            {"operations": primitive.get("operations")}
        )
        if expected_primitive_fingerprint != actual_primitive_fingerprint:
            raise AnalysisError(
                code="PRIMITIVE_GEOMETRY_FINGERPRINT_MISMATCH",
                message="A primitive changed after its verification report was created.",
                details={
                    "dut": dut,
                    "expected_fingerprint_sha256": expected_primitive_fingerprint,
                    "actual_fingerprint_sha256": actual_primitive_fingerprint,
                },
                next_action="Regenerate and reverify the primitive before composition.",
            )
        actual_terminal_components = terminal_component_manifest(
            primitive["operations"], primitive.get("terminals_um", {}), layer_role=first_metal_role
        )
        if primitive["verification"].get("terminal_components") != actual_terminal_components:
            raise AnalysisError(
                code="PRIMITIVE_TERMINAL_COMPONENT_MISMATCH",
                message="The primitive terminal-to-conductor component manifest is missing or stale.",
                details={"dut": dut},
                next_action="Regenerate and reverify the primitive before composition.",
            )
        if primitive.get("process") != process["process"]:
            raise AnalysisError(
                code="PRIMITIVE_PROCESS_MISMATCH",
                message="Primitive and TEG process identities differ.",
                details={"dut": dut, "primitive_process": primitive.get("process"), "teg_process": process["process"]},
                next_action="Regenerate the primitive from the same process capability.",
            )
        cell = _safe_cell(dut)
        if cell in cells:
            raise AnalysisError(
                code="PRIMITIVE_CELL_NAME_COLLISION",
                message="Sanitized DUT cell names collide.",
                details={"dut": dut, "cell": cell},
                next_action="Use distinct alphanumeric DUT identifiers.",
            )
        cells.append(cell)
        local_boxes: list[list[float]] = []
        for primitive_operation in primitive["operations"]:
            operation = dict(primitive_operation)
            operation["cell"] = cell
            operations.append(operation)
            used_layer_roles.add(operation["layer"])
            bbox = operation["bbox_um"]
            local_boxes.append([float(value) for value in bbox])
        primitive_bbox = [
            _q(origin[0] + min(box[0] for box in local_boxes)),
            _q(origin[1] + min(box[1] for box in local_boxes)),
            _q(origin[0] + max(box[2] for box in local_boxes)),
            _q(origin[1] + max(box[3] for box in local_boxes)),
        ]
        primitive_boxes[dut] = primitive_bbox
        if (
            primitive_bbox[0] < 0.0
            or primitive_bbox[1] < 0.0
            or primitive_bbox[2] > frame_width
            or primitive_bbox[3] > frame_height
        ):
            raise AnalysisError(
                code="PRIMITIVE_OUTSIDE_TEG_FRAME",
                message="A placed DUT primitive extends outside the declared TEG frame.",
                details={"dut": dut, "primitive_bbox_um": primitive_bbox, "frame_um": [frame_width, frame_height]},
                next_action="Move the DUT or enlarge the explicitly approved frame.",
            )
        operations.append(
            {
                "type": "add_instance",
                "parent_cell": top_cell,
                "child_cell": cell,
                "origin_um": [_q(origin[0]), _q(origin[1])],
                "rotation_deg": 0,
                "mirror_x": False,
            }
        )
        instance_by_dut[dut] = {
            "primitive": primitive,
            "origin": origin,
            "cell": cell,
            "terminal_components": actual_terminal_components,
        }

    budget = direct.get("pad_budget")
    if not isinstance(budget, Mapping) or budget.get("status") != "fits" or not budget.get("terminal_contracts_verified"):
        raise AnalysisError(
            code="PHASE1_PAD_BUDGET_NOT_VERIFIED",
            message="The explicit DUT terminal/Pad budget is not verified.",
            details={"pad_budget": budget},
            next_action="Provide exact DUT terminal contracts and assignments.",
        )
    mapped_duts = {assignment["dut"] for assignment in budget["assignments"]}
    if mapped_duts != set(instance_by_dut):
        raise AnalysisError(
            code="PRIMITIVE_DUT_SET_MISMATCH",
            message="Primitive instances do not match the terminal-mapped DUT set.",
            details={"mapped_duts": sorted(mapped_duts), "primitive_duts": sorted(instance_by_dut)},
            next_action="Instantiate exactly one primitive for each mapped DUT.",
        )
    assigned_pads_by_dut: dict[str, set[int]] = {}
    for assignment in budget["assignments"]:
        assigned_pads_by_dut.setdefault(assignment["dut"], set()).add(int(assignment["pad"]))
    for dut, primitive_box in primitive_boxes.items():
        unexpected_pads = [
            pad
            for pad, pad_box in pad_boxes.items()
            if pad not in assigned_pads_by_dut[dut] and _bbox_intersects(primitive_box, pad_box)
        ]
        if unexpected_pads:
            raise AnalysisError(
                code="PRIMITIVE_INTERSECTS_UNASSIGNED_PAD",
                message="A placed DUT primitive intersects a Pad not assigned to that DUT.",
                details={"dut": dut, "intersected_pads": unexpected_pads},
                next_action="Move the DUT or assign only physically intended Pad landings.",
            )
    ordered_duts = sorted(primitive_boxes)
    for first_index, first_dut in enumerate(ordered_duts):
        for second_dut in ordered_duts[first_index + 1 :]:
            if _bbox_intersects(primitive_boxes[first_dut], primitive_boxes[second_dut]):
                raise AnalysisError(
                    code="PRIMITIVE_INSTANCE_OVERLAP",
                    message="Two DUT primitive bounding boxes overlap.",
                    details={
                        "first_dut": first_dut,
                        "first_bbox_um": primitive_boxes[first_dut],
                        "second_dut": second_dut,
                        "second_bbox_um": primitive_boxes[second_dut],
                    },
                    next_action="Separate the DUT placements and regenerate routing.",
                )

    route_report = routing_policy.get("m1_feasibility_report")
    if not isinstance(route_report, Mapping) or route_report.get("status") != "feasible_on_first_metal":
        raise AnalysisError(
            code="PHASE1_M1_ROUTE_NOT_VERIFIED",
            message="A feasible first-metal routing report is required.",
            details={"m1_feasibility_report": route_report},
            next_action="Resolve routing and pass the unmodified feasible report.",
        )
    actual_route_fingerprint = _route_fingerprint(route_report.get("routes"))
    if route_report.get("route_fingerprint_sha256") != actual_route_fingerprint:
        raise AnalysisError(
            code="M1_ROUTE_FINGERPRINT_MISMATCH",
            message="The first-metal routes changed after bounded feasibility verification.",
            details={
                "expected_fingerprint_sha256": route_report.get("route_fingerprint_sha256"),
                "actual_fingerprint_sha256": actual_route_fingerprint,
            },
            next_action="Rerun first-metal feasibility and pass its unmodified report.",
        )
    search_evidence = route_report.get("search_evidence")
    if not isinstance(search_evidence, Mapping):
        raise AnalysisError(
            code="M1_SEARCH_EVIDENCE_REQUIRED",
            message="The first-metal route report lacks its bounded-search evidence.",
            details={},
            next_action="Rerun first-metal feasibility with the current router.",
        )
    actual_search_fingerprint = hashlib.sha256(
        json.dumps(
            search_evidence,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if route_report.get("search_evidence_fingerprint_sha256") != actual_search_fingerprint:
        raise AnalysisError(
            code="M1_SEARCH_EVIDENCE_FINGERPRINT_MISMATCH",
            message="The bounded first-metal search evidence changed after routing.",
            details={
                "expected_fingerprint_sha256": route_report.get(
                    "search_evidence_fingerprint_sha256"
                ),
                "actual_fingerprint_sha256": actual_search_fingerprint,
            },
            next_action="Rerun first-metal feasibility and preserve its complete evidence.",
        )
    routes_by_id = {route["connection_id"]: route for route in route_report["routes"]}
    expected_ids = {f"{assignment['dut']}:{assignment['terminal']}" for assignment in budget["assignments"]}
    if set(routes_by_id) != expected_ids:
        raise AnalysisError(
            code="TERMINAL_ROUTE_SET_MISMATCH",
            message="First-metal route IDs do not exactly match every DUT terminal.",
            details={"missing": sorted(expected_ids.difference(routes_by_id)), "unexpected": sorted(set(routes_by_id).difference(expected_ids))},
            next_action="Use connection_id DUT:terminal for every explicit assignment.",
        )

    terminal_manifest: list[dict[str, Any]] = []
    route_operations: list[dict[str, Any]] = []
    for assignment in budget["assignments"]:
        dut = assignment["dut"]
        terminal = assignment["terminal"]
        pad = int(assignment["pad"])
        route = routes_by_id[f"{dut}:{terminal}"]
        if route["net"] != assignment["net"]:
            raise AnalysisError(
                code="TERMINAL_ROUTE_NET_MISMATCH",
                message="A terminal route net differs from its Pad assignment.",
                details={"dut": dut, "terminal": terminal, "assignment_net": assignment["net"], "route_net": route["net"]},
                next_action="Keep route net and terminal assignment net identical.",
            )
        route_width = float(route["width_um"])
        mesh_rail_width = float(route.get("mesh_rail_width_um", first_metal["min_width_um"]))
        mesh_rail_space = float(
            route.get(
                "mesh_rail_space_um",
                required_metal_space_um(
                    first_metal,
                    width_um=mesh_rail_width,
                    parallel_length_um=max(frame_width, frame_height),
                ),
            )
        )
        minimum_mesh_envelope = 2.0 * mesh_rail_width + mesh_rail_space
        if (
            mesh_rail_width < float(first_metal["min_width_um"])
            or (maximum is not None and mesh_rail_width > float(maximum))
            or route_width + 1e-12 < minimum_mesh_envelope
        ):
            raise AnalysisError(
                code="ROUTE_WIDTH_OUTSIDE_PROCESS_PROFILE",
                message="A route mesh rail or verified envelope is outside the declared process profile.",
                details={
                    "connection_id": route["connection_id"],
                    "mesh_envelope_width_um": route_width,
                    "mesh_rail_width_um": mesh_rail_width,
                    "mesh_rail_space_um": mesh_rail_space,
                    "minimum_mesh_envelope_um": minimum_mesh_envelope,
                    "min_width_um": first_metal["min_width_um"],
                    "profile_max_width_um": maximum,
                },
                next_action="Regenerate routing with a process-legal multi-rail mesh envelope.",
            )
        instance = instance_by_dut[dut]
        local_terminal = instance["primitive"]["terminals_um"].get(terminal)
        if local_terminal is None:
            raise AnalysisError(
                code="PRIMITIVE_TERMINAL_NOT_FOUND",
                message="A mapped terminal is absent from the DUT-local primitive.",
                details={"dut": dut, "terminal": terminal},
                next_action="Align the primitive and DUT terminal contract.",
            )
        terminal_point = [
            _q(instance["origin"][0] + float(local_terminal[0])),
            _q(instance["origin"][1] + float(local_terminal[1])),
        ]
        route_start = [_q(value) for value in route["points_um"][0]]
        route_end = [_q(value) for value in route["points_um"][-1]]
        pad_point = pad_centers[pad]
        if not (
            (route_start == terminal_point and route_end == pad_point)
            or (route_end == terminal_point and route_start == pad_point)
        ):
            raise AnalysisError(
                code="TERMINAL_ROUTE_ENDPOINT_MISMATCH",
                message="A route does not join the exact primitive terminal and assigned Pad center.",
                details={"dut": dut, "terminal": terminal, "terminal_point_um": terminal_point, "pad_point_um": pad_point, "route_points_um": route["points_um"]},
                next_action="Regenerate the route from the placed terminal to its assigned Pad center.",
            )
        boxes = _route_boxes(route["points_um"], route_width)
        mesh = synthesize_mesh_polyline(
            dbu_um=float(process["dbu_um"]),
            points_um=route["points_um"],
            segment_corridors_um=_mesh_segment_corridors(route["points_um"], route_width),
            rail_width_um=mesh_rail_width,
            rail_space_um=mesh_rail_space,
            landing_span_um=float(route.get("landing_span_um", mesh_rail_width)),
            cross_tie_pitch_um=max(
                mesh_rail_width + mesh_rail_space,
                10.0 * (mesh_rail_width + mesh_rail_space),
            ),
            cell=top_cell,
            layer_role=first_metal_role,
        )
        mesh_boxes = [operation["bbox_um"] for operation in mesh["operations"]]
        local_m1_boxes = [
            operation["bbox_um"]
            for operation in instance["primitive"]["operations"]
            if operation["layer"] == first_metal_role
        ]
        component_by_box = instance["terminal_components"]["component_id_by_box_index"]
        touched_components = {
            component_by_box[box_index]
            for box in mesh_boxes
            for box_index, local_box in enumerate(local_m1_boxes)
            if _bbox_intersects(
                box,
                [
                    _q(instance["origin"][0] + float(local_box[0])),
                    _q(instance["origin"][1] + float(local_box[1])),
                    _q(instance["origin"][0] + float(local_box[2])),
                    _q(instance["origin"][1] + float(local_box[3])),
                ],
            )
        }
        expected_component = instance["terminal_components"]["terminal_component_ids"][terminal]
        if expected_component not in touched_components:
            raise AnalysisError(
                code="ROUTE_DOES_NOT_OVERLAP_TERMINAL_COMPONENT",
                message="An external route reaches the terminal coordinate without positive conductor overlap.",
                details={"connection_id": route["connection_id"], "expected_component_id": expected_component},
                next_action="Extend the route into the intended terminal landing by positive area.",
            )
        unexpected_components = sorted(touched_components - {expected_component})
        if unexpected_components:
            raise AnalysisError(
                code="ROUTE_SHORTS_DUT_TERMINAL_COMPONENTS",
                message="An external route touches another DUT-local terminal conductor component.",
                details={
                    "connection_id": route["connection_id"],
                    "expected_component_id": expected_component,
                    "unexpected_component_ids": unexpected_components,
                },
                next_action="Reroute away from the other DUT-local terminal conductors.",
            )
        longest_parallel_segment = max(
            abs(float(second[0]) - float(first[0]))
            + abs(float(second[1]) - float(first[1]))
            for first, second in zip(route["points_um"], route["points_um"][1:])
        )
        required_route_space = required_metal_space_um(
            first_metal,
            width_um=mesh_rail_width,
            parallel_length_um=longest_parallel_segment,
        )
        if float(route["clear_space_um"]) + 1e-12 < required_route_space:
            raise AnalysisError(
                code="ROUTE_CLEAR_SPACE_BELOW_PROCESS_RULE",
                message="A first-metal route used less clear space than the process width/length rule.",
                details={
                    "connection_id": route["connection_id"],
                    "declared_clear_space_um": route["clear_space_um"],
                    "required_clear_space_um": required_route_space,
                    "longest_parallel_segment_um": longest_parallel_segment,
                },
                next_action="Regenerate routing with the process-derived clear space.",
            )
        for other_pad, other_box in pad_boxes.items():
            if other_pad != pad and any(_bbox_intersects(box, other_box) for box in boxes):
                raise AnalysisError(
                    code="ROUTE_INTERSECTS_UNASSIGNED_PAD",
                    message="A route intersects a Pad assigned to another terminal or left unused.",
                    details={"connection_id": route["connection_id"], "assigned_pad": pad, "intersected_pad": other_pad},
                    next_action="Add every non-target Pad as a first-metal obstacle and reroute.",
                )
        for other_dut, other_box in primitive_boxes.items():
            if other_dut != dut and any(_bbox_intersects(box, other_box) for box in boxes):
                raise AnalysisError(
                    code="ROUTE_INTERSECTS_OTHER_DUT",
                    message="A route intersects another DUT-local primitive.",
                    details={"connection_id": route["connection_id"], "other_dut": other_dut},
                    next_action="Add other DUT bboxes as first-metal obstacles and reroute.",
                )
        route_operations.extend(mesh["operations"])
        terminal_manifest.append(
            {
                "dut": dut,
                "terminal": terminal,
                "net": assignment["net"],
                "pad": pad,
                "terminal_point_um": terminal_point,
                "pad_center_um": pad_point,
                "connection_id": route["connection_id"],
                "primitive_terminal_component_id": expected_component,
                "route_touched_component_ids": sorted(touched_components),
                "positive_area_terminal_overlap_verified": True,
                "route_geometry_kind": "connected_multi_segment_low_resistance_mesh",
                "mesh_synthesis_evidence": mesh["evidence"],
            }
        )

    _add_pad_mesh(
        operations,
        cell="PAD_MESH",
        layer=first_metal_role,
        pad_width_um=pad_width,
        pad_height_um=pad_height,
        rail_width_um=rail,
        rail_space_um=pad_rail_space,
    )
    for pad, center in pad_centers.items():
        operations.append(
            {"type": "add_instance", "parent_cell": top_cell, "child_cell": "PAD_MESH", "origin_um": center, "rotation_deg": 0, "mirror_x": False}
        )
    operations.extend(route_operations)

    if "outline" not in process["layers"]:
        raise AnalysisError(
            code="PROCESS_OUTLINE_LAYER_REQUIRED",
            message="Exact frame composition requires an explicit outline layer role.",
            details={"available_layer_roles": sorted(process["layers"])},
            next_action="Map the non-electrical outline layer in the process capability.",
        )
    used_layer_roles.add("outline")
    dbu = float(process["dbu_um"])
    operations.extend(
        [
            {"type": "add_box", "cell": top_cell, "layer": "outline", "bbox_um": [0.0, 0.0, frame_width, dbu]},
            {"type": "add_box", "cell": top_cell, "layer": "outline", "bbox_um": [0.0, frame_height - dbu, frame_width, frame_height]},
            {"type": "add_box", "cell": top_cell, "layer": "outline", "bbox_um": [0.0, dbu, dbu, frame_height - dbu]},
            {"type": "add_box", "cell": top_cell, "layer": "outline", "bbox_um": [frame_width - dbu, dbu, frame_width, frame_height - dbu]},
        ]
    )
    layers = [
        {"name": role, "layer": process["layers"][role][0], "datatype": process["layers"][role][1]}
        for role in sorted(used_layer_roles)
    ]
    drawing_request = {
        "output_layout_path": str(Path(output_layout_path).expanduser().resolve()),
        "dbu_um": dbu,
        "top_cell": top_cell,
        "cells": cells,
        "layers": layers,
        "operations": operations,
    }
    validated_plan = build_manhattan_drawing_plan(**drawing_request)
    semantic_plan = {
        key: value for key, value in validated_plan.items() if key != "output_layout_path"
    }
    fingerprint = hashlib.sha256(
        json.dumps(semantic_plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "contract_version": 1,
        "production_ready": False,
        "ready_for_klayout_generation": True,
        "fresh_reload_verified": False,
        "process": process["process"],
        "frame_um": [frame_width, frame_height],
        "pad_count": pad_count,
        "pad_rail_width_um": rail,
        "pad_rail_clear_space_um": pad_rail_space,
        "pad_centers_um": {str(pad): center for pad, center in pad_centers.items()},
        "primitive_duts": sorted(instance_by_dut),
        "terminal_routes": terminal_manifest,
        "connectivity_projection": {
            "terminal_count": len(terminal_manifest),
            "route_set_exact": True,
            "different_net_route_spacing_verified": True,
            "same_net_route_connectivity_verified": all(
                count == 1 for count in route_report.get("net_component_counts", {}).values()
            ),
            "primitive_terminal_component_overlap_verified": True,
            "fresh_reload_geometry_equivalence_required": True,
        },
        "first_metal_route_fingerprint_sha256": route_report.get("route_fingerprint_sha256"),
        "dut_to_pad_mesh_compiler_integrated": True,
        "single_rail_route_fallback_allowed": False,
        "first_metal_search_evidence_fingerprint_sha256": route_report.get(
            "search_evidence_fingerprint_sha256"
        ),
        "drawing_plan_fingerprint_sha256": fingerprint,
        "drawing_request": drawing_request,
        "next_gate": "call draw_manhattan_layout then fresh-reload inspect/connectivity/DRC/XOR verification",
    }
