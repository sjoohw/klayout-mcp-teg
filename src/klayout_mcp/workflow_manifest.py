"""Versioned, content-addressed documents for the safe TEG workflow."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from .errors import AnalysisError
from .evidence_state import EVIDENCE_STATES, evaluate_evidence_ladder


SCHEMA_VERSION = 1
CANONICALIZATION_PROFILE = "klayout_mcp_canonical_json_v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEVICE_FAMILIES = ("transistor", "resistor", "capacitor")
DESIGN_INTENT_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "intent_id",
        "units",
        "process",
        "frame",
        "pads",
        "devices",
        "terminal_contracts",
        "terminal_net_pad_map",
        "measurement_requirements",
        "routing_policy",
        "verification_policy",
        "output_policy",
        "unresolved_questions",
    }
)
DESIGN_INTENT_OPTIONAL_FIELDS = frozenset({"technology_adapter"})
APPROVAL_REFERENCE_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "draft_sha256",
        "process_capability_sha256",
        "source_artifact_sha256s",
        "approval_scope",
        "output_classes",
        "signer_reference",
        "scheme_id",
        "attestation_reference",
        "approved_at",
    }
)
MEASUREMENT_MANIFEST_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "design_intent_sha256",
        "generated_layout_sha256",
        "dut_pin_map",
        "electrical_topology",
        "stimuli",
        "observables",
        "timing",
        "environment",
        "safety_envelope",
        "calibration_and_deembedding",
    }
)


def _fail(code: str, message: str, *, details: Mapping[str, Any]) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details=dict(details),
        next_action="Correct the versioned document and recompute its canonical SHA-256.",
    )


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            f"{field} must be an object.",
            details={"field": field, "received_type": type(value).__name__},
        )
    return value


def _sequence(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            f"{field} must be an array.",
            details={"field": field, "received_type": type(value).__name__},
        )
    return list(value)


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            f"{field} must be a non-empty string.",
            details={"field": field, "value": value},
        )
    return value.strip()


def _sha256(value: Any, *, field: str) -> str:
    normalized = _string(value, field=field)
    if not SHA256_PATTERN.fullmatch(normalized):
        _fail(
            "INVALID_CONTENT_HASH",
            f"{field} must be a lowercase SHA-256 hex digest.",
            details={"field": field, "value": value},
        )
    return normalized


def _positive_number(value: Any, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            f"{field} must be a finite positive number.",
            details={"field": field, "value": value},
        )
    return float(value)


def _finite_number(value: Any, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            f"{field} must be a finite number.",
            details={"field": field, "value": value},
        )
    return float(value)


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            f"{field} must be a positive integer.",
            details={"field": field, "value": value},
        )
    return value


def _require_keys(
    value: Mapping[str, Any],
    *,
    field: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = sorted(required.difference(value))
    unexpected = sorted(set(value).difference(required | optional))
    if missing or unexpected:
        _fail(
            "WORKFLOW_SCHEMA_MISMATCH",
            f"{field} does not match schema version {SCHEMA_VERSION}.",
            details={"field": field, "missing": missing, "unexpected": unexpected},
        )


def _require_schema_version(document: Mapping[str, Any], *, kind: str) -> None:
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        _fail(
            "UNSUPPORTED_WORKFLOW_SCHEMA_VERSION",
            f"{kind} schema version is not supported and is not migrated implicitly.",
            details={
                "document_kind": kind,
                "schema_version": version,
                "supported_schema_versions": [SCHEMA_VERSION],
                "implicit_migration_allowed": False,
            },
        )


def _validate_stimulus_program(value: Any, *, field: str) -> Mapping[str, Any]:
    program = _mapping(value, field=field)
    kind = _string(program.get("kind"), field=f"{field}.kind")
    schemas = {
        "dc_value": {"kind", "value", "unit"},
        "linear_sweep": {"kind", "start", "stop", "step", "direction", "unit"},
        "ac_amplitude": {"kind", "amplitude", "unit"},
    }
    if kind not in schemas:
        _fail(
            "INVALID_STIMULUS_PROGRAM_KIND",
            "Stimulus program kind must be explicit and supported.",
            details={"field": field, "kind": kind, "allowed": sorted(schemas)},
        )
    _require_keys(program, field=field, required=schemas[kind])
    _string(program["unit"], field=f"{field}.unit")
    numeric_fields = {
        "dc_value": ("value",),
        "linear_sweep": ("start", "stop", "step"),
        "ac_amplitude": ("amplitude",),
    }[kind]
    for name in numeric_fields:
        _finite_number(program[name], field=f"{field}.{name}")
    if kind == "linear_sweep":
        if float(program["step"]) == 0:
            _fail(
                "INVALID_STIMULUS_SWEEP_STEP",
                "Linear sweep step cannot be zero.",
                details={"field": field, "step": program["step"]},
            )
        if program["direction"] not in {"ascending", "descending"}:
            _fail(
                "INVALID_STIMULUS_SWEEP_DIRECTION",
                "Linear sweep direction must be ascending or descending.",
                details={"field": field, "direction": program["direction"]},
            )
    return program


def _validate_compliance(value: Any, *, field: str) -> Mapping[str, Any]:
    compliance = _mapping(value, field=field)
    _require_keys(
        compliance,
        field=field,
        required={"quantity", "limit", "unit"},
    )
    _string(compliance["quantity"], field=f"{field}.quantity")
    _positive_number(compliance["limit"], field=f"{field}.limit")
    _string(compliance["unit"], field=f"{field}.unit")
    return compliance


def _validate_measurement_timing(value: Any, *, field: str) -> Mapping[str, Any]:
    timing = _mapping(value, field=field)
    _require_keys(
        timing,
        field=field,
        required={"settling_s", "integration", "hold_s", "delay_s"},
    )
    for name in ("settling_s", "hold_s", "delay_s"):
        if _finite_number(timing[name], field=f"{field}.{name}") < 0:
            _fail(
                "INVALID_MEASUREMENT_TIMING",
                "Measurement timing values must be nonnegative.",
                details={"field": f"{field}.{name}", "value": timing[name]},
            )
    _mapping(timing["integration"], field=f"{field}.integration")
    return timing


def _validate_safety_envelope(value: Any, *, field: str) -> Mapping[str, Any]:
    safety = _mapping(value, field=field)
    _require_keys(
        safety,
        field=field,
        required={"limits", "source_reference", "em_current_density_evidence"},
    )
    limits = _mapping(safety["limits"], field=f"{field}.limits")
    allowed_limits = {
        "max_abs_voltage_v",
        "max_voltage_v",
        "max_abs_current_a",
        "max_current_a",
        "max_frequency_hz",
    }
    unexpected = sorted(set(limits).difference(allowed_limits))
    if unexpected:
        _fail(
            "UNKNOWN_MEASUREMENT_SAFETY_LIMIT",
            "Safety limits must use a supported canonical quantity.",
            details={"field": field, "unexpected": unexpected},
        )
    for name, limit in limits.items():
        _positive_number(limit, field=f"{field}.limits.{name}")
    _string(safety["source_reference"], field=f"{field}.source_reference")
    if safety["em_current_density_evidence"] is not None:
        _mapping(
            safety["em_current_density_evidence"],
            field=f"{field}.em_current_density_evidence",
        )
    return safety


def _check_json_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail(
                    "NON_STRING_CANONICAL_JSON_KEY",
                    "Canonical workflow JSON permits string object keys only.",
                    details={"path": path, "key_type": type(key).__name__},
                )
            _check_json_keys(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _check_json_keys(child, path=f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        _fail(
            "NONFINITE_CANONICAL_JSON_NUMBER",
            "Canonical workflow JSON forbids NaN and infinity.",
            details={"path": path, "value": repr(value)},
        )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically without silently coercing unsafe values."""

    _check_json_keys(value)
    normalized = _normalize_canonical_numbers(value)
    try:
        encoded = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        _fail(
            "NON_CANONICAL_JSON_VALUE",
            "Workflow documents must contain JSON-compatible values only.",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        )
    return encoded.encode("utf-8")


def _normalize_canonical_numbers(value: Any) -> Any:
    """Make JSON integers and mathematically integral floats hash identically."""

    if isinstance(value, Mapping):
        return {key: _normalize_canonical_numbers(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_canonical_numbers(child) for child in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonicalization_contract() -> dict[str, Any]:
    """Describe the named v1 profile that approval clients must reproduce exactly."""

    return {
        "profile": CANONICALIZATION_PROFILE,
        "encoding": "utf-8",
        "object_key_order": "unicode_codepoint_sort",
        "whitespace": "none",
        "ensure_ascii": False,
        "nonfinite_numbers_allowed": False,
        "integral_float_normalization": "convert_to_json_integer",
        "hash": "sha256_lowercase_hex",
        "rfc8785_claimed": False,
        "cross_language_use_requires_shared_fixtures": True,
    }


def workflow_document_contract() -> dict[str, Any]:
    """Expose schema discovery without granting workflow authority."""

    return {
        "schema_version": SCHEMA_VERSION,
        "schema_frozen": True,
        "document_kinds": [
            "DesignIntentDraft",
            "ApprovedDesignIntent",
            "JobManifest",
            "MeasurementManifest",
        ],
        "canonicalization": canonicalization_contract(),
        "schema_discovery": {
            "DesignIntentDraft": {
                "required_top_level_fields": sorted(DESIGN_INTENT_REQUIRED_FIELDS),
                "canonical_nested_template_tool": "teg_intake",
                "template_requires_exact_process_profile_version_and_family": True,
            },
            "ApprovedDesignIntent": {
                "required_top_level_fields": sorted(APPROVAL_REFERENCE_REQUIRED_FIELDS),
                "issued_by": "trusted_host_approval_backend",
                "model_may_self_issue": False,
            },
            "MeasurementManifest": {
                "required_top_level_fields": sorted(
                    MEASUREMENT_MANIFEST_REQUIRED_FIELDS
                ),
                "validated_against_exact_design_intent_and_layout_hash": True,
                "means_executable_tester_program": False,
            },
        },
        "draft_authorizes_planning": False,
        "draft_authorizes_generation": False,
        "approval_reference_shape_is_trusted_approval": False,
        "measurement_layout_reference_is_file_hash_verification": False,
    }


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def immutable_json_copy(value: Any) -> Any:
    """Return a detached JSON representation used as an immutable manifest snapshot."""

    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def validate_design_intent_draft(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one generic draft; a valid draft never grants plan/generate authority."""

    draft = _mapping(document, field="DesignIntentDraft")
    _require_schema_version(draft, kind="DesignIntentDraft")
    _require_keys(
        draft,
        field="DesignIntentDraft",
        required=set(DESIGN_INTENT_REQUIRED_FIELDS),
        optional=set(DESIGN_INTENT_OPTIONAL_FIELDS),
    )
    _string(draft["intent_id"], field="intent_id")
    if draft["units"] != "um":
        _fail(
            "UNSUPPORTED_WORKFLOW_UNITS",
            "DesignIntentDraft version 1 uses microns for all layout dimensions.",
            details={"units": draft["units"], "supported_units": ["um"]},
        )

    process = _mapping(draft["process"], field="process")
    _require_keys(
        process,
        field="process",
        required={"profile", "version", "capability_sha256"},
    )
    if "technology_adapter" in draft:
        adapter = _mapping(draft["technology_adapter"], field="technology_adapter")
        _require_keys(
            adapter,
            field="technology_adapter",
            required={"identity", "package_sha256", "registry_snapshot_sha256"},
        )
        identity = _mapping(adapter["identity"], field="technology_adapter.identity")
        _require_keys(
            identity,
            field="technology_adapter.identity",
            required={
                "technology",
                "pdk_revision",
                "adapter_kind",
                "device_family",
                "topology",
                "package_version",
            },
        )
        for key, value in identity.items():
            _string(value, field=f"technology_adapter.identity.{key}")
        _sha256(adapter["package_sha256"], field="technology_adapter.package_sha256")
        _sha256(
            adapter["registry_snapshot_sha256"],
            field="technology_adapter.registry_snapshot_sha256",
        )
    _string(process["profile"], field="process.profile")
    _string(process["version"], field="process.version")
    _sha256(process["capability_sha256"], field="process.capability_sha256")

    frame = _mapping(draft["frame"], field="frame")
    _require_keys(
        frame,
        field="frame",
        required={"width_um", "height_um", "origin_um", "allowed_boundary_um"},
    )
    _positive_number(frame["width_um"], field="frame.width_um")
    _positive_number(frame["height_um"], field="frame.height_um")
    origin = _sequence(frame["origin_um"], field="frame.origin_um")
    boundary = _sequence(frame["allowed_boundary_um"], field="frame.allowed_boundary_um")
    if len(origin) != 2 or len(boundary) != 4:
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            "Frame origin and boundary must have two and four coordinates respectively.",
            details={"origin_um": origin, "allowed_boundary_um": boundary},
        )

    pads = _mapping(draft["pads"], field="pads")
    _require_keys(
        pads,
        field="pads",
        required={"count", "rows", "outline_um", "numbering", "reserved_roles"},
        optional={"pitch_um", "explicit_bboxes_um"},
    )
    pad_count = _positive_int(pads["count"], field="pads.count")
    _positive_int(pads["rows"], field="pads.rows")
    outline = _sequence(pads["outline_um"], field="pads.outline_um")
    if len(outline) != 2:
        _fail(
            "INVALID_WORKFLOW_DOCUMENT",
            "pads.outline_um must contain width and height.",
            details={"outline_um": outline},
        )
    _positive_number(outline[0], field="pads.outline_um[0]")
    _positive_number(outline[1], field="pads.outline_um[1]")
    _string(pads["numbering"], field="pads.numbering")
    _mapping(pads["reserved_roles"], field="pads.reserved_roles")
    if "pitch_um" not in pads and "explicit_bboxes_um" not in pads:
        _fail(
            "PAD_TOPOLOGY_UNDERSPECIFIED",
            "Pad topology needs a pitch or explicit Pad boxes.",
            details={"required_one_of": ["pitch_um", "explicit_bboxes_um"]},
        )

    devices = _sequence(draft["devices"], field="devices")
    if not devices:
        _fail(
            "DESIGN_INTENT_DEVICES_REQUIRED",
            "A design intent must contain at least one DUT.",
            details={},
        )
    dut_ids: set[str] = set()
    families: set[str] = set()
    for index, raw_device in enumerate(devices):
        device = _mapping(raw_device, field=f"devices[{index}]")
        _require_keys(
            device,
            field=f"devices[{index}]",
            required={
                "dut_id",
                "family",
                "device_type",
                "measurement_type",
                "parameters",
                "doe",
                "placement_constraints",
            },
        )
        dut_id = _string(device["dut_id"], field=f"devices[{index}].dut_id")
        family = _string(device["family"], field=f"devices[{index}].family")
        if family not in DEVICE_FAMILIES:
            _fail(
                "UNSUPPORTED_DESIGN_INTENT_FAMILY",
                "Schema version 1 supports the Phase 1 device families only.",
                details={"family": family, "supported_families": list(DEVICE_FAMILIES)},
            )
        if dut_id in dut_ids:
            _fail(
                "DUPLICATE_DESIGN_INTENT_DUT",
                "Every DUT requires a stable unique identifier.",
                details={"dut_id": dut_id},
            )
        dut_ids.add(dut_id)
        families.add(family)
        _string(device["device_type"], field=f"devices[{index}].device_type")
        _string(device["measurement_type"], field=f"devices[{index}].measurement_type")
        _mapping(device["parameters"], field=f"devices[{index}].parameters")
        _mapping(device["doe"], field=f"devices[{index}].doe")
        _mapping(
            device["placement_constraints"],
            field=f"devices[{index}].placement_constraints",
        )

    contracts = _sequence(draft["terminal_contracts"], field="terminal_contracts")
    contract_terminals: dict[str, set[str]] = {}
    for index, raw_contract in enumerate(contracts):
        contract = _mapping(raw_contract, field=f"terminal_contracts[{index}]")
        _require_keys(
            contract,
            field=f"terminal_contracts[{index}]",
            required={"dut_id", "terminals"},
        )
        dut_id = _string(contract["dut_id"], field=f"terminal_contracts[{index}].dut_id")
        if dut_id not in dut_ids or dut_id in contract_terminals:
            _fail(
                "DESIGN_INTENT_TERMINAL_CONTRACT_MISMATCH",
                "Each declared DUT needs exactly one terminal contract.",
                details={"dut_id": dut_id},
            )
        terminal_names: set[str] = set()
        for term_index, raw_terminal in enumerate(
            _sequence(contract["terminals"], field=f"terminal_contracts[{index}].terminals")
        ):
            terminal = _mapping(
                raw_terminal,
                field=f"terminal_contracts[{index}].terminals[{term_index}]",
            )
            _require_keys(
                terminal,
                field=f"terminal_contracts[{index}].terminals[{term_index}]",
                required={"name", "electrical_role"},
            )
            name = _string(terminal["name"], field="terminal.name")
            _string(terminal["electrical_role"], field="terminal.electrical_role")
            if name in terminal_names:
                _fail(
                    "DUPLICATE_DESIGN_INTENT_TERMINAL",
                    "Terminal names must be unique within one DUT.",
                    details={"dut_id": dut_id, "terminal": name},
                )
            terminal_names.add(name)
        if not terminal_names:
            _fail(
                "DESIGN_INTENT_TERMINALS_REQUIRED",
                "Every DUT needs at least one required terminal.",
                details={"dut_id": dut_id},
            )
        contract_terminals[dut_id] = terminal_names
    if set(contract_terminals) != dut_ids:
        _fail(
            "DESIGN_INTENT_TERMINAL_CONTRACT_MISMATCH",
            "The terminal contract set must exactly match the DUT set.",
            details={
                "missing_duts": sorted(dut_ids.difference(contract_terminals)),
                "unexpected_duts": sorted(set(contract_terminals).difference(dut_ids)),
            },
        )

    mappings = _sequence(draft["terminal_net_pad_map"], field="terminal_net_pad_map")
    seen_terminal_refs: set[tuple[str, str]] = set()
    pad_to_net: dict[int, str] = {}
    net_records: dict[str, list[Mapping[str, Any]]] = {}
    for index, raw_record in enumerate(mappings):
        record = _mapping(raw_record, field=f"terminal_net_pad_map[{index}]")
        _require_keys(
            record,
            field=f"terminal_net_pad_map[{index}]",
            required={"dut_id", "terminal", "net", "pad", "shared_net_explicit"},
        )
        dut_id = _string(record["dut_id"], field=f"terminal_net_pad_map[{index}].dut_id")
        terminal = _string(
            record["terminal"], field=f"terminal_net_pad_map[{index}].terminal"
        )
        net = _string(record["net"], field=f"terminal_net_pad_map[{index}].net")
        pad = record["pad"]
        if (
            dut_id not in contract_terminals
            or terminal not in contract_terminals[dut_id]
            or isinstance(pad, bool)
            or not isinstance(pad, int)
            or pad < 1
            or pad > pad_count
            or not isinstance(record["shared_net_explicit"], bool)
        ):
            _fail(
                "INVALID_DESIGN_INTENT_TERMINAL_MAP",
                "Terminal mapping must reference one required terminal and a valid Pad.",
                details={"record_index": index, "record": dict(record)},
            )
        key = (dut_id, terminal)
        if key in seen_terminal_refs:
            _fail(
                "DUPLICATE_DESIGN_INTENT_TERMINAL_MAP",
                "Every DUT terminal must be mapped exactly once.",
                details={"dut_id": dut_id, "terminal": terminal},
            )
        if pad in pad_to_net and pad_to_net[pad] != net:
            _fail(
                "DESIGN_INTENT_PAD_NET_CONFLICT",
                "One Pad cannot represent multiple distinct direct-measurement nets.",
                details={"pad": pad, "existing_net": pad_to_net[pad], "new_net": net},
            )
        seen_terminal_refs.add(key)
        pad_to_net[pad] = net
        net_records.setdefault(net, []).append(record)
    required_terminal_refs = {
        (dut_id, terminal)
        for dut_id, terminals in contract_terminals.items()
        for terminal in terminals
    }
    if seen_terminal_refs != required_terminal_refs:
        _fail(
            "DESIGN_INTENT_TERMINAL_MAP_INCOMPLETE",
            "Terminal mapping must exactly cover every required DUT terminal.",
            details={
                "missing": sorted(required_terminal_refs.difference(seen_terminal_refs)),
                "unexpected": sorted(seen_terminal_refs.difference(required_terminal_refs)),
            },
        )
    implicit_sharing = {
        net: [f"{record['dut_id']}:{record['terminal']}" for record in records]
        for net, records in net_records.items()
        if len(records) > 1
        and any(record["shared_net_explicit"] is not True for record in records)
    }
    if implicit_sharing:
        _fail(
            "IMPLICIT_DESIGN_INTENT_NET_SHARING",
            "Every terminal on a shared net must explicitly acknowledge sharing.",
            details={"implicit_shared_nets": implicit_sharing},
        )

    measurement = _mapping(draft["measurement_requirements"], field="measurement_requirements")
    _require_keys(
        measurement,
        field="measurement_requirements",
        required={
            "stimuli",
            "observables",
            "biases",
            "timing",
            "environment",
            "safety_envelope",
        },
    )
    for kind in ("stimuli", "observables", "biases"):
        records = _sequence(measurement[kind], field=f"measurement_requirements.{kind}")
        for index, raw_record in enumerate(records):
            record = _mapping(
                raw_record, field=f"measurement_requirements.{kind}[{index}]"
            )
            required_fields = {"dut_id", "terminal", "mode"}
            if kind in {"stimuli", "biases"}:
                required_fields |= {
                    "source_mode",
                    "program",
                    "compliance",
                    "polarity",
                    "frequency_hz",
                }
            _require_keys(
                record,
                field=f"measurement_requirements.{kind}[{index}]",
                required=required_fields,
                optional={"quantity", "unit"},
            )
            ref = (
                _string(record["dut_id"], field=f"{kind}[{index}].dut_id"),
                _string(record["terminal"], field=f"{kind}[{index}].terminal"),
            )
            if ref not in required_terminal_refs:
                _fail(
                    "MEASUREMENT_REQUIREMENT_TERMINAL_UNKNOWN",
                    "Measurement requirements must reference a declared DUT terminal.",
                    details={"kind": kind, "index": index, "terminal_ref": list(ref)},
                )
            _string(record["mode"], field=f"{kind}[{index}].mode")
            for optional_field in ("quantity", "unit"):
                if optional_field in record:
                    _string(
                        record[optional_field],
                        field=f"{kind}[{index}].{optional_field}",
                    )
            if kind in {"stimuli", "biases"}:
                source_mode = _string(
                    record["source_mode"], field=f"{kind}[{index}].source_mode"
                )
                program = _validate_stimulus_program(
                    record["program"], field=f"{kind}[{index}].program"
                )
                compliance = _validate_compliance(
                    record["compliance"], field=f"{kind}[{index}].compliance"
                )
                source_quantity = (
                    "voltage" if "voltage" in source_mode.lower() else
                    "current" if "current" in source_mode.lower() else None
                )
                if source_quantity is None:
                    _fail(
                        "UNSUPPORTED_MEASUREMENT_SOURCE_MODE",
                        "Approved source_mode must explicitly identify voltage or current.",
                        details={"kind": kind, "index": index, "source_mode": source_mode},
                    )
                expected_program_unit = "V" if source_quantity == "voltage" else "A"
                if program["unit"] != expected_program_unit:
                    _fail(
                        "MEASUREMENT_SOURCE_PROGRAM_UNIT_MISMATCH",
                        "Approved source program unit does not match source_mode.",
                        details={
                            "kind": kind,
                            "index": index,
                            "source_mode": source_mode,
                            "program_unit": program["unit"],
                        },
                    )
                expected_compliance = (
                    ("current", "A")
                    if source_quantity == "voltage"
                    else ("voltage", "V")
                )
                if (
                    str(compliance["quantity"]).lower() != expected_compliance[0]
                    or compliance["unit"] != expected_compliance[1]
                ):
                    _fail(
                        "MEASUREMENT_SOURCE_COMPLIANCE_MISMATCH",
                        "Approved compliance quantity/unit is incompatible with source_mode.",
                        details={"kind": kind, "index": index},
                    )
                _string(record["polarity"], field=f"{kind}[{index}].polarity")
                if record["frequency_hz"] is not None:
                    _positive_number(
                        record["frequency_hz"], field=f"{kind}[{index}].frequency_hz"
                    )
    _validate_measurement_timing(
        measurement["timing"], field="measurement_requirements.timing"
    )
    _mapping(measurement["environment"], field="measurement_requirements.environment")
    _validate_safety_envelope(
        measurement["safety_envelope"],
        field="measurement_requirements.safety_envelope",
    )
    approved_limits = measurement["safety_envelope"]["limits"]
    canonical_limits: dict[str, float] = {}
    for canonical, aliases in {
        "voltage": ("max_abs_voltage_v", "max_voltage_v"),
        "current": ("max_abs_current_a", "max_current_a"),
        "frequency": ("max_frequency_hz",),
    }.items():
        supplied = [alias for alias in aliases if alias in approved_limits]
        if len(supplied) > 1:
            _fail(
                "AMBIGUOUS_MEASUREMENT_SAFETY_LIMIT",
                "Use only one canonical alias for each safety quantity.",
                details={"quantity": canonical, "fields": supplied},
            )
        if supplied:
            canonical_limits[canonical] = float(approved_limits[supplied[0]])
    for kind in ("stimuli", "biases"):
        for index, record in enumerate(measurement[kind]):
            source_mode = str(record["source_mode"]).lower()
            quantity = (
                "voltage" if "voltage" in source_mode else
                "current" if "current" in source_mode else None
            )
            if quantity in canonical_limits:
                program = record["program"]
                numeric_values = [
                    abs(float(program[name]))
                    for name in ("value", "amplitude", "start", "stop")
                    if name in program
                ]
                if numeric_values and max(numeric_values) > canonical_limits[quantity]:
                    _fail(
                        "DESIGN_INTENT_SAFETY_LIMIT_EXCEEDED",
                        "An approved stimulus or bias program exceeds its own safety envelope.",
                        details={
                            "kind": kind,
                            "index": index,
                            "quantity": quantity,
                            "program_peak": max(numeric_values),
                            "safety_limit": canonical_limits[quantity],
                        },
                    )
            compliance_quantity = str(record["compliance"]["quantity"]).lower()
            if (
                compliance_quantity in canonical_limits
                and float(record["compliance"]["limit"])
                > canonical_limits[compliance_quantity]
            ):
                _fail(
                    "DESIGN_INTENT_SAFETY_LIMIT_EXCEEDED",
                    "An approved compliance limit exceeds its own safety envelope.",
                    details={"kind": kind, "index": index},
                )
            if (
                record["frequency_hz"] is not None
                and "frequency" in canonical_limits
                and float(record["frequency_hz"]) > canonical_limits["frequency"]
            ):
                _fail(
                    "DESIGN_INTENT_SAFETY_LIMIT_EXCEEDED",
                    "An approved frequency exceeds its own safety envelope.",
                    details={"kind": kind, "index": index},
                )

    for field in ("routing_policy", "verification_policy", "output_policy"):
        _mapping(draft[field], field=field)
    if draft["routing_policy"].get("manhattan_only") is not True:
        _fail(
            "NON_MANHATTAN_ROUTING_POLICY_FORBIDDEN",
            "The current drawing contract requires orthogonal Manhattan routing.",
            details={"manhattan_only": draft["routing_policy"].get("manhattan_only")},
        )
    if draft["output_policy"].get("new_output_required") is not True:
        _fail(
            "ADDITIVE_OUTPUT_POLICY_REQUIRED",
            "The workflow may generate only to a new output path.",
            details={
                "new_output_required": draft["output_policy"].get(
                    "new_output_required"
                )
            },
        )
    unresolved = _sequence(draft["unresolved_questions"], field="unresolved_questions")
    for index, question in enumerate(unresolved):
        _string(question, field=f"unresolved_questions[{index}]")

    snapshot = immutable_json_copy(draft)
    return {
        "ok": True,
        "document_kind": "DesignIntentDraft",
        "schema_version": SCHEMA_VERSION,
        "document": snapshot,
        "canonical_sha256": canonical_sha256(snapshot),
        "canonicalization_profile": CANONICALIZATION_PROFILE,
        "device_families": sorted(families),
        "draft_complete": not unresolved,
        "unresolved_question_count": len(unresolved),
        "authorizes_planning": False,
        "authorizes_generation": False,
        "approval_verified": False,
    }


def validate_approved_design_intent_reference(
    document: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate reference shape only; trust verification belongs to M2."""

    reference = _mapping(document, field="ApprovedDesignIntent")
    _require_schema_version(reference, kind="ApprovedDesignIntent")
    _require_keys(
        reference,
        field="ApprovedDesignIntent",
        required=set(APPROVAL_REFERENCE_REQUIRED_FIELDS),
        optional={"expires_at", "revocation_id"},
    )
    for field in ("draft_sha256", "process_capability_sha256"):
        _sha256(reference[field], field=field)
    source_hashes = _mapping(
        reference["source_artifact_sha256s"], field="source_artifact_sha256s"
    )
    if not source_hashes:
        _fail(
            "APPROVAL_SOURCE_ARTIFACTS_REQUIRED",
            "Approval references must bind at least one source artifact hash.",
            details={},
        )
    for role, digest in source_hashes.items():
        _string(role, field="source_artifact_sha256s.role")
        _sha256(digest, field=f"source_artifact_sha256s.{role}")
    for field in (
        "approval_scope",
        "signer_reference",
        "scheme_id",
        "attestation_reference",
        "approved_at",
    ):
        _string(reference[field], field=field)
    output_classes = _sequence(reference["output_classes"], field="output_classes")
    if not output_classes:
        _fail(
            "APPROVAL_OUTPUT_SCOPE_REQUIRED",
            "Approval must name at least one allowed output class.",
            details={},
        )
    for index, value in enumerate(output_classes):
        _string(value, field=f"output_classes[{index}]")

    snapshot = immutable_json_copy(reference)
    return {
        "ok": True,
        "document_kind": "ApprovedDesignIntent",
        "schema_version": SCHEMA_VERSION,
        "document": snapshot,
        "canonical_sha256": canonical_sha256(snapshot),
        "canonicalization_profile": CANONICALIZATION_PROFILE,
        "reference_shape_valid": True,
        "approval_verified": False,
        "authorizes_planning": False,
        "authorizes_generation": False,
        "next_gate": "verify this exact reference with a configured trusted approval backend",
    }


def _validate_job_manifest_fields(manifest: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    _require_schema_version(manifest, kind="JobManifest")
    _require_keys(
        manifest,
        field="JobManifest",
        required={
            "schema_version",
            "job_id",
            "parent_manifest_sha256",
            "design_intent_sha256",
            "approved_intent_sha256",
            "process_capability_sha256",
            "stage",
            "evidence",
            "normalized_inputs",
            "outputs",
            "fingerprints",
            "runtime",
            "warnings",
            "blockers",
            "refusal_codes",
            "created_at",
            "completed_at",
            "atomic_promotion",
        },
    )
    _string(manifest["job_id"], field="job_id")
    for field in ("design_intent_sha256", "process_capability_sha256"):
        _sha256(manifest[field], field=field)
    for optional_hash in ("parent_manifest_sha256", "approved_intent_sha256"):
        if manifest[optional_hash] is not None:
            _sha256(manifest[optional_hash], field=optional_hash)
    stage = _string(manifest["stage"], field="stage")
    if stage not in EVIDENCE_STATES:
        _fail(
            "UNKNOWN_JOB_MANIFEST_STAGE",
            "JobManifest stage must be one evidence-ladder state.",
            details={"stage": stage, "allowed_states": list(EVIDENCE_STATES)},
        )
    evidence = _mapping(manifest["evidence"], field="evidence")
    evidence_report = evaluate_evidence_ladder(evidence)
    if stage not in evidence_report["attained_states"]:
        _fail(
            "JOB_MANIFEST_STAGE_NOT_ATTAINED",
            "JobManifest stage is not supported by its evidence payload.",
            details={
                "stage": stage,
                "highest_attained_state": evidence_report["highest_attained_state"],
            },
        )
    for field in ("normalized_inputs", "fingerprints", "runtime", "atomic_promotion"):
        _mapping(manifest[field], field=field)
    for role, digest in manifest["fingerprints"].items():
        _string(role, field="fingerprints.role")
        _sha256(digest, field=f"fingerprints.{role}")
    outputs = _sequence(manifest["outputs"], field="outputs")
    for index, raw_output in enumerate(outputs):
        output = _mapping(raw_output, field=f"outputs[{index}]")
        _require_keys(
            output,
            field=f"outputs[{index}]",
            required={"role", "content_sha256", "reference"},
        )
        _string(output["role"], field=f"outputs[{index}].role")
        _sha256(output["content_sha256"], field=f"outputs[{index}].content_sha256")
        _string(output["reference"], field=f"outputs[{index}].reference")
    for field in ("warnings", "blockers", "refusal_codes"):
        records = _sequence(manifest[field], field=field)
        for index, value in enumerate(records):
            _string(value, field=f"{field}[{index}]")
    _string(manifest["created_at"], field="created_at")
    if manifest["completed_at"] is not None:
        _string(manifest["completed_at"], field="completed_at")
    return stage, evidence_report


def build_job_manifest(
    document: Mapping[str, Any],
    *,
    parent_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and freeze a resumable content-addressed job manifest."""

    manifest = _mapping(document, field="JobManifest")
    stage, evidence_report = _validate_job_manifest_fields(manifest)
    parent_hash = manifest["parent_manifest_sha256"]
    if parent_hash is None:
        if parent_manifest is not None or stage != "intent_draft_complete":
            _fail(
                "INVALID_JOB_MANIFEST_ROOT",
                "Only the draft-complete stage may start a chain without a parent.",
                details={"stage": stage, "parent_supplied": parent_manifest is not None},
            )
    else:
        if parent_manifest is None:
            _fail(
                "JOB_MANIFEST_PARENT_REQUIRED",
                "A child manifest must include the exact parent snapshot for validation.",
                details={"parent_manifest_sha256": parent_hash},
            )
        parent = _mapping(parent_manifest, field="parent_manifest")
        parent_stage, _parent_evidence = _validate_job_manifest_fields(parent)
        actual_parent_hash = canonical_sha256(parent)
        if actual_parent_hash != parent_hash:
            _fail(
                "JOB_MANIFEST_PARENT_HASH_MISMATCH",
                "The supplied parent snapshot does not match the child back-link.",
                details={"expected": parent_hash, "actual": actual_parent_hash},
            )
        identity_fields = (
            "job_id",
            "design_intent_sha256",
            "process_capability_sha256",
        )
        drift = {
            field: {"parent": parent[field], "child": manifest[field]}
            for field in identity_fields
            if parent[field] != manifest[field]
        }
        parent_approval = parent["approved_intent_sha256"]
        child_approval = manifest["approved_intent_sha256"]
        if parent_approval is not None and child_approval != parent_approval:
            drift["approved_intent_sha256"] = {
                "parent": parent_approval,
                "child": child_approval,
            }
        if drift:
            _fail(
                "JOB_MANIFEST_IDENTITY_DRIFT",
                "A child manifest cannot change job, design, process, or established approval identity.",
                details={"drift": drift},
            )
        parent_index = EVIDENCE_STATES.index(parent_stage)
        child_index = EVIDENCE_STATES.index(stage)
        if child_index not in (parent_index, parent_index + 1):
            _fail(
                "INVALID_JOB_MANIFEST_TRANSITION",
                "A manifest update may remain at its state or advance by one state only.",
                details={"parent_stage": parent_stage, "child_stage": stage},
            )
    if EVIDENCE_STATES.index(stage) >= EVIDENCE_STATES.index("intent_approved") and (
        manifest["approved_intent_sha256"] is None
    ):
        _fail(
            "APPROVED_INTENT_HASH_REQUIRED",
            "Approved and downstream stages must bind an ApprovedDesignIntent hash.",
            details={"stage": stage},
        )

    snapshot = immutable_json_copy(manifest)
    return {
        "ok": True,
        "document_kind": "JobManifest",
        "schema_version": SCHEMA_VERSION,
        "manifest": snapshot,
        "manifest_sha256": canonical_sha256(snapshot),
        "canonicalization_profile": CANONICALIZATION_PROFILE,
        "parent_manifest_sha256": snapshot["parent_manifest_sha256"],
        "stage": stage,
        "evidence_ladder": evidence_report,
        "production_ready": False,
        "stage_claim_evidence_satisfied": True,
        "parent_transition_verified": True,
        "trusted_approval_verified": False,
        "content_addressed": True,
        "mutable_in_place": False,
    }


def validate_measurement_manifest(
    document: Mapping[str, Any],
    *,
    design_intent: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate generic measurement meaning bound to one exact generated layout."""

    manifest = _mapping(document, field="MeasurementManifest")
    _require_schema_version(manifest, kind="MeasurementManifest")
    _require_keys(
        manifest,
        field="MeasurementManifest",
        required=set(MEASUREMENT_MANIFEST_REQUIRED_FIELDS),
        optional={"exporter"},
    )
    intent_result = validate_design_intent_draft(design_intent)
    expected_intent_hash = intent_result["canonical_sha256"]
    actual_intent_hash = _sha256(
        manifest["design_intent_sha256"], field="design_intent_sha256"
    )
    if actual_intent_hash != expected_intent_hash:
        _fail(
            "MEASUREMENT_INTENT_HASH_MISMATCH",
            "MeasurementManifest is stale or bound to a different design intent.",
            details={"expected": expected_intent_hash, "actual": actual_intent_hash},
        )
    _sha256(manifest["generated_layout_sha256"], field="generated_layout_sha256")
    intent = intent_result["document"]
    roles_by_terminal = {
        (contract["dut_id"], terminal["name"]): terminal["electrical_role"]
        for contract in intent["terminal_contracts"]
        for terminal in contract["terminals"]
    }
    expected_map = {
        (record["dut_id"], record["terminal"]): {
            "net": record["net"],
            "pad": record["pad"],
            "electrical_role": roles_by_terminal[(record["dut_id"], record["terminal"])],
        }
        for record in intent["terminal_net_pad_map"]
    }
    pin_map = _sequence(manifest["dut_pin_map"], field="dut_pin_map")
    if not pin_map:
        _fail(
            "MEASUREMENT_PIN_MAP_REQUIRED",
            "MeasurementManifest requires at least one DUT pin mapping.",
            details={},
        )
    seen_pins: set[tuple[str, str]] = set()
    access_by_pad: dict[int, tuple[str, str]] = {}
    pad_by_probe_pin: dict[str, int] = {}
    pad_by_instrument_channel: dict[str, int] = {}
    for index, raw_pin in enumerate(pin_map):
        pin = _mapping(raw_pin, field=f"dut_pin_map[{index}]")
        _require_keys(
            pin,
            field=f"dut_pin_map[{index}]",
            required={
                "dut_id",
                "terminal",
                "net",
                "pad",
                "probe_pin",
                "instrument_channel",
                "electrical_role",
            },
        )
        key = (
            _string(pin["dut_id"], field=f"dut_pin_map[{index}].dut_id"),
            _string(pin["terminal"], field=f"dut_pin_map[{index}].terminal"),
        )
        if key in seen_pins:
            _fail(
                "DUPLICATE_MEASUREMENT_PIN",
                "Every DUT terminal must have one measurement pin mapping.",
                details={"dut_id": key[0], "terminal": key[1]},
            )
        seen_pins.add(key)
        pad = _positive_int(pin["pad"], field=f"dut_pin_map[{index}].pad")
        for field in ("net", "probe_pin", "instrument_channel", "electrical_role"):
            _string(pin[field], field=f"dut_pin_map[{index}].{field}")
        access = (pin["probe_pin"].strip(), pin["instrument_channel"].strip())
        existing_access = access_by_pad.setdefault(pad, access)
        if existing_access != access:
            _fail(
                "MEASUREMENT_PAD_ACCESS_CONFLICT",
                "Terminals sharing one Pad must use the same probe pin and instrument channel.",
                details={
                    "pad": pad,
                    "expected_access": list(existing_access),
                    "actual_access": list(access),
                },
            )
        for label, value, index_map in (
            ("probe_pin", access[0], pad_by_probe_pin),
            ("instrument_channel", access[1], pad_by_instrument_channel),
        ):
            existing_pad = index_map.setdefault(value, pad)
            if existing_pad != pad:
                _fail(
                    "DUPLICATE_MEASUREMENT_ACCESS_CHANNEL",
                    "Different Pads cannot share one probe pin or instrument channel.",
                    details={
                        "field": label,
                        "value": value,
                        "existing_pad": existing_pad,
                        "new_pad": pad,
                    },
                )
        expected = expected_map.get(key)
        actual = {
            "net": pin["net"],
            "pad": pin["pad"],
            "electrical_role": pin["electrical_role"],
        }
        if expected != actual:
            _fail(
                "MEASUREMENT_PIN_INTENT_MISMATCH",
                "A measurement pin does not match the approved terminal/net/Pad meaning.",
                details={"terminal_ref": list(key), "expected": expected, "actual": actual},
            )
    if seen_pins != set(expected_map):
        _fail(
            "MEASUREMENT_PIN_MAP_INCOMPLETE",
            "Measurement pin mapping must exactly cover the design intent terminals.",
            details={
                "missing": sorted(set(expected_map).difference(seen_pins)),
                "unexpected": sorted(seen_pins.difference(expected_map)),
            },
        )

    topology = _mapping(manifest["electrical_topology"], field="electrical_topology")
    _require_keys(
        topology,
        field="electrical_topology",
        required={"type", "connections", "guards"},
        optional={"inactive_terminal_policy"},
    )
    _string(topology["type"], field="electrical_topology.type")
    _sequence(topology["connections"], field="electrical_topology.connections")
    _sequence(topology["guards"], field="electrical_topology.guards")

    terminal_records = list(intent["terminal_net_pad_map"])
    records_by_pad: dict[int, list[Mapping[str, Any]]] = {}
    for record in terminal_records:
        records_by_pad.setdefault(record["pad"], []).append(record)
    shared_pad_groups = {
        pad: records
        for pad, records in records_by_pad.items()
        if len({record["dut_id"] for record in records}) > 1
    }
    inactive_policy = topology.get("inactive_terminal_policy")
    if shared_pad_groups and inactive_policy is None:
        _fail(
            "SHARED_PAD_INACTIVE_TERMINAL_POLICY_REQUIRED",
            "A multi-DUT shared-Pad topology requires explicit active DUTs and inactive terminal states.",
            details={
                "shared_pads": sorted(shared_pad_groups),
                "allowed_states": [
                    "force",
                    "float",
                    "ground",
                    "guard",
                    "follow_shared_pad",
                ],
                "drawing_blocked": False,
                "measurement_program_ready": False,
            },
        )
    inactive_policy_complete = not shared_pad_groups
    active_dut_id_set: set[str] | None = None
    if inactive_policy is not None:
        policy = _mapping(
            inactive_policy,
            field="electrical_topology.inactive_terminal_policy",
        )
        _require_keys(
            policy,
            field="electrical_topology.inactive_terminal_policy",
            required={
                "execution_mode",
                "active_dut_ids",
                "inactive_terminal_states",
            },
        )
        execution_mode = _string(
            policy["execution_mode"],
            field="electrical_topology.inactive_terminal_policy.execution_mode",
        )
        if execution_mode not in {"serial", "simultaneous"}:
            _fail(
                "INVALID_MEASUREMENT_EXECUTION_MODE",
                "Measurement execution mode must be serial or simultaneous.",
                details={"execution_mode": execution_mode},
            )
        active_records = _sequence(
            policy["active_dut_ids"],
            field="electrical_topology.inactive_terminal_policy.active_dut_ids",
        )
        active_dut_ids = [
            _string(
                value,
                field=(
                    "electrical_topology.inactive_terminal_policy."
                    f"active_dut_ids[{index}]"
                ),
            )
            for index, value in enumerate(active_records)
        ]
        if not active_dut_ids or len(set(active_dut_ids)) != len(active_dut_ids):
            _fail(
                "INVALID_ACTIVE_DUT_SELECTION",
                "Active DUT ids must be a non-empty unique list.",
                details={"active_dut_ids": active_dut_ids},
            )
        declared_dut_ids = {device["dut_id"] for device in intent["devices"]}
        active_dut_id_set = set(active_dut_ids)
        unknown_active = sorted(active_dut_id_set.difference(declared_dut_ids))
        if unknown_active:
            _fail(
                "UNKNOWN_ACTIVE_DUT",
                "Every active DUT must be declared by the bound design intent.",
                details={"unknown_active_dut_ids": unknown_active},
            )
        if execution_mode == "serial" and len(active_dut_ids) != 1:
            _fail(
                "SERIAL_MEASUREMENT_REQUIRES_ONE_ACTIVE_DUT",
                "Serial execution requires exactly one active DUT.",
                details={"active_dut_ids": active_dut_ids},
            )
        if execution_mode == "simultaneous" and len(active_dut_ids) < 2:
            _fail(
                "SIMULTANEOUS_MEASUREMENT_REQUIRES_MULTIPLE_ACTIVE_DUTS",
                "Simultaneous execution requires at least two active DUTs.",
                details={"active_dut_ids": active_dut_ids},
            )

        expected_inactive_refs = {
            (dut_id, terminal)
            for (dut_id, terminal) in expected_map
            if dut_id not in active_dut_id_set
        }
        seen_inactive_refs: set[tuple[str, str]] = set()
        state_signatures_by_pad: dict[int, set[tuple[Any, ...]]] = {}
        state_records = _sequence(
            policy["inactive_terminal_states"],
            field=(
                "electrical_topology.inactive_terminal_policy."
                "inactive_terminal_states"
            ),
        )
        for index, raw_state in enumerate(state_records):
            state_record = _mapping(
                raw_state,
                field=(
                    "electrical_topology.inactive_terminal_policy."
                    f"inactive_terminal_states[{index}]"
                ),
            )
            _require_keys(
                state_record,
                field=f"inactive_terminal_states[{index}]",
                required={"dut_id", "terminal", "state"},
                optional={"value", "unit", "reference"},
            )
            terminal_ref = (
                _string(state_record["dut_id"], field=f"inactive[{index}].dut_id"),
                _string(
                    state_record["terminal"],
                    field=f"inactive[{index}].terminal",
                ),
            )
            if terminal_ref not in expected_inactive_refs:
                _fail(
                    "INVALID_INACTIVE_TERMINAL_REFERENCE",
                    "Inactive state records must reference terminals of non-active DUTs.",
                    details={"terminal_ref": list(terminal_ref)},
                )
            if terminal_ref in seen_inactive_refs:
                _fail(
                    "DUPLICATE_INACTIVE_TERMINAL_STATE",
                    "Each inactive DUT terminal requires exactly one state.",
                    details={"terminal_ref": list(terminal_ref)},
                )
            seen_inactive_refs.add(terminal_ref)
            state = _string(state_record["state"], field=f"inactive[{index}].state")
            if state not in {
                "force",
                "float",
                "ground",
                "guard",
                "follow_shared_pad",
            }:
                _fail(
                    "INVALID_INACTIVE_TERMINAL_STATE",
                    "Inactive terminal state must be force, float, ground, guard, or follow_shared_pad.",
                    details={"terminal_ref": list(terminal_ref), "state": state},
                )
            supplied_details = {
                key for key in ("value", "unit", "reference") if key in state_record
            }
            if state == "force":
                if supplied_details != {"value", "unit"}:
                    _fail(
                        "INACTIVE_FORCE_VALUE_REQUIRED",
                        "A forced inactive terminal needs exactly value and unit.",
                        details={
                            "terminal_ref": list(terminal_ref),
                            "supplied_details": sorted(supplied_details),
                        },
                    )
                value = state_record["value"]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    _fail(
                        "INVALID_INACTIVE_FORCE_VALUE",
                        "Forced inactive terminal value must be finite.",
                        details={"terminal_ref": list(terminal_ref), "value": value},
                    )
                _string(state_record["unit"], field=f"inactive[{index}].unit")
                state_signature = (
                    "force",
                    float(state_record["value"]),
                    state_record["unit"].strip(),
                )
            elif state in {"guard", "follow_shared_pad"}:
                if supplied_details != {"reference"}:
                    _fail(
                        "INACTIVE_STATE_REFERENCE_REQUIRED",
                        "Guard and follow_shared_pad states need exactly one reference.",
                        details={
                            "terminal_ref": list(terminal_ref),
                            "state": state,
                            "supplied_details": sorted(supplied_details),
                        },
                    )
                reference = _string(
                    state_record["reference"],
                    field=f"inactive[{index}].reference",
                )
                state_signature = (state, reference)
            elif supplied_details:
                _fail(
                    "UNEXPECTED_INACTIVE_TERMINAL_STATE_DETAILS",
                    "Float and ground states do not accept value, unit, or reference fields.",
                    details={
                        "terminal_ref": list(terminal_ref),
                        "state": state,
                        "supplied_details": sorted(supplied_details),
                    },
                )
            else:
                state_signature = (state,)

            pad = expected_map[terminal_ref]["pad"]
            active_refs_on_pad = {
                ref
                for ref, expected in expected_map.items()
                if expected["pad"] == pad and ref[0] in active_dut_id_set
            }
            if active_refs_on_pad:
                allowed_references = {
                    f"terminal:{dut_id}:{terminal}"
                    for dut_id, terminal in active_refs_on_pad
                }
                if (
                    state != "follow_shared_pad"
                    or state_record.get("reference") not in allowed_references
                ):
                    _fail(
                        "INACTIVE_SHARED_PAD_STATE_CONFLICT",
                        "A terminal physically sharing an active Pad must follow that exact active terminal.",
                        details={
                            "terminal_ref": list(terminal_ref),
                            "pad": pad,
                            "state": state,
                            "reference": state_record.get("reference"),
                            "allowed_references": sorted(allowed_references),
                        },
                    )
            elif state == "follow_shared_pad":
                _fail(
                    "INACTIVE_SHARED_PAD_REFERENCE_NOT_ACTIVE",
                    "follow_shared_pad requires an active terminal on the same physical Pad.",
                    details={"terminal_ref": list(terminal_ref), "pad": pad},
                )
            elif state == "guard":
                reference = state_record["reference"]
                valid_terminal_references = {
                    f"terminal:{dut_id}:{terminal}" for dut_id, terminal in expected_map
                }
                valid_instrument_references = {
                    f"instrument:{pin['instrument_channel']}" for pin in pin_map
                }
                if reference not in (
                    valid_terminal_references | valid_instrument_references
                ):
                    _fail(
                        "INACTIVE_GUARD_REFERENCE_UNKNOWN",
                        "A guard reference must name a declared terminal or mapped instrument channel.",
                        details={
                            "terminal_ref": list(terminal_ref),
                            "reference": reference,
                            "reference_formats": [
                                "terminal:<dut_id>:<terminal>",
                                "instrument:<instrument_channel>",
                            ],
                        },
                    )
            state_signatures_by_pad.setdefault(pad, set()).add(state_signature)
        if seen_inactive_refs != expected_inactive_refs:
            _fail(
                "INACTIVE_TERMINAL_POLICY_INCOMPLETE",
                "Inactive terminal states must exactly cover every terminal of every non-active DUT.",
                details={
                    "missing": sorted(expected_inactive_refs.difference(seen_inactive_refs)),
                    "unexpected": sorted(seen_inactive_refs.difference(expected_inactive_refs)),
                },
            )
        conflicting_inactive_pads = {
            pad: [list(signature) for signature in sorted(signatures, key=repr)]
            for pad, signatures in state_signatures_by_pad.items()
            if len(signatures) > 1
        }
        if conflicting_inactive_pads:
            _fail(
                "INACTIVE_SHARED_PAD_POLICY_CONFLICT",
                "All inactive terminals on one physical Pad must have one compatible state.",
                details={"conflicting_pads": conflicting_inactive_pads},
            )
        inactive_policy_complete = True

    stimuli = _sequence(manifest["stimuli"], field="stimuli")
    observables = _sequence(manifest["observables"], field="observables")
    if not stimuli or not observables:
        _fail(
            "MEASUREMENT_SEMANTICS_INCOMPLETE",
            "MeasurementManifest requires explicit stimuli and observables.",
            details={"stimulus_count": len(stimuli), "observable_count": len(observables)},
        )
    actual_requirement_records: list[dict[str, str | None]] = []
    seen_stimulus_ids: set[str] = set()
    for index, raw_stimulus in enumerate(stimuli):
        stimulus = _mapping(raw_stimulus, field=f"stimuli[{index}]")
        _require_keys(
            stimulus,
            field=f"stimuli[{index}]",
            required={
                "stimulus_id",
                "requirement_kind",
                "requirement_mode",
                "target",
                "source_mode",
                "program",
                "compliance",
                "polarity",
                "frequency_hz",
            },
            optional={"requirement_quantity", "requirement_unit"},
        )
        stimulus_id = _string(
            stimulus["stimulus_id"], field=f"stimuli[{index}].stimulus_id"
        )
        if stimulus_id in seen_stimulus_ids:
            _fail(
                "DUPLICATE_MEASUREMENT_STIMULUS_ID",
                "Every measurement stimulus requires a unique stimulus_id.",
                details={"stimulus_id": stimulus_id},
            )
        seen_stimulus_ids.add(stimulus_id)
        requirement_kind = _string(
            stimulus["requirement_kind"],
            field=f"stimuli[{index}].requirement_kind",
        )
        if requirement_kind not in {"stimulus", "bias"}:
            _fail(
                "INVALID_MEASUREMENT_REQUIREMENT_KIND",
                "A manifest stimulus must bind either a stimulus or bias requirement.",
                details={"stimulus_index": index, "requirement_kind": requirement_kind},
            )
        requirement_mode = _string(
            stimulus["requirement_mode"],
            field=f"stimuli[{index}].requirement_mode",
        )
        requirement_quantity = (
            _string(
                stimulus["requirement_quantity"],
                field=f"stimuli[{index}].requirement_quantity",
            )
            if "requirement_quantity" in stimulus
            else None
        )
        requirement_unit = (
            _string(
                stimulus["requirement_unit"],
                field=f"stimuli[{index}].requirement_unit",
            )
            if "requirement_unit" in stimulus
            else None
        )
        target = _mapping(stimulus["target"], field=f"stimuli[{index}].target")
        _require_keys(
            target,
            field=f"stimuli[{index}].target",
            required={"dut_id", "terminal"},
        )
        target_ref = (
            _string(target["dut_id"], field=f"stimuli[{index}].target.dut_id"),
            _string(target["terminal"], field=f"stimuli[{index}].target.terminal"),
        )
        if target_ref not in expected_map:
            _fail(
                "MEASUREMENT_STIMULUS_TERMINAL_UNKNOWN",
                "A stimulus target is not a declared design terminal.",
                details={"stimulus_index": index, "target": list(target_ref)},
            )
        if active_dut_id_set is not None and target_ref[0] not in active_dut_id_set:
            _fail(
                "MEASUREMENT_TARGETS_INACTIVE_DUT",
                "Stimuli must target DUTs declared active by the inactive-terminal policy.",
                details={"stimulus_index": index, "target": list(target_ref)},
            )
        source_mode = _string(
            stimulus["source_mode"], field=f"stimuli[{index}].source_mode"
        )
        program = _validate_stimulus_program(
            stimulus["program"], field=f"stimuli[{index}].program"
        )
        compliance = _validate_compliance(
            stimulus["compliance"], field=f"stimuli[{index}].compliance"
        )
        polarity = _string(
            stimulus["polarity"], field=f"stimuli[{index}].polarity"
        )
        if stimulus["frequency_hz"] is not None:
            _positive_number(
                stimulus["frequency_hz"], field=f"stimuli[{index}].frequency_hz"
            )
        actual_requirement_records.append(
            {
                "kind": requirement_kind,
                "dut_id": target_ref[0],
                "terminal": target_ref[1],
                "mode": requirement_mode,
                "quantity": requirement_quantity,
                "unit": requirement_unit,
                "execution_sha256": canonical_sha256(
                    {
                        "source_mode": source_mode,
                        "program": program,
                        "compliance": compliance,
                        "polarity": polarity,
                        "frequency_hz": stimulus["frequency_hz"],
                    }
                ),
            }
        )
    seen_observable_labels: set[str] = set()
    for index, raw_observable in enumerate(observables):
        observable = _mapping(raw_observable, field=f"observables[{index}]")
        _require_keys(
            observable,
            field=f"observables[{index}]",
            required={"label", "requirement_mode", "quantity", "unit", "source"},
        )
        normalized_observable = {
            field: _string(observable[field], field=f"observables[{index}].{field}")
            for field in ("label", "requirement_mode", "quantity", "unit")
        }
        if normalized_observable["label"] in seen_observable_labels:
            _fail(
                "DUPLICATE_MEASUREMENT_OBSERVABLE_LABEL",
                "Every measurement observable requires a unique label.",
                details={"label": normalized_observable["label"]},
            )
        seen_observable_labels.add(normalized_observable["label"])
        source = _mapping(observable["source"], field=f"observables[{index}].source")
        _require_keys(
            source,
            field=f"observables[{index}].source",
            required={"dut_id", "terminal"},
        )
        source_ref = (
            _string(source["dut_id"], field=f"observables[{index}].source.dut_id"),
            _string(source["terminal"], field=f"observables[{index}].source.terminal"),
        )
        if source_ref not in expected_map:
            _fail(
                "MEASUREMENT_OBSERVABLE_TERMINAL_UNKNOWN",
                "An observable source is not a declared design terminal.",
                details={"observable_index": index, "source": list(source_ref)},
            )
        if active_dut_id_set is not None and source_ref[0] not in active_dut_id_set:
            _fail(
                "MEASUREMENT_OBSERVES_INACTIVE_DUT",
                "Observables must come from DUTs declared active by the inactive-terminal policy.",
                details={"observable_index": index, "source": list(source_ref)},
            )
        actual_requirement_records.append(
            {
                "kind": "observable",
                "dut_id": source_ref[0],
                "terminal": source_ref[1],
                "mode": normalized_observable["requirement_mode"],
                "quantity": normalized_observable["quantity"],
                "unit": normalized_observable["unit"],
            }
        )

    expected_requirement_records: list[dict[str, str | None]] = []
    intent_requirements = intent["measurement_requirements"]
    for plural_kind, singular_kind in (
        ("stimuli", "stimulus"),
        ("biases", "bias"),
        ("observables", "observable"),
    ):
        for requirement in intent_requirements[plural_kind]:
            expected_requirement_records.append(
                {
                    "kind": singular_kind,
                    "dut_id": requirement["dut_id"].strip(),
                    "terminal": requirement["terminal"].strip(),
                    "mode": requirement["mode"].strip(),
                    "quantity": requirement.get("quantity", None),
                    "unit": requirement.get("unit", None),
                    "execution_sha256": (
                        canonical_sha256(
                            {
                                "source_mode": requirement["source_mode"],
                                "program": requirement["program"],
                                "compliance": requirement["compliance"],
                                "polarity": requirement["polarity"],
                                "frequency_hz": requirement["frequency_hz"],
                            }
                        )
                        if singular_kind in {"stimulus", "bias"}
                        else None
                    ),
                }
            )

    def _base(record: Mapping[str, Any]) -> tuple[str, str, str, str, str | None]:
        return (
            str(record["kind"]),
            str(record["dut_id"]),
            str(record["terminal"]),
            str(record["mode"]),
            record.get("execution_sha256"),
        )

    def _identity(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(record["kind"]),
            str(record["dut_id"]),
            str(record["terminal"]),
            str(record["mode"]),
        )

    expected_identity_counts: dict[tuple[str, str, str, str], int] = {}
    actual_identity_counts: dict[tuple[str, str, str, str], int] = {}
    for record in expected_requirement_records:
        expected_identity_counts[_identity(record)] = (
            expected_identity_counts.get(_identity(record), 0) + 1
        )
    for record in actual_requirement_records:
        actual_identity_counts[_identity(record)] = (
            actual_identity_counts.get(_identity(record), 0) + 1
        )
    if expected_identity_counts != actual_identity_counts:
        _fail(
            "MEASUREMENT_REQUIREMENT_COVERAGE_MISMATCH",
            "MeasurementManifest must cover every DesignIntent requirement exactly once and add none.",
            details={
                "expected": expected_requirement_records,
                "actual": actual_requirement_records,
            },
        )
    expected_base_counts: dict[tuple[str, str, str, str, str | None], int] = {}
    actual_base_counts: dict[tuple[str, str, str, str, str | None], int] = {}
    for record in expected_requirement_records:
        expected_base_counts[_base(record)] = expected_base_counts.get(_base(record), 0) + 1
    for record in actual_requirement_records:
        actual_base_counts[_base(record)] = actual_base_counts.get(_base(record), 0) + 1
    if expected_base_counts != actual_base_counts:
        _fail(
            "MEASUREMENT_EXECUTION_INTENT_MISMATCH",
            "Actual source program, compliance, polarity, or frequency differs from DesignIntent.",
            details={
                "expected": expected_requirement_records,
                "actual": actual_requirement_records,
            },
        )

    for base in expected_base_counts:
        unmatched_actual = [
            record for record in actual_requirement_records if _base(record) == base
        ]
        expected_group = sorted(
            (record for record in expected_requirement_records if _base(record) == base),
            key=lambda record: sum(record[field] is not None for field in ("quantity", "unit")),
            reverse=True,
        )
        for expected in expected_group:
            match_index = next(
                (
                    index
                    for index, actual in enumerate(unmatched_actual)
                    if all(
                        expected[field] is None or expected[field] == actual[field]
                        for field in ("quantity", "unit")
                    )
                ),
                None,
            )
            if match_index is None:
                _fail(
                    "MEASUREMENT_REQUIREMENT_SEMANTIC_MISMATCH",
                    "MeasurementManifest quantity or unit differs from DesignIntent.",
                    details={
                        "expected": expected,
                        "actual_candidates": unmatched_actual,
                    },
                )
            unmatched_actual.pop(match_index)

    stimuli_by_pad: dict[int, list[Mapping[str, Any]]] = {}
    for stimulus in stimuli:
        target = stimulus["target"]
        target_ref = (target["dut_id"], target["terminal"])
        stimuli_by_pad.setdefault(expected_map[target_ref]["pad"], []).append(stimulus)
    conflicting_active_stimuli: dict[int, list[str]] = {}
    for pad, pad_stimuli in stimuli_by_pad.items():
        signatures = {
            canonical_sha256(
                {
                    "source_mode": stimulus["source_mode"],
                    "program": stimulus["program"],
                    "compliance": stimulus["compliance"],
                    "polarity": stimulus["polarity"],
                    "frequency_hz": stimulus["frequency_hz"],
                }
            )
            for stimulus in pad_stimuli
        }
        if len(signatures) > 1:
            conflicting_active_stimuli[pad] = [
                stimulus["stimulus_id"] for stimulus in pad_stimuli
            ]
    if conflicting_active_stimuli:
        _fail(
            "ACTIVE_SHARED_PAD_STIMULUS_CONFLICT",
            "One physical Pad cannot receive incompatible simultaneous stimulus programs.",
            details={"conflicting_pads": conflicting_active_stimuli},
        )

    timing = _validate_measurement_timing(manifest["timing"], field="timing")
    environment = _mapping(manifest["environment"], field="environment")
    approved_measurement = intent["measurement_requirements"]
    if canonical_sha256(timing) != canonical_sha256(approved_measurement["timing"]):
        _fail(
            "MEASUREMENT_TIMING_INTENT_MISMATCH",
            "Measurement timing differs from the approved DesignIntent.",
            details={},
        )
    if canonical_sha256(environment) != canonical_sha256(
        approved_measurement["environment"]
    ):
        _fail(
            "MEASUREMENT_ENVIRONMENT_INTENT_MISMATCH",
            "Measurement environment differs from the approved DesignIntent.",
            details={},
        )

    safety = _validate_safety_envelope(
        manifest["safety_envelope"], field="safety_envelope"
    )
    if canonical_sha256(safety) != canonical_sha256(
        approved_measurement["safety_envelope"]
    ):
        _fail(
            "MEASUREMENT_SAFETY_INTENT_MISMATCH",
            "Measurement safety envelope cannot relax or replace the approved DesignIntent.",
            details={},
        )
    safety_limits = _mapping(safety["limits"], field="safety_envelope.limits")
    recognized_limits: dict[str, float] = {}
    for canonical, aliases in {
        "voltage": ("max_abs_voltage_v", "max_voltage_v"),
        "current": ("max_abs_current_a", "max_current_a"),
        "frequency": ("max_frequency_hz",),
    }.items():
        supplied = [alias for alias in aliases if alias in safety_limits]
        if len(supplied) > 1:
            _fail(
                "AMBIGUOUS_MEASUREMENT_SAFETY_LIMIT",
                "Use only one canonical alias for each safety quantity.",
                details={"quantity": canonical, "fields": supplied},
            )
        if supplied:
            recognized_limits[canonical] = _positive_number(
                safety_limits[supplied[0]],
                field=f"safety_envelope.limits.{supplied[0]}",
            )
    if inactive_policy is not None:
        for index, state in enumerate(inactive_policy["inactive_terminal_states"]):
            if state["state"] not in {"force", "guard"} or "value" not in state:
                continue
            unit = str(state.get("unit", "")).strip()
            quantity = "voltage" if unit == "V" else "current" if unit == "A" else None
            if quantity is None:
                _fail(
                    "INACTIVE_TERMINAL_BIAS_UNIT_UNSUPPORTED",
                    "Inactive force/guard bias must use canonical V or A units.",
                    details={"index": index, "unit": unit},
                )
            if (
                quantity in recognized_limits
                and abs(float(state["value"])) > recognized_limits[quantity]
            ):
                _fail(
                    "INACTIVE_TERMINAL_SAFETY_LIMIT_EXCEEDED",
                    "An inactive terminal force/guard bias exceeds approved safety.",
                    details={
                        "index": index,
                        "quantity": quantity,
                        "value": state["value"],
                        "safety_limit": recognized_limits[quantity],
                    },
                )
    for index, stimulus in enumerate(stimuli):
        source_mode = str(stimulus["source_mode"]).lower()
        quantity = (
            "voltage" if "voltage" in source_mode else
            "current" if "current" in source_mode else None
        )
        if quantity in recognized_limits:
            program = stimulus["program"]
            numeric_values = [
                abs(float(value))
                for key, value in program.items()
                if key in {"value", "amplitude", "start", "stop", "maximum"}
                and not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
            ]
            if numeric_values and max(numeric_values) > recognized_limits[quantity]:
                _fail(
                    "MEASUREMENT_SAFETY_LIMIT_EXCEEDED",
                    "A stimulus program exceeds its declared safety envelope.",
                    details={
                        "stimulus_index": index,
                        "quantity": quantity,
                        "program_peak": max(numeric_values),
                        "safety_limit": recognized_limits[quantity],
                    },
                )
        compliance = stimulus["compliance"]
        compliance_quantity = str(compliance["quantity"]).lower()
        if (
            compliance_quantity in recognized_limits
            and float(compliance["limit"]) > recognized_limits[compliance_quantity]
        ):
            _fail(
                "MEASUREMENT_SAFETY_LIMIT_EXCEEDED",
                "A compliance setting exceeds its declared safety envelope.",
                details={
                    "stimulus_index": index,
                    "quantity": compliance_quantity,
                    "compliance_limit": compliance["limit"],
                    "safety_limit": recognized_limits[compliance_quantity],
                },
            )
        if (
            stimulus["frequency_hz"] is not None
            and "frequency" in recognized_limits
            and float(stimulus["frequency_hz"]) > recognized_limits["frequency"]
        ):
            _fail(
                "MEASUREMENT_SAFETY_LIMIT_EXCEEDED",
                "A stimulus frequency exceeds its declared safety envelope.",
                details={
                    "stimulus_index": index,
                    "frequency_hz": stimulus["frequency_hz"],
                    "safety_limit_hz": recognized_limits["frequency"],
                },
            )

    calibration = _mapping(
        manifest["calibration_and_deembedding"], field="calibration_and_deembedding"
    )
    _require_keys(
        calibration,
        field="calibration_and_deembedding",
        required={"required", "calibration_plane", "reference_duts"},
    )
    if not isinstance(calibration["required"], bool):
        _fail(
            "INVALID_CALIBRATION_REQUIREMENT",
            "calibration_and_deembedding.required must be boolean.",
            details={"value": calibration["required"]},
        )
    _string(calibration["calibration_plane"], field="calibration_and_deembedding.calibration_plane")
    references = _sequence(
        calibration["reference_duts"],
        field="calibration_and_deembedding.reference_duts",
    )
    if calibration["required"] and not references:
        _fail(
            "CALIBRATION_REFERENCE_REQUIRED",
            "Required calibration/de-embedding needs explicit reference DUTs.",
            details={},
        )
    if "exporter" in manifest:
        exporter = _mapping(manifest["exporter"], field="exporter")
        _require_keys(
            exporter,
            field="exporter",
            required={"name", "version", "output_sha256"},
        )
        _string(exporter["name"], field="exporter.name")
        _string(exporter["version"], field="exporter.version")
        _sha256(exporter["output_sha256"], field="exporter.output_sha256")

    snapshot = immutable_json_copy(manifest)
    return {
        "ok": True,
        "document_kind": "MeasurementManifest",
        "schema_version": SCHEMA_VERSION,
        "document": snapshot,
        "canonical_sha256": canonical_sha256(snapshot),
        "canonicalization_profile": CANONICALIZATION_PROFILE,
        "generated_layout_sha256": snapshot["generated_layout_sha256"],
        "schema_valid": True,
        "intent_binding_verified": True,
        "layout_hash_reference_valid": True,
        "inactive_terminal_policy_complete": inactive_policy_complete,
        "measurement_manifest_verified": False,
        "next_gate": "recompute the generated layout file SHA-256 and compare it before state promotion",
        "instrument_commands_generated": False,
        "production_ready": False,
    }
