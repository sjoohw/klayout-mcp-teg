import copy

import pytest

from conftest import SYNTHETIC_PROCESS_CAPABILITY
from klayout_mcp.errors import AnalysisError
from klayout_mcp.process_capability import (
    describe_builtin_process_capability,
    pdk_profile_input_contract,
    required_metal_space_um,
    validate_process_capability,
)


def test_runtime_ships_no_builtin_fabrication_process() -> None:
    with pytest.raises(AnalysisError) as caught:
        describe_builtin_process_capability("target_process")

    assert caught.value.code == "NO_BUNDLED_PROCESS_CAPABILITY"
    assert caught.value.details["bundled_process_profiles"] == []
    assert caught.value.details["input_contract"]["runtime_profile_policy"] == {
        "bundled_process_profiles": False,
        "profile_must_be_user_or_host_supplied": True,
        "unknown_process_values_fail_closed": True,
        "onboarding_document": "onboarding.md",
    }


def test_input_contract_separates_process_organization_and_job_inputs() -> None:
    result = pdk_profile_input_contract()

    assert result["runtime_profile_policy"]["bundled_process_profiles"] is False
    assert "layers" in result["required_for_core_profile"]
    assert "fixed terminal names and order per family/measurement" in result[
        "required_from_organization_preset"
    ]
    assert "frame size and Pad topology" in result["not_pdk_inputs"]


def test_explicit_synthetic_capability_is_valid_but_not_production_ready() -> None:
    result = validate_process_capability(SYNTHETIC_PROCESS_CAPABILITY)

    assert result["first_metal_role"] == "m1"
    assert result["device_families"] == ["capacitor", "resistor", "transistor"]
    assert result["production_ready"] is False
    assert result["process_profile_approved"] is False


def test_optional_verification_evidence_does_not_imply_production() -> None:
    profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    profile["process"]["evidence_status"] = "approved"
    profile["verification"] = {"drc": "approved", "lvs": "approved", "pex": "public"}

    result = validate_process_capability(profile)

    assert result["process_profile_approved"] is True
    assert result["unapproved_optional_verification_evidence"] == ["pex"]
    assert result["production_ready"] is False


def test_layer_collision_is_rejected() -> None:
    profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    profile["layers"]["gate"] = profile["layers"]["active"]

    with pytest.raises(AnalysisError) as caught:
        validate_process_capability(profile)

    assert caught.value.code == "PROCESS_LAYER_COLLISION"


def test_device_cannot_claim_an_unrecognized_lde_axis() -> None:
    profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    profile["devices"]["example_nmos"]["doe_axes"].append("magic_stress")

    with pytest.raises(AnalysisError) as caught:
        validate_process_capability(profile)

    assert caught.value.code == "INVALID_PROCESS_DEVICE_CAPABILITY"
    assert caught.value.details["invalid_doe_axes"] == ["magic_stress"]


def test_explicit_width_dependent_spacing_table_is_evaluated() -> None:
    result = validate_process_capability(SYNTHETIC_PROCESS_CAPABILITY)
    metal = result["routing_metals"][0]

    assert result["manufacturing_grid_um"] == 0.001
    assert result["manufacturing_grid_dbu"] == 1
    assert required_metal_space_um(metal, width_um=0.1, parallel_length_um=2.0) == 0.1
    assert required_metal_space_um(metal, width_um=0.3, parallel_length_um=2.0) == 0.3


def test_process_manufacturing_grid_must_be_an_integer_dbu_multiple() -> None:
    profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    profile["manufacturing_grid_um"] = 0.0015

    with pytest.raises(AnalysisError) as caught:
        validate_process_capability(profile)

    assert caught.value.code == "INVALID_PROCESS_MANUFACTURING_GRID"
