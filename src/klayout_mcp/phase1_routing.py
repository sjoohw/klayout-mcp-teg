"""Pad-aware endpoint and keepout synthesis for Phase 1 direct M1 routing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any, Iterable, Mapping

from .errors import AnalysisError
from .process_capability import required_metal_space_um, validate_process_capability
from .routing_feasibility import analyze_first_metal_feasibility


def _point(value: object, *, field: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in value)
    ):
        raise AnalysisError(
            code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
            message=f"{field} must be a finite [x, y] micron point.",
            details={"field": field, "value": value},
            next_action="Provide an explicit placement point.",
        )
    return float(value[0]), float(value[1])


def _positive(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise AnalysisError(
            code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
            message=f"{field} must be finite and positive.",
            details={"field": field, "value": value},
            next_action="Provide a positive process-legal value.",
        )
    return float(value)


def _grid(value: float, dbu: float, *, field: str) -> None:
    try:
        units = Decimal(str(value)) / Decimal(str(dbu))
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise AnalysisError(
            code="PHASE1_ROUTE_OFF_GRID",
            message="A route-planning coordinate cannot be represented on the process grid.",
            details={"field": field, "value_um": value, "dbu_um": dbu},
            next_action="Use exact integer-DBU coordinates.",
        ) from exc
    if units != units.to_integral_value():
        raise AnalysisError(
            code="PHASE1_ROUTE_OFF_GRID",
            message="A route-planning coordinate cannot be represented on the process grid.",
            details={"field": field, "value_um": value, "dbu_um": dbu},
            next_action="Use exact integer-DBU coordinates.",
        )


def plan_phase1_terminal_routes(
    *,
    process_capability: Mapping[str, Any],
    primitive_instances: Iterable[Mapping[str, Any]],
    terminal_assignments: Iterable[Mapping[str, Any]],
    route_specs: Iterable[Mapping[str, Any]],
    frame_width_um: float = 2000.0,
    frame_height_um: float = 54.0,
    pad_count: int = 25,
    pad_width_um: float = 40.0,
    pad_height_um: float = 40.0,
    extra_obstacles_um: Iterable[list[float]] = (),
) -> dict[str, Any]:
    """Derive exact terminal/Pad endpoints and per-route non-target keepouts."""

    process = validate_process_capability(process_capability)
    frame_width = _positive(frame_width_um, field="frame_width_um")
    frame_height = _positive(frame_height_um, field="frame_height_um")
    pad_width = _positive(pad_width_um, field="pad_width_um")
    pad_height = _positive(pad_height_um, field="pad_height_um")
    dbu = float(process["dbu_um"])
    if isinstance(pad_count, bool) or not isinstance(pad_count, int) or pad_count <= 0:
        raise AnalysisError(
            code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
            message="pad_count must be a positive integer.",
            details={"pad_count": pad_count},
            next_action="Use the confirmed Pad count.",
        )
    pitch = frame_width / pad_count
    if pad_width >= pitch or pad_height >= frame_height:
        raise AnalysisError(
            code="PAD_TOPOLOGY_DOES_NOT_FIT_FRAME",
            message="The declared single-row Pads do not fit the routing frame.",
            details={"frame_um": [frame_width, frame_height], "pad_pitch_um": pitch, "pad_outline_um": [pad_width, pad_height]},
            next_action="Increase the frame or reduce the Pad outline/count.",
        )
    for field, value in (
        ("frame_width_um", frame_width),
        ("frame_height_um", frame_height),
        ("pad_width_um", pad_width),
        ("pad_height_um", pad_height),
        ("pad_pitch_um", pitch),
        ("pad_center_y_um", frame_height / 2.0),
    ):
        _grid(value, dbu, field=field)
    pad_centers = {
        pad: ((pad - 0.5) * pitch, frame_height / 2.0)
        for pad in range(1, pad_count + 1)
    }
    pad_boxes = {
        pad: [center[0] - pad_width / 2.0, center[1] - pad_height / 2.0, center[0] + pad_width / 2.0, center[1] + pad_height / 2.0]
        for pad, center in pad_centers.items()
    }

    first_metal_role = process["first_metal_role"]
    first_metal = next(metal for metal in process["routing_metals"] if metal["layer_role"] == first_metal_role)
    instances: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(primitive_instances):
        dut = entry.get("dut") if isinstance(entry, Mapping) else None
        primitive = entry.get("primitive") if isinstance(entry, Mapping) else None
        if not isinstance(dut, str) or not dut or dut in instances or not isinstance(primitive, Mapping):
            raise AnalysisError(
                code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
                message="Primitive instances require unique dut, primitive, and origin_um.",
                details={"instance_index": index, "dut": dut},
                next_action="Provide one process-matched primitive placement per DUT.",
            )
        if primitive.get("process") != process["process"] or not primitive.get("verification"):
            raise AnalysisError(
                code="PHASE1_ROUTE_PRIMITIVE_PROCESS_MISMATCH",
                message="A route-planning primitive is unverified or belongs to another process.",
                details={"dut": dut, "primitive_process": primitive.get("process")},
                next_action="Regenerate the primitive from the selected capability.",
            )
        origin = _point(entry.get("origin_um"), field=f"primitive_instances[{index}].origin_um")
        _grid(origin[0], dbu, field=f"primitive_instances[{index}].origin_um[0]")
        _grid(origin[1], dbu, field=f"primitive_instances[{index}].origin_um[1]")
        terminals = primitive.get("terminals_um")
        if not isinstance(terminals, Mapping) or not terminals:
            raise AnalysisError(
                code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
                message="A primitive has no terminal point manifest.",
                details={"dut": dut},
                next_action="Use a verified Phase 1 primitive.",
            )
        m1_boxes = [
            [origin[0] + float(box[0]), origin[1] + float(box[1]), origin[0] + float(box[2]), origin[1] + float(box[3])]
            for operation in primitive.get("operations", [])
            if operation.get("layer") == first_metal_role
            for box in [operation["bbox_um"]]
        ]
        instances[dut] = {"primitive": primitive, "origin": origin, "m1_boxes": m1_boxes}

    assignments = list(terminal_assignments)
    specs = list(route_specs)
    specs_by_id = {
        str(spec.get("connection_id")): spec
        for spec in specs
        if isinstance(spec, Mapping) and spec.get("connection_id") is not None
    }
    expected_ids = {
        f"{assignment.get('dut')}:{assignment.get('terminal')}"
        for assignment in assignments
        if isinstance(assignment, Mapping)
    }
    if len(specs_by_id) != len(specs) or set(specs_by_id) != expected_ids:
        raise AnalysisError(
            code="PHASE1_ROUTE_SPEC_SET_MISMATCH",
            message="Route specs must exactly match every DUT:terminal assignment.",
            details={"expected_connection_ids": sorted(expected_ids), "provided_connection_ids": sorted(specs_by_id)},
            next_action="Provide one unique width/space route spec for every assigned terminal.",
        )

    connections: list[dict[str, Any]] = []
    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, Mapping):
            raise AnalysisError(
                code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
                message="Terminal assignments must be objects.",
                details={"assignment_index": index},
                next_action="Provide explicit dut, terminal, net, and pad fields.",
            )
        dut = assignment.get("dut")
        terminal = assignment.get("terminal")
        net = assignment.get("net")
        pad = assignment.get("pad")
        if dut not in instances or not isinstance(terminal, str) or not isinstance(net, str) or not net or isinstance(pad, bool) or not isinstance(pad, int) or pad not in pad_centers:
            raise AnalysisError(
                code="INVALID_PHASE1_ROUTE_PLANNING_INPUT",
                message="A terminal assignment references an unknown DUT, terminal, net, or Pad.",
                details={"assignment_index": index, "assignment": dict(assignment)},
                next_action="Align assignments with placed primitive terminals and valid Pads.",
            )
        local_terminal = instances[dut]["primitive"]["terminals_um"].get(terminal)
        if local_terminal is None:
            raise AnalysisError(
                code="PHASE1_ROUTE_TERMINAL_NOT_FOUND",
                message="An assigned terminal is absent from its placed primitive.",
                details={"dut": dut, "terminal": terminal},
                next_action="Correct the assignment or primitive terminal contract.",
            )
        connection_id = f"{dut}:{terminal}"
        spec = specs_by_id[connection_id]
        route_width = _positive(spec.get("width_um"), field=f"route_specs[{connection_id}].width_um")
        clear_space = _positive(spec.get("clear_space_um"), field=f"route_specs[{connection_id}].clear_space_um")
        _grid(route_width / 2.0, dbu, field=f"route_specs[{connection_id}].half_width_um")
        _grid(clear_space, dbu, field=f"route_specs[{connection_id}].clear_space_um")
        maximum = first_metal.get("profile_max_width_um")
        if route_width < first_metal["min_width_um"] or (maximum is not None and route_width > maximum):
            raise AnalysisError(
                code="ROUTE_WIDTH_OUTSIDE_PROCESS_PROFILE",
                message="A route spec width is outside the process profile.",
                details={"connection_id": connection_id, "width_um": route_width, "first_metal": first_metal},
                next_action="Use a process-legal route width.",
            )
        required_space = required_metal_space_um(
            first_metal,
            width_um=route_width,
            parallel_length_um=max(frame_width, frame_height),
        )
        if clear_space + 1e-12 < required_space:
            raise AnalysisError(
                code="ROUTE_CLEAR_SPACE_BELOW_PROCESS_RULE",
                message="A route spec clear space is below the process projection.",
                details={"connection_id": connection_id, "clear_space_um": clear_space, "required_clear_space_um": required_space},
                next_action="Increase the declared route clear space.",
            )
        origin = instances[dut]["origin"]
        start = [origin[0] + float(local_terminal[0]), origin[1] + float(local_terminal[1])]
        _grid(start[0], dbu, field=f"connections[{connection_id}].start_um[0]")
        _grid(start[1], dbu, field=f"connections[{connection_id}].start_um[1]")
        obstacles = [box for other_pad, box in pad_boxes.items() if other_pad != pad]
        obstacles.extend(
            box
            for other_dut, instance in instances.items()
            if other_dut != dut
            for box in instance["m1_boxes"]
        )
        connection = {
            "connection_id": connection_id,
            "net": net,
            "start_um": start,
            "end_um": list(pad_centers[pad]),
            "width_um": route_width,
            "clear_space_um": clear_space,
            "obstacles_um": obstacles,
        }
        if spec.get("preferred_waypoints_um") is not None:
            connection["preferred_waypoints_um"] = spec["preferred_waypoints_um"]
            for waypoint_index, waypoint in enumerate(spec["preferred_waypoints_um"]):
                point = _point(
                    waypoint,
                    field=f"route_specs[{connection_id}].preferred_waypoints_um[{waypoint_index}]",
                )
                _grid(point[0], dbu, field=f"route_specs[{connection_id}].preferred_waypoints_um[{waypoint_index}][0]")
                _grid(point[1], dbu, field=f"route_specs[{connection_id}].preferred_waypoints_um[{waypoint_index}][1]")
        connections.append(connection)

    report = analyze_first_metal_feasibility(
        connections,
        boundary_um=[0.0, 0.0, frame_width, frame_height],
        obstacles_um=extra_obstacles_um,
    )
    return {
        "ok": True,
        "contract_version": 1,
        "process": process["process"],
        "frame_um": [frame_width, frame_height],
        "pad_count": pad_count,
        "pad_centers_um": {str(pad): list(center) for pad, center in pad_centers.items()},
        "routing_connections": connections,
        "m1_feasibility_report": report,
        "ready_for_direct_measurement_planner": report["feasible"] is True,
        "failure_proves_m1_impossible": False,
        "routing_layer_roles_used": [first_metal_role] if report["feasible"] else [],
        "additional_metals_generated": [],
        "layer_escalation_performed": False,
    }
