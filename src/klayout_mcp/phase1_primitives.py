"""Process-gated Manhattan resistor and MOM-capacitor drawing primitives."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any, Mapping

from .design_contract import TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
from .errors import AnalysisError
from .process_capability import required_metal_space_um, validate_process_capability
from .primitive_verification import (
    terminal_component_manifest,
    verify_single_conductor_primitive,
    verify_two_net_primitive,
)


def _positive(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise AnalysisError(
            code="INVALID_PHASE1_PRIMITIVE_PARAMETER",
            message=f"{field} must be a finite positive micron value.",
            details={"field": field, "value": value},
            next_action="Provide a positive process-legal value.",
        )
    return float(value)


def _grid(value: float, dbu: float, *, field: str) -> None:
    try:
        units = Decimal(str(value)) / Decimal(str(dbu))
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise AnalysisError(
            code="PHASE1_PRIMITIVE_OFF_GRID",
            message="Primitive coordinate cannot be represented on the process grid.",
            details={"field": field, "value_um": value, "dbu_um": dbu},
            next_action="Use an exact integer multiple of the process DBU.",
        ) from exc
    if units != units.to_integral_value():
        raise AnalysisError(
            code="PHASE1_PRIMITIVE_OFF_GRID",
            message="Primitive coordinate cannot be represented on the process grid.",
            details={"field": field, "value_um": value, "dbu_um": dbu},
            next_action="Use an exact integer multiple of the process DBU.",
        )


def _q(value: float) -> float:
    """Remove binary-float noise after exact Decimal DBU validation."""

    return round(value, 12)


def _device(
    capability: Mapping[str, Any], *, device_name: str, family: str, measurement: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    validated = validate_process_capability(capability)
    device = validated["devices"].get(device_name)
    if device is None:
        raise AnalysisError(
            code="PROCESS_DEVICE_NOT_AVAILABLE",
            message="The process capability does not contain the requested device.",
            details={"device_name": device_name, "available_devices": sorted(validated["devices"])},
            next_action="Use a declared device or add an evidence-backed process capability.",
        )
    if device["family"] != family or measurement not in device["measurements"]:
        raise AnalysisError(
            code="PROCESS_DEVICE_MEASUREMENT_MISMATCH",
            message="The requested family/measurement is not enabled for this process device.",
            details={
                "device_name": device_name,
                "requested_family": family,
                "requested_measurement": measurement,
                "device_capability": device,
            },
            next_action="Choose a process-supported device measurement contract.",
        )
    return validated, device


def _metal(validated: Mapping[str, Any], layer_role: str) -> dict[str, Any]:
    for metal in validated["routing_metals"]:
        if metal["layer_role"] == layer_role:
            return metal
    raise AnalysisError(
        code="PROCESS_ROUTING_METAL_NOT_AVAILABLE",
        message="The requested primitive layer is not a declared routing metal.",
        details={"layer_role": layer_role},
        next_action="Choose an explicit routing metal from the process capability.",
    )


def plan_metal_resistor_primitive(
    *,
    process_capability: Mapping[str, Any],
    device_name: str,
    layer_role: str,
    measurement: str,
    width_um: float,
    length_um: float,
    terminal_size_um: float,
    dimension_semantics: str,
) -> dict[str, Any]:
    """Plan a horizontal 2T or Kelvin-4T metal-line primitive without external routes."""

    if dimension_semantics != TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH:
        raise AnalysisError(
            code="DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED",
            message="Metal resistor width/length axes must be explicitly confirmed.",
            details={"required": TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH},
            next_action="Confirm width transverse and length longitudinal to current flow.",
        )
    if measurement not in {"direct_2t", "kelvin_4t"}:
        raise AnalysisError(
            code="UNSUPPORTED_RESISTOR_MEASUREMENT",
            message="Metal resistor primitive supports direct_2t or kelvin_4t.",
            details={"measurement": measurement},
            next_action="Choose direct_2t or kelvin_4t.",
        )
    validated, device = _device(
        process_capability, device_name=device_name, family="resistor", measurement=measurement
    )
    if layer_role not in device["required_layers"]:
        raise AnalysisError(
            code="PROCESS_DEVICE_LAYER_MISMATCH",
            message="The resistor device does not declare the selected layer role.",
            details={"layer_role": layer_role, "required_layers": device["required_layers"]},
            next_action="Use a device-declared layer role.",
        )
    metal = _metal(validated, layer_role)
    width = _positive(width_um, field="width_um")
    length = _positive(length_um, field="length_um")
    terminal = _positive(terminal_size_um, field="terminal_size_um")
    if width < metal["min_width_um"] or terminal < metal["min_width_um"]:
        raise AnalysisError(
            code="PHASE1_PRIMITIVE_BELOW_PROCESS_MINIMUM",
            message="Resistor or terminal width is below the declared process minimum.",
            details={"width_um": width, "terminal_size_um": terminal, "metal": metal},
            next_action="Increase the geometry or correct the process capability.",
        )
    maximum = metal.get("profile_max_width_um")
    if maximum is not None and (width > maximum or terminal > maximum):
        raise AnalysisError(
            code="PHASE1_PRIMITIVE_ABOVE_PROFILE_MAXIMUM",
            message="Resistor or terminal width exceeds the profile maximum.",
            details={"width_um": width, "terminal_size_um": terminal, "maximum_um": maximum},
            next_action="Reduce the geometry or explicitly revise the profile maximum.",
        )
    dbu = validated["dbu_um"]
    for field, value in (
        ("width_um", width),
        ("length_um", length),
        ("terminal_size_um", terminal),
        ("half_width_um", width / 2.0),
        ("half_length_um", length / 2.0),
        ("half_terminal_um", terminal / 2.0),
    ):
        _grid(value, dbu, field=field)

    half_w = width / 2.0
    half_l = length / 2.0
    half_t = terminal / 2.0
    left = [_q(-half_l - terminal), _q(-half_t), _q(-half_l), _q(half_t)]
    right = [_q(half_l), _q(-half_t), _q(half_l + terminal), _q(half_t)]
    operations = [
        {"type": "add_box", "cell": "DUT", "layer": layer_role, "bbox_um": [_q(-half_l), _q(-half_w), _q(half_l), _q(half_w)]},
        {"type": "add_box", "cell": "DUT", "layer": layer_role, "bbox_um": left},
        {"type": "add_box", "cell": "DUT", "layer": layer_role, "bbox_um": right},
        {
            "type": "add_box",
            "cell": "DUT",
            "layer": layer_role,
            "bbox_um": [_q(-half_l - dbu), _q(-half_w), _q(-half_l + dbu), _q(half_w)],
        },
        {
            "type": "add_box",
            "cell": "DUT",
            "layer": layer_role,
            "bbox_um": [_q(half_l - dbu), _q(-half_w), _q(half_l + dbu), _q(half_w)],
        },
    ]
    terminals = (
        {"P": [half_l + half_t, 0.0], "N": [-half_l - half_t, 0.0]}
        if measurement == "direct_2t"
        else {
            "F+": [half_l + half_t, 0.0],
            "S+": [half_l, half_t],
            "F-": [-half_l - half_t, 0.0],
            "S-": [-half_l, half_t],
        }
    )
    verification = verify_single_conductor_primitive(operations)
    verification["terminal_components"] = terminal_component_manifest(
        operations, terminals, layer_role=layer_role
    )
    return {
        "ok": True,
        "geometry_status": "process_gated_primitive_not_routed",
        "production_ready": False,
        "process": validated["process"],
        "device": {"name": device_name, "family": "resistor", "measurement": measurement},
        "dbu_um": dbu,
        "cells": ["DUT"],
        "layers": [
            {
                "name": layer_role,
                "layer": validated["layers"][layer_role][0],
                "datatype": validated["layers"][layer_role][1],
            }
        ],
        "operations": operations,
        "terminals_um": terminals,
        "measured_body_bbox_um": [_q(-half_l), _q(-half_w), _q(half_l), _q(half_w)],
        "measured_length_excludes_terminal_landings": True,
        "terminal_junction_positive_overlap_um": dbu,
        "external_routing_included": False,
        "verification": verification,
    }


def plan_mom_capacitor_primitive(
    *,
    process_capability: Mapping[str, Any],
    device_name: str,
    layer_role: str,
    finger_width_um: float,
    finger_space_um: float,
    finger_length_um: float,
    finger_count: int,
    bus_width_um: float,
) -> dict[str, Any]:
    """Plan a single-metal two-net interdigitated MOM primitive."""

    validated, device = _device(
        process_capability,
        device_name=device_name,
        family="capacitor",
        measurement="capacitance_2t",
    )
    if layer_role not in device["required_layers"]:
        raise AnalysisError(
            code="PROCESS_DEVICE_LAYER_MISMATCH",
            message="The capacitor device does not declare the selected layer role.",
            details={"layer_role": layer_role, "required_layers": device["required_layers"]},
            next_action="Use a device-declared layer role.",
        )
    metal = _metal(validated, layer_role)
    finger_width = _positive(finger_width_um, field="finger_width_um")
    finger_space = _positive(finger_space_um, field="finger_space_um")
    finger_length = _positive(finger_length_um, field="finger_length_um")
    bus_width = _positive(bus_width_um, field="bus_width_um")
    if isinstance(finger_count, bool) or not isinstance(finger_count, int) or finger_count < 2:
        raise AnalysisError(
            code="INVALID_MOM_FINGER_COUNT",
            message="MOM capacitor requires at least two integer fingers.",
            details={"finger_count": finger_count},
            next_action="Use an integer finger_count of two or more.",
        )
    required_finger_space = required_metal_space_um(
        metal,
        width_um=finger_width,
        parallel_length_um=finger_length - finger_space,
    )
    if min(finger_width, bus_width) < metal["min_width_um"] or finger_space < required_finger_space:
        raise AnalysisError(
            code="PHASE1_PRIMITIVE_BELOW_PROCESS_MINIMUM",
            message="MOM width or space is below the declared process minimum.",
            details={
                "finger_width_um": finger_width,
                "finger_space_um": finger_space,
                "bus_width_um": bus_width,
                "metal": metal,
                "required_finger_space_um": required_finger_space,
            },
            next_action="Increase MOM width/space or correct the process capability.",
        )
    maximum = metal.get("profile_max_width_um")
    if maximum is not None and max(finger_width, bus_width) > maximum:
        raise AnalysisError(
            code="PHASE1_PRIMITIVE_ABOVE_PROFILE_MAXIMUM",
            message="MOM conductor width exceeds the profile maximum.",
            details={"maximum_um": maximum},
            next_action="Reduce the conductor or explicitly revise the profile maximum.",
        )
    if finger_length <= finger_space:
        raise AnalysisError(
            code="INVALID_MOM_FINGER_LENGTH",
            message="MOM finger length must exceed tip spacing to create coupled overlap.",
            details={"finger_length_um": finger_length, "finger_space_um": finger_space},
            next_action="Increase finger_length_um.",
        )
    dbu = validated["dbu_um"]
    pitch = finger_width + finger_space
    total_height = finger_width + (finger_count - 1) * pitch
    total_width = 2.0 * bus_width + finger_length + finger_space
    for field, value in (
        ("finger_width_um", finger_width),
        ("finger_space_um", finger_space),
        ("finger_length_um", finger_length),
        ("bus_width_um", bus_width),
        ("pitch_um", pitch),
        ("total_height_um", total_height),
        ("total_width_um", total_width),
    ):
        _grid(value, dbu, field=field)

    left_bus = [0.0, 0.0, _q(bus_width), _q(total_height)]
    right_inner = _q(bus_width + finger_length + finger_space)
    right_bus = [right_inner, 0.0, _q(right_inner + bus_width), _q(total_height)]
    operations = [
        {"type": "add_box", "cell": "DUT", "layer": layer_role, "bbox_um": left_bus, "net": "P"},
        {"type": "add_box", "cell": "DUT", "layer": layer_role, "bbox_um": right_bus, "net": "N"},
    ]
    for index in range(finger_count):
        y1 = _q(index * pitch)
        y2 = _q(y1 + finger_width)
        if index % 2 == 0:
            bbox = [_q(bus_width - finger_width), y1, _q(bus_width + finger_length), y2]
            net = "P"
        else:
            bbox = [_q(bus_width + finger_space), y1, _q(right_inner + finger_width), y2]
            net = "N"
        operations.append(
            {"type": "add_box", "cell": "DUT", "layer": layer_role, "bbox_um": bbox, "net": net}
        )
    verification = verify_two_net_primitive(
        operations,
        required_clear_space_um=required_finger_space,
    )
    terminals = {
        "P": [0.0, _q(total_height / 2.0)],
        "N": [_q(total_width), _q(total_height / 2.0)],
    }
    verification["terminal_components"] = terminal_component_manifest(
        operations, terminals, layer_role=layer_role
    )
    return {
        "ok": True,
        "geometry_status": "process_gated_primitive_not_routed",
        "production_ready": False,
        "process": validated["process"],
        "device": {"name": device_name, "family": "capacitor", "measurement": "capacitance_2t"},
        "dbu_um": dbu,
        "cells": ["DUT"],
        "layers": [
            {
                "name": layer_role,
                "layer": validated["layers"][layer_role][0],
                "datatype": validated["layers"][layer_role][1],
            }
        ],
        "operations": operations,
        "terminals_um": terminals,
        "bbox_um": [0.0, 0.0, _q(total_width), _q(total_height)],
        "finger_pitch_um": _q(pitch),
        "required_finger_space_um": required_finger_space,
        "adjacent_finger_overlap_length_um": _q(finger_length - finger_space),
        "finger_to_bus_positive_overlap_um": finger_width,
        "external_routing_included": False,
        "capacitance_value_claimed": False,
        "verification": verification,
    }
