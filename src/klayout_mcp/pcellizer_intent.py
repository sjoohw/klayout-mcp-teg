"""Fail-closed parameter intent contract for non-destructive PCellizer authoring.

This module contains no ``pya`` dependency and runs purely in Python.
Parameter intent records bind snapshot identity, safe parameter names,
strictly-quantized dimensional bounds on the manufacturing grid and DBU,
explicit dimension semantics, anchor policy, and dependency policy.
"""

from __future__ import annotations

import keyword
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .errors import AnalysisError
from .workflow_manifest import (
    SHA256_PATTERN,
    canonical_sha256,
    immutable_json_copy,
)


PCELLIZER_INTENT_SCHEMA_VERSION = 1
INTENT_KIND = "PCellizerParameterIntent"

ACCEPTED_DIMENSION_SEMANTICS = ("transverse_width", "longitudinal_length")
ACCEPTED_ANCHOR_POLICIES = ("p1_fixed", "p2_fixed", "center_fixed")
ACCEPTED_DEPENDENCY_POLICIES = ("fixed_unselected_geometry",)
DEPENDENCY_POLICY_FIXED_UNSELECTED_GEOMETRY = "fixed_unselected_geometry"
RESERVED_KLAYOUT_PCELL_PARAMETER_NAMES = frozenset(
    {"layout", "cell", "shape", "layer", "parameters"}
)


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action="Correct the parameter intent and recompute its canonical SHA-256.",
    )


def _validate_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        _fail(
            "INVALID_PCELLIZER_INTENT_HASH",
            f"{field} must be a lowercase SHA-256 hex digest.",
            field=field,
            value=value,
        )
    val = value.strip()
    if not SHA256_PATTERN.fullmatch(val):
        _fail(
            "INVALID_PCELLIZER_INTENT_HASH",
            f"{field} must be a 64-character lowercase hexadecimal string.",
            field=field,
            value=value,
        )
    return val


def _validate_parameter_name(name: Any) -> str:
    if not isinstance(name, str) or isinstance(name, bool) or not name:
        _fail(
            "INVALID_PCELLIZER_PARAMETER_NAME",
            "parameter_name must be a non-empty string.",
            parameter_name=name,
        )
    if not name.isidentifier():
        _fail(
            "INVALID_PCELLIZER_PARAMETER_NAME",
            f"parameter_name {name!r} is not a valid Python identifier.",
            parameter_name=name,
        )
    if keyword.iskeyword(name):
        _fail(
            "RESERVED_PCELLIZER_PARAMETER_NAME",
            f"parameter_name {name!r} is a reserved Python keyword.",
            parameter_name=name,
        )
    if name in RESERVED_KLAYOUT_PCELL_PARAMETER_NAMES:
        _fail(
            "RESERVED_KLAYOUT_PCELL_PARAMETER_NAME",
            f"parameter_name {name!r} collides with a KLayout PCell helper member.",
            parameter_name=name,
            reserved_names=sorted(RESERVED_KLAYOUT_PCELL_PARAMETER_NAMES),
        )
    return name


def _validate_positive_float(value: Any, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        _fail(
            "INVALID_PCELLIZER_INTENT_NUMERIC",
            f"{field} must be a finite positive number.",
            field=field,
            value=value,
        )
    return float(value)


def _is_integer_multiple(value: Any, base: Any, *, allow_zero: bool = False) -> bool:
    """Return exact decimal quantization, never a scale-dependent float tolerance."""

    try:
        decimal_value = Decimal(str(value))
        decimal_base = Decimal(str(base))
        ratio = decimal_value / decimal_base
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return False
    minimum = 0 if allow_zero else 1
    return (
        decimal_value.is_finite()
        and decimal_base.is_finite()
        and decimal_base > 0
        and ratio == ratio.to_integral_value()
        and ratio >= minimum
    )


def _validate_numeric_grid_and_bounds(
    *,
    min_um: float,
    nominal_um: float,
    max_um: float,
    step_um: float,
    dbu_um: float,
    manufacturing_grid_um: float,
) -> None:
    if not (min_um <= nominal_um <= max_um):
        _fail(
            "INVALID_PCELLIZER_PARAMETER_BOUNDS",
            f"Parameter bounds must satisfy min_um ({min_um}) <= nominal_um ({nominal_um}) <= max_um ({max_um}).",
            min_um=min_um,
            nominal_um=nominal_um,
            max_um=max_um,
        )

    if not _is_integer_multiple(manufacturing_grid_um, dbu_um):
        _fail(
            "PCELLIZER_GRID_NOT_ON_DBU",
            f"manufacturing_grid_um ({manufacturing_grid_um}) must be an exact integer multiple of dbu_um ({dbu_um}).",
            manufacturing_grid_um=manufacturing_grid_um,
            dbu_um=dbu_um,
        )

    if not _is_integer_multiple(step_um, dbu_um):
        _fail(
            "PCELLIZER_STEP_NOT_ON_DBU",
            f"step_um ({step_um}) must be an exact integer multiple of dbu_um ({dbu_um}).",
            step_um=step_um,
            dbu_um=dbu_um,
        )
    if not _is_integer_multiple(step_um, manufacturing_grid_um):
        _fail(
            "PCELLIZER_STEP_OFF_GRID",
            "step_um must be an exact manufacturing-grid multiple so every generated value stays legal.",
            step_um=step_um,
            manufacturing_grid_um=manufacturing_grid_um,
        )

    for bound_name, bound_val in (
        ("min_um", min_um),
        ("nominal_um", nominal_um),
        ("max_um", max_um),
    ):
        if not _is_integer_multiple(bound_val, dbu_um):
            _fail(
                "PCELLIZER_BOUND_NOT_ON_DBU",
                f"{bound_name} ({bound_val}) must be an exact integer multiple of dbu_um ({dbu_um}).",
                field=bound_name,
                value=bound_val,
                dbu_um=dbu_um,
            )
        if not _is_integer_multiple(bound_val, manufacturing_grid_um):
            _fail(
                "PCELLIZER_BOUND_OFF_GRID",
                f"{bound_name} ({bound_val}) must align to manufacturing_grid_um ({manufacturing_grid_um}).",
                field=bound_name,
                value=bound_val,
                manufacturing_grid_um=manufacturing_grid_um,
            )
    decimal_min = Decimal(str(min_um))
    decimal_nominal = Decimal(str(nominal_um))
    decimal_max = Decimal(str(max_um))
    for delta_name, delta in (
        ("nominal_minus_min_um", decimal_nominal - decimal_min),
        ("max_minus_nominal_um", decimal_max - decimal_nominal),
    ):
        if not _is_integer_multiple(delta, step_um, allow_zero=True):
            _fail(
                "PCELLIZER_BOUNDS_OFF_STEP_LATTICE",
                "min, nominal, and max must lie on one exact step lattice.",
                field=delta_name,
                delta_um=delta,
                step_um=step_um,
            )


def _validate_dimension_semantics(value: Any) -> str:
    if value not in ACCEPTED_DIMENSION_SEMANTICS:
        _fail(
            "INVALID_PCELLIZER_DIMENSION_SEMANTICS",
            f"dimension_semantics must be one of {list(ACCEPTED_DIMENSION_SEMANTICS)}, received {value!r}.",
            dimension_semantics=value,
            accepted_semantics=list(ACCEPTED_DIMENSION_SEMANTICS),
        )
    return value


def _validate_anchor_policy(value: Any) -> str:
    if value not in ACCEPTED_ANCHOR_POLICIES:
        _fail(
            "INVALID_PCELLIZER_ANCHOR_POLICY",
            f"anchor_policy must be one of {list(ACCEPTED_ANCHOR_POLICIES)}, received {value!r}.",
            anchor_policy=value,
            accepted_anchor_policies=list(ACCEPTED_ANCHOR_POLICIES),
        )
    return value


def _validate_dependency_policy(value: Any) -> str:
    if value != DEPENDENCY_POLICY_FIXED_UNSELECTED_GEOMETRY:
        _fail(
            "UNSUPPORTED_PCELLIZER_DEPENDENCY_POLICY",
            f"dependency_policy must be '{DEPENDENCY_POLICY_FIXED_UNSELECTED_GEOMETRY}', received {value!r}.",
            dependency_policy=value,
            accepted_dependency_policies=list(ACCEPTED_DEPENDENCY_POLICIES),
        )
    return value


def build_pcellizer_parameter_intent(
    snapshot_package_sha256: str,
    parameter_name: str,
    min_um: float,
    nominal_um: float,
    max_um: float,
    step_um: float,
    dbu_um: float,
    manufacturing_grid_um: float,
    dimension_semantics: str,
    anchor_policy: str,
    dependency_policy: str = DEPENDENCY_POLICY_FIXED_UNSELECTED_GEOMETRY,
) -> dict[str, Any]:
    """Construct and validate an immutable PCellizer parameter intent contract."""
    validated_snapshot = _validate_sha256(
        snapshot_package_sha256, field="snapshot_package_sha256"
    )
    validated_name = _validate_parameter_name(parameter_name)

    v_dbu = _validate_positive_float(dbu_um, field="dbu_um")
    v_grid = _validate_positive_float(
        manufacturing_grid_um, field="manufacturing_grid_um"
    )
    v_step = _validate_positive_float(step_um, field="step_um")
    v_min = _validate_positive_float(min_um, field="min_um")
    v_nom = _validate_positive_float(nominal_um, field="nominal_um")
    v_max = _validate_positive_float(max_um, field="max_um")

    _validate_numeric_grid_and_bounds(
        min_um=v_min,
        nominal_um=v_nom,
        max_um=v_max,
        step_um=v_step,
        dbu_um=v_dbu,
        manufacturing_grid_um=v_grid,
    )

    validated_semantics = _validate_dimension_semantics(dimension_semantics)
    validated_anchor = _validate_anchor_policy(anchor_policy)
    validated_dep = _validate_dependency_policy(dependency_policy)

    core_doc = {
        "schema_version": PCELLIZER_INTENT_SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "snapshot_package_sha256": validated_snapshot,
        "parameter_name": validated_name,
        "min_um": v_min,
        "nominal_um": v_nom,
        "max_um": v_max,
        "step_um": v_step,
        "dbu_um": v_dbu,
        "manufacturing_grid_um": v_grid,
        "dimension_semantics": validated_semantics,
        "anchor_policy": validated_anchor,
        "dependency_policy": validated_dep,
        "production_ready": False,
    }

    intent_hash = canonical_sha256(core_doc)
    doc_with_hash = {
        **core_doc,
        "parameter_intent_sha256": intent_hash,
    }
    return immutable_json_copy(doc_with_hash)


def validate_pcellizer_parameter_intent(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one parameter intent record, verify content hash, and return an immutable copy."""
    if not isinstance(intent, Mapping):
        _fail(
            "INVALID_PCELLIZER_INTENT",
            "Parameter intent must be an object/mapping.",
            received_type=type(intent).__name__,
        )
    document = immutable_json_copy(intent)

    schema_version = document.get("schema_version")
    if schema_version != PCELLIZER_INTENT_SCHEMA_VERSION:
        _fail(
            "UNSUPPORTED_PCELLIZER_SCHEMA_VERSION",
            f"Parameter intent schema version is not supported: {schema_version}.",
            schema_version=schema_version,
            supported_schema_versions=[PCELLIZER_INTENT_SCHEMA_VERSION],
        )

    kind = document.get("kind")
    if kind != INTENT_KIND:
        _fail(
            "INVALID_PCELLIZER_INTENT",
            f"Parameter intent kind must be '{INTENT_KIND}', received {kind!r}.",
            kind=kind,
        )

    production_ready = document.get("production_ready")
    if production_ready is not False:
        _fail(
            "UNSUPPORTED_PCELLIZER_PRODUCTION_READY",
            "Parameter intent production_ready must be False.",
            production_ready=production_ready,
        )

    recorded_hash = document.pop("parameter_intent_sha256", None)
    if recorded_hash is None:
        _fail(
            "INVALID_PCELLIZER_INTENT_HASH",
            "Parameter intent is missing parameter_intent_sha256.",
        )
    validated_recorded_hash = _validate_sha256(
        recorded_hash, field="parameter_intent_sha256"
    )

    required_keys = {
        "schema_version",
        "kind",
        "snapshot_package_sha256",
        "parameter_name",
        "min_um",
        "nominal_um",
        "max_um",
        "step_um",
        "dbu_um",
        "manufacturing_grid_um",
        "dimension_semantics",
        "anchor_policy",
        "dependency_policy",
        "production_ready",
    }
    missing_keys = sorted(required_keys.difference(document))
    unexpected_keys = sorted(set(document).difference(required_keys))
    if missing_keys or unexpected_keys:
        _fail(
            "INVALID_PCELLIZER_INTENT_SCHEMA",
            "Parameter intent document keys do not match schema.",
            missing_keys=missing_keys,
            unexpected_keys=unexpected_keys,
        )
    raw_expected_hash = canonical_sha256(document)
    if validated_recorded_hash != raw_expected_hash:
        _fail(
            "PCELLIZER_INTENT_HASH_MISMATCH",
            "Parameter intent content changed after its SHA-256 identity was created.",
            expected_sha256=raw_expected_hash,
            actual_sha256=validated_recorded_hash,
        )

    snapshot_package_sha256 = _validate_sha256(
        document["snapshot_package_sha256"], field="snapshot_package_sha256"
    )
    parameter_name = _validate_parameter_name(document["parameter_name"])

    dbu_um = _validate_positive_float(document["dbu_um"], field="dbu_um")
    manufacturing_grid_um = _validate_positive_float(
        document["manufacturing_grid_um"], field="manufacturing_grid_um"
    )
    step_um = _validate_positive_float(document["step_um"], field="step_um")
    min_um = _validate_positive_float(document["min_um"], field="min_um")
    nominal_um = _validate_positive_float(document["nominal_um"], field="nominal_um")
    max_um = _validate_positive_float(document["max_um"], field="max_um")

    _validate_numeric_grid_and_bounds(
        min_um=min_um,
        nominal_um=nominal_um,
        max_um=max_um,
        step_um=step_um,
        dbu_um=dbu_um,
        manufacturing_grid_um=manufacturing_grid_um,
    )

    dimension_semantics = _validate_dimension_semantics(document["dimension_semantics"])
    anchor_policy = _validate_anchor_policy(document["anchor_policy"])
    dependency_policy = _validate_dependency_policy(document["dependency_policy"])

    normalized_core = {
        "schema_version": PCELLIZER_INTENT_SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "snapshot_package_sha256": snapshot_package_sha256,
        "parameter_name": parameter_name,
        "min_um": min_um,
        "nominal_um": nominal_um,
        "max_um": max_um,
        "step_um": step_um,
        "dbu_um": dbu_um,
        "manufacturing_grid_um": manufacturing_grid_um,
        "dimension_semantics": dimension_semantics,
        "anchor_policy": anchor_policy,
        "dependency_policy": dependency_policy,
        "production_ready": False,
    }

    expected_hash = canonical_sha256(normalized_core)
    if validated_recorded_hash != expected_hash:
        _fail(
            "PCELLIZER_INTENT_HASH_MISMATCH",
            "Parameter intent content does not match its SHA-256 identity hash.",
            expected_sha256=expected_hash,
            actual_sha256=validated_recorded_hash,
        )

    normalized_doc = {
        **normalized_core,
        "parameter_intent_sha256": validated_recorded_hash,
    }
    return immutable_json_copy(normalized_doc)
