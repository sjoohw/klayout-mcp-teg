from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_process_intake import plan_pcellizer_process_inputs


def _layermap(tmp_path: Path) -> Path:
    path = tmp_path / "layers.yaml"
    path.write_text(
        "layers:\n  active: [1, 0]\n  contact: [5, 0]\n  m1: [10, 0]\n",
        encoding="utf-8",
    )
    return path


def test_layermap_populates_identity_but_never_infers_connectivity(tmp_path) -> None:
    result = plan_pcellizer_process_inputs(
        layermap_path=str(_layermap(tmp_path)),
        editable_layer_roles=["M1"],
        modified_cut_layer_roles=["contact"],
    )

    assert result["layermap"]["layers"]["m1"] == {"layer": 10, "datatype": 0}
    assert result["connectivity"] == []
    assert result["connectivity_policy"]["infer_from_layer_names"] is False
    assert "connectivity.contact" in {
        item["id"] for item in result["missing_questions"]
    }
    assert "enclosure_rules.contact" in {
        item["id"] for item in result["missing_questions"]
    }
    assert result["status"] == "needs_user_input"


def test_complete_user_rules_are_ready_without_drc_or_techfile(tmp_path) -> None:
    result = plan_pcellizer_process_inputs(
        layermap_path=str(_layermap(tmp_path)),
        process_name="demo45",
        process_version="v1",
        layout_dbu_um=0.001,
        manufacturing_grid_um=0.005,
        editable_layer_roles=["m1"],
        layer_rules={
            "m1": {
                "min_width_um": 0.022,
                "min_space_um": 0.022,
                "min_area_um2": 0.01,
                "project_max_width_um": 0.3,
            }
        },
    )

    assert result["status"] == "ready_for_geometry_export"
    assert result["missing_questions"] == []
    assert result["connectivity"] == []
    assert result["connectivity_policy"] == {
        "drc_auto_extract": False,
        "explicit_user_confirmation_required_for_modified_cuts": True,
        "infer_from_layer_names": False,
        "techfile_auto_import": False,
    }
    assert result["layer_rules"]["m1"]["project_max_width_um"] == 0.3


def test_modified_cut_requires_explicit_mapped_connection(tmp_path) -> None:
    common = {
        "layermap_path": str(_layermap(tmp_path)),
        "process_name": "demo45",
        "process_version": "v1",
        "layout_dbu_um": 0.001,
        "manufacturing_grid_um": 0.001,
        "editable_layer_roles": ["contact"],
        "layer_rules": {
            "contact": {
                "min_width_um": 0.05,
                "min_space_um": 0.05,
                "min_area_um2": 0.0025,
            }
        },
        "modified_cut_layer_roles": ["contact"],
    }
    missing = plan_pcellizer_process_inputs(**common)
    assert missing["status"] == "needs_user_input"

    ready = plan_pcellizer_process_inputs(
        **common,
        connectivity=[
            {
                "lower_layer_role": "active",
                "cut_layer_role": "contact",
                "upper_layer_role": "m1",
            }
        ],
        enclosure_rules={
            "contact": {
                "lower_enclosure_um": 0.01,
                "upper_enclosure_um": 0.01,
            }
        },
    )
    assert ready["status"] == "ready_for_geometry_export"
    assert ready["connectivity"][0]["source"] == "user_confirmed"


def test_grid_must_be_integer_multiple_of_layout_dbu(tmp_path) -> None:
    with pytest.raises(AnalysisError) as error:
        plan_pcellizer_process_inputs(
            layermap_path=str(_layermap(tmp_path)),
            layout_dbu_um=0.003,
            manufacturing_grid_um=0.005,
        )

    assert error.value.code == "PCELLIZER_GRID_NOT_ON_DBU"
