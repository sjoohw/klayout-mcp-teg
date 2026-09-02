"""Tests for pure-Python fail-closed PCellizer parameter intent contracts."""

import hashlib
import math
import sys
from typing import Any

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_intent import (
    ACCEPTED_ANCHOR_POLICIES,
    ACCEPTED_DEPENDENCY_POLICIES,
    ACCEPTED_DIMENSION_SEMANTICS,
    DEPENDENCY_POLICY_FIXED_UNSELECTED_GEOMETRY,
    INTENT_KIND,
    PCELLIZER_INTENT_SCHEMA_VERSION,
    build_pcellizer_parameter_intent,
    validate_pcellizer_parameter_intent,
)
from klayout_mcp.workflow_manifest import canonical_sha256


VALID_SNAPSHOT_SHA256 = "a" * 64


def _valid_intent_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "snapshot_package_sha256": VALID_SNAPSHOT_SHA256,
        "parameter_name": "gate_width",
        "min_um": 0.050,
        "nominal_um": 0.100,
        "max_um": 0.500,
        "step_um": 0.010,
        "dbu_um": 0.001,
        "manufacturing_grid_um": 0.005,
        "dimension_semantics": "transverse_width",
        "anchor_policy": "center_fixed",
        "dependency_policy": "fixed_unselected_geometry",
    }
    base.update(overrides)
    return base


def test_build_valid_parameter_intent_and_validate() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())

    assert intent["schema_version"] == PCELLIZER_INTENT_SCHEMA_VERSION
    assert intent["kind"] == INTENT_KIND
    assert intent["snapshot_package_sha256"] == VALID_SNAPSHOT_SHA256
    assert intent["parameter_name"] == "gate_width"
    assert intent["min_um"] == 0.050
    assert intent["nominal_um"] == 0.100
    assert intent["max_um"] == 0.500
    assert intent["step_um"] == 0.010
    assert intent["dbu_um"] == 0.001
    assert intent["manufacturing_grid_um"] == 0.005
    assert intent["dimension_semantics"] == "transverse_width"
    assert intent["anchor_policy"] == "center_fixed"
    assert intent["dependency_policy"] == "fixed_unselected_geometry"
    assert intent["production_ready"] is False
    assert "parameter_intent_sha256" in intent

    # Re-validating the built intent passes and returns an equivalent immutable copy
    validated = validate_pcellizer_parameter_intent(intent)
    assert validated == intent


def test_pure_python_no_pya_dependency() -> None:
    assert "klayout.db" not in sys.modules
    assert "pya" not in sys.modules


@pytest.mark.parametrize(
    "semantics,anchor",
    [
        ("transverse_width", "p1_fixed"),
        ("transverse_width", "p2_fixed"),
        ("transverse_width", "center_fixed"),
        ("longitudinal_length", "p1_fixed"),
        ("longitudinal_length", "p2_fixed"),
        ("longitudinal_length", "center_fixed"),
    ],
)
def test_all_accepted_semantics_and_anchor_combinations(semantics: str, anchor: str) -> None:
    intent = build_pcellizer_parameter_intent(
        **_valid_intent_kwargs(dimension_semantics=semantics, anchor_policy=anchor)
    )
    validated = validate_pcellizer_parameter_intent(intent)
    assert validated["dimension_semantics"] == semantics
    assert validated["anchor_policy"] == anchor


def test_default_dependency_policy_is_fixed_unselected_geometry() -> None:
    kwargs = _valid_intent_kwargs()
    kwargs.pop("dependency_policy")
    intent = build_pcellizer_parameter_intent(**kwargs)
    assert intent["dependency_policy"] == DEPENDENCY_POLICY_FIXED_UNSELECTED_GEOMETRY


def test_positional_arguments_supported() -> None:
    intent = build_pcellizer_parameter_intent(
        VALID_SNAPSHOT_SHA256,
        "transistor_l",
        0.040,
        0.050,
        0.200,
        0.005,
        0.001,
        0.005,
        "longitudinal_length",
        "p1_fixed",
    )
    assert intent["parameter_name"] == "transistor_l"
    assert intent["dimension_semantics"] == "longitudinal_length"
    assert intent["anchor_policy"] == "p1_fixed"
    assert intent["dependency_policy"] == "fixed_unselected_geometry"


def test_equal_min_nominal_max_bounds_supported() -> None:
    intent = build_pcellizer_parameter_intent(
        **_valid_intent_kwargs(min_um=0.100, nominal_um=0.100, max_um=0.100)
    )
    validated = validate_pcellizer_parameter_intent(intent)
    assert validated["min_um"] == 0.100
    assert validated["nominal_um"] == 0.100
    assert validated["max_um"] == 0.100


def test_deterministic_hashing() -> None:
    first = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    second = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    assert first["parameter_intent_sha256"] == second["parameter_intent_sha256"]

    # Integral float and int equivalence in canonical hashing
    third = build_pcellizer_parameter_intent(
        **_valid_intent_kwargs(min_um=1.0, nominal_um=2.0, max_um=3.0, step_um=1.0)
    )
    fourth = build_pcellizer_parameter_intent(
        **_valid_intent_kwargs(min_um=1, nominal_um=2, max_um=3, step_um=1)
    )
    assert third["parameter_intent_sha256"] == fourth["parameter_intent_sha256"]


def test_tampering_bounds_detected_and_rejected() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    tampered = dict(intent)
    tampered["min_um"] = 0.055  # modified value without updating hash

    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(tampered)
    assert exc_info.value.code == "PCELLIZER_INTENT_HASH_MISMATCH"


def test_tampering_parameter_name_rejected() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    tampered = dict(intent)
    tampered["parameter_name"] = "tampered_name"

    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(tampered)
    assert exc_info.value.code == "PCELLIZER_INTENT_HASH_MISMATCH"


def test_tampering_production_ready_rejected() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    tampered = dict(intent)
    tampered["production_ready"] = True

    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(tampered)
    assert exc_info.value.code == "UNSUPPORTED_PCELLIZER_PRODUCTION_READY"


def test_tampering_extra_key_rejected() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    tampered = dict(intent)
    tampered["injected_field"] = "malicious_payload"

    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(tampered)
    assert exc_info.value.code in ("INVALID_PCELLIZER_INTENT_SCHEMA", "PCELLIZER_INTENT_HASH_MISMATCH")


def test_missing_or_invalid_intent_hash() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())

    no_hash = dict(intent)
    no_hash.pop("parameter_intent_sha256")
    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(no_hash)
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT_HASH"

    bad_hash = dict(intent)
    bad_hash["parameter_intent_sha256"] = "NOT_A_VALID_HEX"
    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(bad_hash)
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT_HASH"

    uppercase_hash = dict(intent)
    uppercase_hash["parameter_intent_sha256"] = intent["parameter_intent_sha256"].upper()
    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(uppercase_hash)
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT_HASH"


@pytest.mark.parametrize(
    "keyword_name",
    [
        "class",
        "def",
        "import",
        "from",
        "for",
        "while",
        "if",
        "else",
        "elif",
        "try",
        "except",
        "finally",
        "raise",
        "return",
        "pass",
        "break",
        "continue",
        "lambda",
        "with",
        "as",
        "global",
        "nonlocal",
        "assert",
        "del",
        "yield",
        "None",
        "True",
        "False",
        "and",
        "or",
        "not",
        "is",
        "in",
        "async",
        "await",
    ],
)
def test_reserved_python_keywords_rejected(keyword_name: str) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(**_valid_intent_kwargs(parameter_name=keyword_name))
    assert exc_info.value.code == "RESERVED_PCELLIZER_PARAMETER_NAME"


@pytest.mark.parametrize("reserved_name", ["layout", "cell", "shape", "layer", "parameters"])
def test_klayout_pcell_helper_names_rejected(reserved_name: str) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(parameter_name=reserved_name)
        )
    assert exc_info.value.code == "RESERVED_KLAYOUT_PCELL_PARAMETER_NAME"


@pytest.mark.parametrize(
    "invalid_name",
    [
        "1gate",
        "2_width",
        "gate-width",
        "gate width",
        "width.um",
        "width$um",
        "width/um",
        "",
        "   ",
        None,
        123,
        True,
    ],
)
def test_unsafe_and_invalid_parameter_names_rejected(invalid_name: Any) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(**_valid_intent_kwargs(parameter_name=invalid_name))
    assert exc_info.value.code == "INVALID_PCELLIZER_PARAMETER_NAME"


@pytest.mark.parametrize(
    "invalid_semantics",
    [
        "width",
        "length",
        "inferred",
        "auto",
        "TRANSVERSE_WIDTH",
        "LONGITUDINAL_LENGTH",
        "transverse",
        "longitudinal",
        "",
        None,
        123,
    ],
)
def test_invalid_dimension_semantics_rejected(invalid_semantics: Any) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(dimension_semantics=invalid_semantics)
        )
    assert exc_info.value.code == "INVALID_PCELLIZER_DIMENSION_SEMANTICS"


@pytest.mark.parametrize(
    "invalid_anchor",
    [
        "p1",
        "p2",
        "center",
        "fixed",
        "p3_fixed",
        "dynamic",
        "P1_FIXED",
        "",
        None,
        123,
    ],
)
def test_invalid_anchor_policy_rejected(invalid_anchor: Any) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(**_valid_intent_kwargs(anchor_policy=invalid_anchor))
    assert exc_info.value.code == "INVALID_PCELLIZER_ANCHOR_POLICY"


@pytest.mark.parametrize(
    "invalid_dependency",
    [
        "automatic_dependent_motion",
        "auto_move",
        "follow",
        "flexible",
        "FIXED_UNSELECTED_GEOMETRY",
        "",
        None,
        123,
    ],
)
def test_invalid_dependency_policy_rejected(invalid_dependency: Any) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(dependency_policy=invalid_dependency)
        )
    assert exc_info.value.code == "UNSUPPORTED_PCELLIZER_DEPENDENCY_POLICY"


@pytest.mark.parametrize(
    "field_name,bad_val",
    [
        ("min_um", -0.05),
        ("min_um", 0.0),
        ("min_um", float("nan")),
        ("min_um", float("inf")),
        ("min_um", "0.05"),
        ("min_um", True),
        ("nominal_um", -0.1),
        ("nominal_um", 0.0),
        ("max_um", -0.5),
        ("step_um", -0.01),
        ("step_um", 0.0),
        ("dbu_um", 0.0),
        ("dbu_um", -0.001),
        ("manufacturing_grid_um", 0.0),
        ("manufacturing_grid_um", -0.005),
    ],
)
def test_non_positive_and_non_finite_numeric_values_rejected(
    field_name: str, bad_val: Any
) -> None:
    kwargs = _valid_intent_kwargs(**{field_name: bad_val})
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(**kwargs)
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT_NUMERIC"


@pytest.mark.parametrize(
    "min_val,nom_val,max_val",
    [
        (0.200, 0.100, 0.500),  # min > nominal
        (0.050, 0.600, 0.500),  # nominal > max
        (0.600, 0.500, 0.400),  # min > max
        (0.300, 0.200, 0.100),  # inverted order
    ],
)
def test_invalid_bounds_ordering_rejected(min_val: float, nom_val: float, max_val: float) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(min_um=min_val, nominal_um=nom_val, max_um=max_val)
        )
    assert exc_info.value.code == "INVALID_PCELLIZER_PARAMETER_BOUNDS"


def test_grid_not_on_dbu_rejected() -> None:
    # manufacturing_grid_um = 0.0025 with dbu_um = 0.001 is 2.5 DBU (non-integer multiple)
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(dbu_um=0.001, manufacturing_grid_um=0.0025)
        )
    assert exc_info.value.code == "PCELLIZER_GRID_NOT_ON_DBU"


def test_grid_smaller_than_dbu_rejected() -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(dbu_um=0.005, manufacturing_grid_um=0.001)
        )
    assert exc_info.value.code == "PCELLIZER_GRID_NOT_ON_DBU"


def test_step_not_on_dbu_rejected() -> None:
    # step_um = 0.0005 with dbu_um = 0.001 is 0.5 DBU
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(dbu_um=0.001, step_um=0.0005)
        )
    assert exc_info.value.code == "PCELLIZER_STEP_NOT_ON_DBU"


def test_step_must_keep_generated_values_on_manufacturing_grid() -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(step_um=0.001)
        )
    assert exc_info.value.code == "PCELLIZER_STEP_OFF_GRID"


def test_nominal_and_bounds_must_share_one_step_lattice() -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(
                min_um=0.05,
                nominal_um=0.10,
                max_um=0.15,
                step_um=0.03,
                manufacturing_grid_um=0.01,
            )
        )
    assert exc_info.value.code == "PCELLIZER_BOUNDS_OFF_STEP_LATTICE"


def test_quantization_is_exact_not_large_ratio_float_tolerance() -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(
                min_um=1000000.0000001,
                nominal_um=1000000.0000001,
                max_um=1000000.0000001,
                step_um=0.005,
            )
        )
    assert exc_info.value.code == "PCELLIZER_BOUND_NOT_ON_DBU"


@pytest.mark.parametrize(
    "bound_field,off_dbu_val",
    [
        ("min_um", 0.051),
        ("nominal_um", 0.101),
        ("max_um", 0.501),
    ],
)
def test_bounds_not_on_dbu_rejected(bound_field: str, off_dbu_val: float) -> None:
    # dbu is 0.0025, so 0.051 / 0.0025 = 20.4 DBU (not integer multiple)
    overrides = {
        "dbu_um": 0.0025,
        "manufacturing_grid_um": 0.005,
        "min_um": 0.050,
        "nominal_um": 0.100,
        "max_um": 0.500,
        "step_um": 0.005,
    }
    overrides[bound_field] = off_dbu_val
    kwargs = _valid_intent_kwargs(**overrides)
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(**kwargs)
    assert exc_info.value.code == "PCELLIZER_BOUND_NOT_ON_DBU"


@pytest.mark.parametrize(
    "bound_field,off_grid_val",
    [
        ("min_um", 0.052),     # 52 nm on 1 nm DBU, but off 5 nm grid
        ("nominal_um", 0.103), # 103 nm on 1 nm DBU, but off 5 nm grid
        ("max_um", 0.504),     # 504 nm on 1 nm DBU, but off 5 nm grid
    ],
)
def test_bounds_off_manufacturing_grid_rejected(bound_field: str, off_grid_val: float) -> None:
    overrides = {
        "dbu_um": 0.001,
        "manufacturing_grid_um": 0.005,
        "min_um": 0.050,
        "nominal_um": 0.100,
        "max_um": 0.500,
    }
    overrides[bound_field] = off_grid_val
    kwargs = _valid_intent_kwargs(**overrides)
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(**kwargs)
    assert exc_info.value.code == "PCELLIZER_BOUND_OFF_GRID"


@pytest.mark.parametrize(
    "invalid_snapshot_sha",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "",
        None,
        12345,
    ],
)
def test_invalid_snapshot_package_sha256_rejected(invalid_snapshot_sha: Any) -> None:
    with pytest.raises(AnalysisError) as exc_info:
        build_pcellizer_parameter_intent(
            **_valid_intent_kwargs(snapshot_package_sha256=invalid_snapshot_sha)
        )
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT_HASH"


def test_validate_non_mapping_rejected() -> None:
    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(["not", "a", "mapping"])  # type: ignore[arg-type]
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT"


def test_validate_unsupported_schema_version_rejected() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    tampered = dict(intent)
    tampered["schema_version"] = 999
    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(tampered)
    assert exc_info.value.code == "UNSUPPORTED_PCELLIZER_SCHEMA_VERSION"


def test_validate_invalid_kind_rejected() -> None:
    intent = build_pcellizer_parameter_intent(**_valid_intent_kwargs())
    tampered = dict(intent)
    tampered["kind"] = "DifferentKind"
    with pytest.raises(AnalysisError) as exc_info:
        validate_pcellizer_parameter_intent(tampered)
    assert exc_info.value.code == "INVALID_PCELLIZER_INTENT"
