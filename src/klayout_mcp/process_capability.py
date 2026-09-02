"""Process-neutral capability schema for target-specific TEG profiles."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .dbu_grid import DbuGridError, micron_to_dbu
from .device_doe import PHASE1_AXIS_CATALOG
from .errors import AnalysisError


EVIDENCE_STATES = {"approved", "public_demo", "research_only"}
VERIFICATION_STATES = {"approved", "public", "projection_only", "not_available"}
GEOMETRY_SOURCES = {"approved_pcell", "reference_geometry", "rule_synthesized"}


def pdk_profile_input_contract() -> dict[str, Any]:
    """Describe the inputs required to onboard one real target process."""

    return {
        "ok": True,
        "schema_version": 1,
        "required_for_core_profile": {
            "process": ["name", "version", "evidence_status"],
            "units": ["dbu_um", "manufacturing_grid_um"],
            "layers": "semantic_role -> [gds_layer, datatype]",
            "routing_metals": [
                "ordered metal name and layer_role",
                "min_width_um",
                "min_space_um",
                "explicit width/parallel-length spacing table when applicable",
            ],
        },
        "required_from_organization_preset": [
            "fixed terminal names and order per family/measurement",
            "supported company measurement modes",
        ],
        "required_for_drawing_job": [
            "target GDS/OAS and top-cell policy",
            "frame and Pad geometry/count/placement",
            "selected DUTs and parameter splits",
            "W/L, DOE, and LDE axes selected for this drawing",
            "terminal-to-Pad measurement assignment",
            "allowed routing layers and obstacles",
            "optional project max routing width",
            "output path and authorization state",
        ],
        "conditional_inputs": {
            "external_verification_requested": [
                "host-policy-selected evidence kinds, if any",
                "adapter/deck/runset provenance for each selected kind",
                "device coverage: covered | pending | unavailable",
            ],
            "contact_or_via_used": [
                "lower/cut/upper layer roles",
                "cut width/space/array rules",
                "lower and upper enclosure",
            ],
            "transistor_geometry_generated": [
                "well/active/gate/implant/contact layer roles",
                "W/L definitions and min/max/grid",
                "contact and gate/active enclosure/extension rules",
                "physical rule meaning for each selected LDE axis",
                "body-tie and well-continuity policy",
            ],
            "target_electrical_value_claimed": [
                "electrical model provenance",
                "bias/current limits and temperature assumptions",
                "extraction evidence when required by the claim",
            ],
        },
        "preferred_sources": [
            "layermap for semantic stream mapping",
            "PDK documentation or user-confirmed rule table for geometry limits",
            "techfile only through an explicit importer; never infer from layer names",
            "user-confirmed reference GDS for drawing precedent",
        ],
        "not_pdk_inputs": [
            "frame size and Pad topology",
            "routing style",
            "DUT split table",
            "company terminal names and measurement modes",
            "job-specific DOE/LDE axes",
            "project max routing width",
            "company verification-engine availability",
        ],
        "policy": {
            "unknown_values_are_inferred": False,
            "bundled_process_profile_is_fallback": False,
            "verification_is_run_automatically": False,
            "production_ready_without_approved_evidence": False,
            "reference_style_overrides_process_identity": False,
        },
        "runtime_profile_policy": {
            "bundled_process_profiles": False,
            "profile_must_be_user_or_host_supplied": True,
            "unknown_process_values_fail_closed": True,
            "onboarding_document": "onboarding.md",
        },
    }


def _positive(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise AnalysisError(
            code="INVALID_PROCESS_CAPABILITY",
            message=f"{field} must be a finite positive number.",
            details={"field": field, "value": value},
            next_action="Correct the explicit process capability profile.",
        )
    return float(value)


def validate_process_capability(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate explicit layer/rule/device evidence without filling missing facts."""

    if profile.get("schema_version") != 1:
        raise AnalysisError(
            code="UNSUPPORTED_PROCESS_CAPABILITY_SCHEMA",
            message="Process capability schema_version must be 1.",
            details={"schema_version": profile.get("schema_version")},
            next_action="Migrate the profile to schema version 1.",
        )
    process = profile.get("process")
    if not isinstance(process, Mapping):
        raise AnalysisError(
            code="INVALID_PROCESS_CAPABILITY",
            message="The process identity/evidence object is missing.",
            details={},
            next_action="Provide process name, version, and evidence_status.",
        )
    name = process.get("name")
    version = process.get("version")
    evidence = process.get("evidence_status")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(version, str)
        or not version.strip()
        or evidence not in EVIDENCE_STATES
    ):
        raise AnalysisError(
            code="INVALID_PROCESS_CAPABILITY",
            message="Process name/version/evidence_status is invalid.",
            details={"process": dict(process), "allowed_evidence_states": sorted(EVIDENCE_STATES)},
            next_action="Identify the exact process version and evidence state.",
        )

    dbu = _positive(profile.get("dbu_um"), field="dbu_um")
    manufacturing_grid = _positive(
        profile.get("manufacturing_grid_um"), field="manufacturing_grid_um"
    )
    try:
        manufacturing_grid_dbu = micron_to_dbu(manufacturing_grid, dbu)
    except DbuGridError as exc:
        raise AnalysisError(
            code="INVALID_PROCESS_MANUFACTURING_GRID",
            message="manufacturing_grid_um must be exactly representable in layout DBU.",
            details={"dbu_um": dbu, "manufacturing_grid_um": manufacturing_grid},
            next_action="Use an integer multiple of dbu_um for the manufacturing grid.",
        ) from exc
    if manufacturing_grid_dbu < 1:
        raise AnalysisError(
            code="INVALID_PROCESS_MANUFACTURING_GRID",
            message="Manufacturing grid cannot be smaller than one layout DBU.",
            details={"dbu_um": dbu, "manufacturing_grid_um": manufacturing_grid},
            next_action="Use manufacturing_grid_um greater than or equal to dbu_um.",
        )

    layers = profile.get("layers")
    if not isinstance(layers, Mapping) or not layers:
        raise AnalysisError(
            code="INVALID_PROCESS_CAPABILITY",
            message="An explicit non-empty layer map is required.",
            details={},
            next_action="Map every required semantic role to [layer, datatype].",
        )
    normalized_layers: dict[str, list[int]] = {}
    used_stream: dict[tuple[int, int], str] = {}
    for role, stream in layers.items():
        if (
            not isinstance(role, str)
            or not role.strip()
            or not isinstance(stream, (list, tuple))
            or len(stream) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in stream
            )
        ):
            raise AnalysisError(
                code="INVALID_PROCESS_LAYER_MAP",
                message="Layer roles must map to non-negative integer [layer, datatype].",
                details={"role": role, "stream": stream},
                next_action="Correct the explicit stream mapping.",
            )
        pair = (stream[0], stream[1])
        if pair in used_stream:
            raise AnalysisError(
                code="PROCESS_LAYER_COLLISION",
                message="Two semantic roles use the same stream layer/datatype.",
                details={"roles": [used_stream[pair], role], "stream": list(pair)},
                next_action="Use the approved role mapping or explicitly merge the semantic role.",
            )
        used_stream[pair] = role
        normalized_layers[role] = list(pair)

    metals = profile.get("routing_metals")
    if not isinstance(metals, list) or not metals:
        raise AnalysisError(
            code="INVALID_PROCESS_CAPABILITY",
            message="At least one routing metal capability is required.",
            details={},
            next_action="Declare first metal and its process-specific width/space rules.",
        )
    normalized_metals: list[dict[str, Any]] = []
    for index, metal in enumerate(metals):
        if not isinstance(metal, Mapping) or metal.get("layer_role") not in normalized_layers:
            raise AnalysisError(
                code="INVALID_ROUTING_METAL_CAPABILITY",
                message="Routing metal references an unmapped layer role.",
                details={"metal_index": index, "metal": metal},
                next_action="Map the metal role before declaring routing capability.",
            )
        metal_name = metal.get("name")
        if not isinstance(metal_name, str) or not metal_name.strip():
            raise AnalysisError(
                code="INVALID_ROUTING_METAL_CAPABILITY",
                message="Routing metal name is missing.",
                details={"metal_index": index},
                next_action="Provide a stable canonical metal name.",
            )
        normalized = dict(metal)
        normalized["min_width_um"] = _positive(
            metal.get("min_width_um"), field=f"routing_metals[{index}].min_width_um"
        )
        normalized["min_space_um"] = _positive(
            metal.get("min_space_um"), field=f"routing_metals[{index}].min_space_um"
        )
        if metal.get("profile_max_width_um") is not None:
            normalized["profile_max_width_um"] = _positive(
                metal.get("profile_max_width_um"),
                field=f"routing_metals[{index}].profile_max_width_um",
            )
        spacing_table = metal.get("spacing_table", [])
        if not isinstance(spacing_table, list):
            raise AnalysisError(
                code="INVALID_ROUTING_METAL_CAPABILITY",
                message="spacing_table must be an ordered list.",
                details={"metal_index": index, "spacing_table": spacing_table},
                next_action="Provide explicit numeric spacing rules or an empty list.",
            )
        normalized_table: list[dict[str, float]] = []
        for rule_index, rule in enumerate(spacing_table):
            if not isinstance(rule, Mapping):
                raise AnalysisError(
                    code="INVALID_ROUTING_METAL_CAPABILITY",
                    message="Every spacing_table rule must be an object.",
                    details={"metal_index": index, "rule_index": rule_index},
                    next_action="Provide width, parallel-length, and spacing thresholds.",
                )
            prefix = f"routing_metals[{index}].spacing_table[{rule_index}]"
            normalized_table.append(
                {
                    "width_over_um": _positive(
                        rule.get("width_over_um"), field=f"{prefix}.width_over_um"
                    ),
                    "parallel_length_at_least_um": _positive(
                        rule.get("parallel_length_at_least_um"),
                        field=f"{prefix}.parallel_length_at_least_um",
                    ),
                    "min_space_um": _positive(
                        rule.get("min_space_um"), field=f"{prefix}.min_space_um"
                    ),
                }
            )
        normalized["spacing_table"] = normalized_table
        if bool(metal.get("width_dependent_spacing")) and not normalized_table:
            raise AnalysisError(
                code="INVALID_ROUTING_METAL_CAPABILITY",
                message="width_dependent_spacing is true but spacing_table is empty.",
                details={"metal_index": index, "metal_name": metal_name},
                next_action="Provide explicit rules or clear the capability flag.",
            )
        normalized_metals.append(normalized)

    devices = profile.get("devices")
    if not isinstance(devices, Mapping) or not devices:
        raise AnalysisError(
            code="INVALID_PROCESS_CAPABILITY",
            message="At least one explicit measurable device capability is required.",
            details={},
            next_action="Declare a device family, terminals, measurements, DOE axes, and source.",
        )
    normalized_devices: dict[str, dict[str, Any]] = {}
    for device_name, device in devices.items():
        if not isinstance(device_name, str) or not isinstance(device, Mapping):
            raise AnalysisError(
                code="INVALID_PROCESS_DEVICE_CAPABILITY",
                message="Device capabilities must be named objects.",
                details={"device": device_name},
                next_action="Correct the device capability entry.",
            )
        family = device.get("family")
        source = device.get("geometry_source")
        if family not in PHASE1_AXIS_CATALOG or source not in GEOMETRY_SOURCES:
            raise AnalysisError(
                code="INVALID_PROCESS_DEVICE_CAPABILITY",
                message="Device family or geometry source is unsupported.",
                details={"device": device_name, "family": family, "geometry_source": source},
                next_action="Use a supported Phase 1 family and explicit geometry source.",
            )
        list_fields = {
            "terminals": device.get("terminals"),
            "measurements": device.get("measurements"),
            "doe_axes": device.get("doe_axes"),
            "required_layers": device.get("required_layers"),
        }
        if any(
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))
            for values in list_fields.values()
        ):
            raise AnalysisError(
                code="INVALID_PROCESS_DEVICE_CAPABILITY",
                message="Device list fields must be non-empty unique string lists.",
                details={"device": device_name, **list_fields},
                next_action="Declare terminals, measurements, DOE axes, and layers exactly.",
            )
        axes = list_fields["doe_axes"]
        required_layers = list_fields["required_layers"]
        invalid_axes = sorted(set(axes).difference(PHASE1_AXIS_CATALOG[family]))
        missing_layers = sorted(set(required_layers).difference(normalized_layers))
        if invalid_axes or missing_layers:
            raise AnalysisError(
                code="INVALID_PROCESS_DEVICE_CAPABILITY",
                message="Device axes or required layers are not supported by the profile.",
                details={
                    "device": device_name,
                    "invalid_doe_axes": invalid_axes,
                    "missing_layer_roles": missing_layers,
                },
                next_action="Correct the process device capability contract.",
            )
        normalized_devices[device_name] = dict(device)

    verification = profile.get("verification")
    if not isinstance(verification, Mapping) or set(verification) != {"drc", "lvs", "pex"}:
        raise AnalysisError(
            code="INVALID_PROCESS_VERIFICATION_CAPABILITY",
            message="Verification must explicitly state drc, lvs, and pex evidence.",
            details={"verification": verification},
            next_action="Set each evidence state, including not_available where applicable.",
        )
    invalid_verification = {
        key: value for key, value in verification.items() if value not in VERIFICATION_STATES
    }
    if invalid_verification:
        raise AnalysisError(
            code="INVALID_PROCESS_VERIFICATION_CAPABILITY",
            message="A process verification evidence state is invalid.",
            details={"invalid": invalid_verification, "allowed": sorted(VERIFICATION_STATES)},
            next_action="Use an allowed explicit verification evidence state.",
        )

    return {
        "ok": True,
        "schema_version": 1,
        "process": {"name": name, "version": version, "evidence_status": evidence},
        "dbu_um": dbu,
        "manufacturing_grid_um": manufacturing_grid,
        "manufacturing_grid_dbu": manufacturing_grid_dbu,
        "layers": normalized_layers,
        "routing_metals": normalized_metals,
        "first_metal_role": normalized_metals[0]["layer_role"],
        "devices": normalized_devices,
        "device_families": sorted({item["family"] for item in normalized_devices.values()}),
        "verification": dict(verification),
        "process_profile_approved": evidence == "approved",
        "production_ready": False,
        "production_ready_reason": "outside_process_capability_scope",
        "missing_production_evidence": [],
        "unapproved_optional_verification_evidence": [
            key for key in ("drc", "lvs", "pex") if verification[key] != "approved"
        ],
    }


def describe_builtin_process_capability(profile_name: str) -> dict[str, Any]:
    """Fail closed: real process data must be supplied during onboarding."""

    raise AnalysisError(
        code="NO_BUNDLED_PROCESS_CAPABILITY",
        message="This MCP intentionally ships without a built-in fabrication-process profile.",
        details={
            "requested_profile_name": profile_name,
            "bundled_process_profiles": [],
            "input_contract": pdk_profile_input_contract(),
        },
        next_action=(
            "Follow onboarding.md, collect the target process inputs, and pass the composed "
            "schema-v1 object to validate_process_capability_profile."
        ),
    )


def required_metal_space_um(
    metal_capability: Mapping[str, Any],
    *,
    width_um: float,
    parallel_length_um: float,
) -> float:
    """Evaluate an explicit width/parallel-length spacing table."""

    required = float(metal_capability["min_space_um"])
    for rule in metal_capability.get("spacing_table", []):
        if (
            width_um > float(rule["width_over_um"])
            and parallel_length_um >= float(rule["parallel_length_at_least_um"])
        ):
            required = max(required, float(rule["min_space_um"]))
    return required
