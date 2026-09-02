from __future__ import annotations

from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.organization_presets import (
    load_organization_preset,
    validate_organization_preset,
)


def test_example_company_preset_loads_as_pdk_independent() -> None:
    root = Path(__file__).resolve().parents[1]
    result = load_organization_preset(
        str(root / "examples" / "settings" / "organization_measurement_preset.yaml")
    )

    assert result["pdk_independent"] is True
    assert result["project_routing_defaults"]["max_width_um"] is None
    assert result["verification_environment"] == {
        "drc": "available",
        "lvs": "available",
        "pex": "available",
    }
    assert result["device_coverage_policy"]["default_for_new_device"] == "pending"
    assert result["terminal_order_by_family_and_measurement"]["resistor"]["kelvin_4t"] == [
        "F+",
        "F-",
        "S+",
        "S-",
    ]


def test_project_max_width_is_optional_but_must_be_positive() -> None:
    preset = {
        "schema_version": 1,
        "name": "company",
        "approval_status": "company_approved",
        "terminal_order_by_family_and_measurement": {
            "transistor": {"dc_4t": ["G", "D", "S", "B"]}
        },
        "project_routing_defaults": {"max_width_um": 0.0},
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

    with pytest.raises(AnalysisError) as caught:
        validate_organization_preset(preset)

    assert caught.value.code == "INVALID_ORGANIZATION_PRESET"
