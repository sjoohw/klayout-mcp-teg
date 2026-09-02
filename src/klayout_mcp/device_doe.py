"""Process-gated Phase 1 device DOE planning without geometry inference."""

from __future__ import annotations

from itertools import product
import math
from typing import Any, Iterable, Mapping

from .errors import AnalysisError


PHASE1_AXIS_CATALOG: dict[str, dict[str, str]] = {
    "transistor": {
        "w_um": "electrical channel width",
        "l_um": "drawn gate length",
        "sa_um": "source-side gate-to-active-edge distance for LOD/stress",
        "sb_um": "drain-side gate-to-active-edge distance for LOD/stress",
        "well_edge_distance_um": "gate/device distance to the relevant well edge for WPE",
        "sti_spacing_um": "active/STI neighborhood spacing",
        "orientation_deg": "layout orientation permitted by the process",
        "finger_count": "number of equal-length gate fingers",
        "gate_pitch_um": "gate-to-gate pitch",
        "dummy_gate_count": "dummy gates at the device-array boundary",
        "source_contact_count": "source contact multiplicity",
        "drain_contact_count": "drain contact multiplicity",
        "guard_ring_distance_um": "distance to the selected guard ring",
        "guard_ring_type": "process-defined guard-ring type or bias",
    },
    "resistor": {
        "width_um": "resistor width transverse to current flow",
        "length_um": "resistor length along current flow",
        "contact_count": "contacts at each resistor terminal",
        "orientation_deg": "layout orientation permitted by the process",
        "dummy_count": "matching dummy elements",
    },
    "capacitor": {
        "area_um2": "active capacitor plate area",
        "perimeter_um": "active capacitor plate perimeter",
        "aspect_ratio": "plate aspect ratio at controlled area or perimeter",
        "orientation_deg": "layout orientation permitted by the process",
        "dummy_count": "matching dummy elements",
        "shield_distance_um": "distance to a process-supported shield or unrelated routing",
    },
}


def _scalar(value: Any, *, field: str) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise AnalysisError(
            code="INVALID_DOE_VALUE",
            message="DOE values must be finite JSON scalars.",
            details={"field": field, "value": value},
            next_action="Use a finite number, string, integer, or boolean.",
        )
    if not isinstance(value, (str, int, float, bool)):
        raise AnalysisError(
            code="INVALID_DOE_VALUE",
            message="DOE values must be JSON scalars.",
            details={"field": field, "value": value},
            next_action="Use a finite number, string, integer, or boolean.",
        )
    return value


def plan_phase1_device_doe(
    *,
    process_profile: str,
    process_profile_version: str,
    process_profile_confirmed: bool,
    family: str,
    device_type: str,
    measurement: str,
    required_terminals: Iterable[str],
    supported_axes: Iterable[str],
    baseline: Mapping[str, Any],
    sweeps: Mapping[str, Iterable[Any]],
    design_mode: str = "one_factor_at_a_time",
    replicates: int = 1,
    max_splits: int = 500,
) -> dict[str, Any]:
    """Build a deterministic LDE/passive split matrix from explicit process capability."""

    for field, value in (
        ("process_profile", process_profile),
        ("process_profile_version", process_profile_version),
        ("device_type", device_type),
        ("measurement", measurement),
    ):
        if not isinstance(value, str) or not value.strip():
            raise AnalysisError(
                code="INVALID_DOE_CONTRACT",
                message=f"{field} must be a non-empty string.",
                details={"field": field, "value": value},
                next_action="Provide an explicit process, device, and measurement contract.",
            )
    if not process_profile_confirmed:
        raise AnalysisError(
            code="PROCESS_PROFILE_CONFIRMATION_REQUIRED",
            message="The process capability/version must be explicitly confirmed before DOE expansion.",
            details={
                "process_profile": process_profile,
                "process_profile_version": process_profile_version,
            },
            next_action="Confirm the exact PDK/rule/model version and supported DOE axes.",
        )
    if family not in PHASE1_AXIS_CATALOG:
        raise AnalysisError(
            code="UNSUPPORTED_TEG_DEVICE_FAMILY",
            message="The requested family is outside the Phase 1 DOE catalog.",
            details={"family": family, "supported": sorted(PHASE1_AXIS_CATALOG)},
            next_action="Choose transistor, resistor, or capacitor.",
        )
    if design_mode not in {"one_factor_at_a_time", "full_factorial"}:
        raise AnalysisError(
            code="UNSUPPORTED_DOE_MODE",
            message="DOE mode must be one_factor_at_a_time or full_factorial.",
            details={"design_mode": design_mode},
            next_action="Use OVAT for isolated effects or explicitly request full_factorial.",
        )
    if (
        isinstance(replicates, bool)
        or not isinstance(replicates, int)
        or replicates < 1
        or isinstance(max_splits, bool)
        or not isinstance(max_splits, int)
        or max_splits < 1
    ):
        raise AnalysisError(
            code="INVALID_DOE_LIMIT",
            message="replicates and max_splits must be positive integers.",
            details={"replicates": replicates, "max_splits": max_splits},
            next_action="Provide positive integer DOE limits.",
        )

    terminals = [value.strip() for value in required_terminals if isinstance(value, str)]
    if not terminals or len(terminals) != len(set(terminals)):
        raise AnalysisError(
            code="INVALID_DOE_TERMINALS",
            message="required_terminals must be a non-empty unique list.",
            details={"required_terminals": terminals},
            next_action="List every direct-measurement terminal exactly once.",
        )

    catalog = PHASE1_AXIS_CATALOG[family]
    supported = set(supported_axes)
    unknown_supported = sorted(supported.difference(catalog))
    requested = set(sweeps)
    unknown_requested = sorted(requested.difference(catalog))
    unsupported_requested = sorted(requested.difference(supported))
    missing_baseline = sorted(requested.difference(baseline))
    if unknown_supported or unknown_requested or unsupported_requested or missing_baseline:
        raise AnalysisError(
            code="DOE_AXIS_CONTRACT_MISMATCH",
            message="Requested DOE axes are not fully supported and baselined by the process contract.",
            details={
                "unknown_supported_axes": unknown_supported,
                "unknown_requested_axes": unknown_requested,
                "unsupported_requested_axes": unsupported_requested,
                "requested_axes_missing_baseline": missing_baseline,
                "catalog_axes": sorted(catalog),
            },
            next_action="Use only process-confirmed catalog axes and provide every baseline value.",
        )

    base = {key: _scalar(value, field=f"baseline.{key}") for key, value in baseline.items()}
    sweep_values: dict[str, list[Any]] = {}
    for axis in sorted(sweeps):
        values = [_scalar(value, field=f"sweeps.{axis}") for value in sweeps[axis]]
        if not values:
            raise AnalysisError(
                code="EMPTY_DOE_SWEEP",
                message="Every requested DOE axis needs at least one value.",
                details={"axis": axis},
                next_action="Provide one or more process-legal sweep values.",
            )
        unique: list[Any] = []
        for value in values:
            if value not in unique:
                unique.append(value)
        sweep_values[axis] = unique

    parameter_sets: list[dict[str, Any]] = [dict(base)]
    if design_mode == "one_factor_at_a_time":
        for axis, values in sweep_values.items():
            for value in values:
                if value == base[axis]:
                    continue
                parameters = dict(base)
                parameters[axis] = value
                parameter_sets.append(parameters)
    else:
        axes = sorted(sweep_values)
        combinations = product(*(sweep_values[axis] for axis in axes))
        parameter_sets = []
        for values in combinations:
            parameters = dict(base)
            parameters.update(dict(zip(axes, values)))
            parameter_sets.append(parameters)

    expanded: list[dict[str, Any]] = []
    for variant_index, parameters in enumerate(parameter_sets, start=1):
        changed_axes = [
            axis for axis in sorted(sweep_values) if parameters.get(axis) != base.get(axis)
        ]
        for replicate in range(1, replicates + 1):
            expanded.append(
                {
                    "split_id": f"{family[:1].upper()}{variant_index:03d}_R{replicate:02d}",
                    "variant_index": variant_index,
                    "replicate": replicate,
                    "changed_axes": changed_axes,
                    "parameters": parameters,
                }
            )
    if len(expanded) > max_splits:
        raise AnalysisError(
            code="DOE_SPLIT_LIMIT_EXCEEDED",
            message="The expanded DOE exceeds the explicit split limit.",
            details={"expanded_split_count": len(expanded), "max_splits": max_splits},
            next_action="Reduce axes/values/replicates or explicitly increase max_splits.",
        )

    return {
        "ok": True,
        "process_contract": {
            "profile": process_profile,
            "version": process_profile_version,
            "confirmed": True,
            "supported_axes": sorted(supported),
        },
        "device_contract": {
            "family": family,
            "device_type": device_type,
            "measurement": measurement,
            "required_terminals": terminals,
        },
        "design_mode": design_mode,
        "baseline": base,
        "axis_definitions": {axis: catalog[axis] for axis in sorted(requested)},
        "variant_count": len(parameter_sets),
        "replicates": replicates,
        "split_count": len(expanded),
        "splits": expanded,
        "layout_generation_authorized": False,
        "next_gate": "assign splits to explicit DUT terminal/Pad contracts and verify routing",
    }
