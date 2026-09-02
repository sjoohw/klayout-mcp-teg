"""Company-level terminal and measurement conventions, independent of a PDK."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import AnalysisError


REFERENCE_ORGANIZATION_PRESET: dict[str, Any] = {
    "schema_version": 1,
    "name": "generic_direct_measurement_reference",
    "approval_status": "reference_only",
    "terminal_order_by_family_and_measurement": {
        "transistor": {"dc_4t": ["G", "D", "S", "B"]},
        "resistor": {
            "direct_2t": ["F+", "F-"],
            "kelvin_4t": ["F+", "F-", "S+", "S-"],
        },
        "capacitor": {"capacitance_2t": ["P", "N"]},
    },
    "project_routing_defaults": {
        "max_width_um": None,
    },
    "verification_environment": {
        "drc": "available",
        "lvs": "available",
        "pex": "available",
    },
    "transistor_context_defaults": {
        "fill_dut_window": True,
        "measured_device_selection": "balanced_central_region",
        "default_measured_device_count": 1,
        "measurement_edge_inset_um": 5.0,
        "surrounding_device_routing": "none",
        "diffusion_sharing": "compatible_neighbors",
        "default_fill_style": "same_as_measured",
        "allowed_fill_styles": ["same_as_measured", "standard_cell_like"],
        "standard_cell_sequence": ["nmos", "pmos", "pmos", "nmos"],
        "sequence_axis": "x",
        "standard_cell_height_required": True,
    },
}


def validate_organization_preset(preset: Mapping[str, Any]) -> dict[str, Any]:
    if preset.get("schema_version") != 1:
        raise AnalysisError(
            code="UNSUPPORTED_ORGANIZATION_PRESET_SCHEMA",
            message="Organization preset schema_version must be 1.",
            details={"schema_version": preset.get("schema_version")},
            next_action="Use schema_version: 1.",
        )
    name = preset.get("name")
    status = preset.get("approval_status")
    if not isinstance(name, str) or not name.strip() or status not in {
        "reference_only",
        "company_approved",
    }:
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="Preset name or approval_status is invalid.",
            details={"name": name, "approval_status": status},
            next_action="Provide a name and reference_only or company_approved status.",
        )
    conventions = preset.get("terminal_order_by_family_and_measurement")
    if not isinstance(conventions, Mapping) or not conventions:
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="At least one family/measurement terminal convention is required.",
            details={},
            next_action="Map each fixed measurement mode to its ordered terminal names.",
        )
    normalized: dict[str, dict[str, list[str]]] = {}
    for family, measurements in conventions.items():
        if not isinstance(family, str) or not family or not isinstance(measurements, Mapping):
            raise AnalysisError(
                code="INVALID_ORGANIZATION_PRESET",
                message="Family conventions must be named measurement maps.",
                details={"family": family},
                next_action="Use family -> measurement -> ordered terminal list.",
            )
        normalized_measurements: dict[str, list[str]] = {}
        for measurement, terminals in measurements.items():
            if (
                not isinstance(measurement, str)
                or not measurement
                or not isinstance(terminals, list)
                or not terminals
                or any(not isinstance(item, str) or not item.strip() for item in terminals)
                or len(terminals) != len(set(terminals))
            ):
                raise AnalysisError(
                    code="INVALID_ORGANIZATION_PRESET",
                    message="Every measurement needs a non-empty ordered unique terminal list.",
                    details={"family": family, "measurement": measurement, "terminals": terminals},
                    next_action="Correct the company terminal convention.",
                )
            normalized_measurements[measurement] = [item.strip() for item in terminals]
        normalized[family] = normalized_measurements
    routing = preset.get("project_routing_defaults", {})
    if not isinstance(routing, Mapping):
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="project_routing_defaults must be an object.",
            details={"project_routing_defaults": routing},
            next_action="Use an object or omit project routing defaults.",
        )
    maximum = routing.get("max_width_um")
    if maximum is not None and (
        isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or not math.isfinite(float(maximum))
        or float(maximum) <= 0
    ):
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="Project max width must be a finite positive micron value or null.",
            details={"max_width_um": maximum},
            next_action="Remove max_width_um or provide the project routing limit.",
        )
    verification = preset.get("verification_environment")
    expected_engines = {"drc", "lvs", "pex"}
    if (
        not isinstance(verification, Mapping)
        or set(verification) != expected_engines
        or any(value != "available" for value in verification.values())
    ):
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="Company verification environment must declare DRC/LVS/PEX as available.",
            details={"verification_environment": verification},
            next_action="Set drc, lvs, and pex to available; track new-device coverage per job.",
        )
    context = preset.get("transistor_context_defaults")
    expected_context = {
        "fill_dut_window": True,
        "measured_device_selection": "balanced_central_region",
        "default_measured_device_count": 1,
        "measurement_edge_inset_um": 5.0,
        "surrounding_device_routing": "none",
        "diffusion_sharing": "compatible_neighbors",
        "default_fill_style": "same_as_measured",
        "allowed_fill_styles": ["same_as_measured", "standard_cell_like"],
        "standard_cell_sequence": ["nmos", "pmos", "pmos", "nmos"],
        "sequence_axis": "x",
        "standard_cell_height_required": True,
    }
    if context != expected_context:
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="Transistor context defaults do not match the supported schema-v1 policy.",
            details={"transistor_context_defaults": context},
            next_action=(
                "Use balanced central selection, unrouted surroundings, compatible diffusion sharing, "
                "and the documented same-device/standard-cell fill styles."
            ),
        )
    return {
        "ok": True,
        "schema_version": 1,
        "name": name.strip(),
        "approval_status": status,
        "terminal_order_by_family_and_measurement": normalized,
        "project_routing_defaults": {
            "max_width_um": None if maximum is None else float(maximum)
        },
        "verification_environment": dict(verification),
        "transistor_context_defaults": dict(context),
        "pdk_independent": True,
        "device_coverage_policy": {
            "allowed_states": ["covered", "pending", "unavailable"],
            "default_for_established_device": "covered",
            "default_for_new_device": "pending",
            "coverage_is_pdk_availability": False,
        },
    }


def load_organization_preset(preset_path: str | None = None) -> dict[str, Any]:
    if preset_path is None:
        return validate_organization_preset(REFERENCE_ORGANIZATION_PRESET)
    path = Path(preset_path).expanduser().resolve()
    if not path.is_file():
        raise AnalysisError(
            code="ORGANIZATION_PRESET_NOT_FOUND",
            message="Organization preset YAML does not exist.",
            details={"preset_path": str(path)},
            next_action="Provide an existing company preset YAML path.",
        )
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET_YAML",
            message="Organization preset YAML could not be read.",
            details={"preset_path": str(path), "error_type": type(exc).__name__},
            next_action="Correct the UTF-8 YAML and retry.",
        ) from exc
    if not isinstance(loaded, Mapping):
        raise AnalysisError(
            code="INVALID_ORGANIZATION_PRESET",
            message="Organization preset YAML root must be an object.",
            details={"preset_path": str(path)},
            next_action="Use the documented organization preset structure.",
        )
    return {**validate_organization_preset(loaded), "preset_path": str(path)}
