import pytest

from klayout_mcp.device_doe import plan_phase1_device_doe
from klayout_mcp.errors import AnalysisError


def _transistor_plan(**overrides):
    arguments = {
        "process_profile": "example-pdk",
        "process_profile_version": "1.0",
        "process_profile_confirmed": True,
        "family": "transistor",
        "device_type": "nmos",
        "measurement": "dc_4t",
        "required_terminals": ["G", "D", "S", "B"],
        "supported_axes": ["w_um", "l_um", "sa_um", "sb_um", "well_edge_distance_um"],
        "baseline": {
            "w_um": 1.0,
            "l_um": 0.1,
            "sa_um": 0.2,
            "sb_um": 0.2,
            "well_edge_distance_um": 1.0,
        },
        "sweeps": {"sa_um": [0.1, 0.2, 0.5], "sb_um": [0.1, 0.2, 0.5]},
    }
    arguments.update(overrides)
    return plan_phase1_device_doe(**arguments)


def test_ovat_keeps_a_baseline_and_changes_one_lde_axis_at_a_time() -> None:
    result = _transistor_plan(replicates=2)

    assert result["design_mode"] == "one_factor_at_a_time"
    assert result["variant_count"] == 5
    assert result["split_count"] == 10
    assert result["splits"][0]["changed_axes"] == []
    assert all(len(split["changed_axes"]) <= 1 for split in result["splits"])
    assert result["layout_generation_authorized"] is False


def test_full_factorial_is_explicit_and_deterministic() -> None:
    result = _transistor_plan(design_mode="full_factorial")

    assert result["variant_count"] == 9
    assert result["split_count"] == 9
    assert result["splits"][0]["split_id"] == "T001_R01"


def test_process_unsupported_lde_axis_is_rejected() -> None:
    with pytest.raises(AnalysisError) as caught:
        _transistor_plan(
            supported_axes=["w_um", "l_um"],
            sweeps={"well_edge_distance_um": [0.5, 1.0]},
        )

    assert caught.value.code == "DOE_AXIS_CONTRACT_MISMATCH"
    assert caught.value.details["unsupported_requested_axes"] == ["well_edge_distance_um"]


def test_unconfirmed_process_profile_cannot_expand_doe() -> None:
    with pytest.raises(AnalysisError) as caught:
        _transistor_plan(process_profile_confirmed=False)

    assert caught.value.code == "PROCESS_PROFILE_CONFIRMATION_REQUIRED"
