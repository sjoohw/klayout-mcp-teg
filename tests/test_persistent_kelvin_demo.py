from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from klayout_mcp.klayout_adapter import find_klayout_executable


def _load_demo_module():
    path = Path(__file__).resolve().parents[1] / "examples" / "run_persistent_kelvin_demo.py"
    spec = spec_from_file_location("persistent_kelvin_demo", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_persistent_kelvin_demo_reaches_measurement_package(tmp_path) -> None:
    try:
        find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    project_root = Path(__file__).resolve().parents[1]
    golden = (
        project_root
        / "artifacts"
        / "SLN001_kelvin_m1"
        / "SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds"
    )
    if not golden.is_file():
        pytest.skip("Kelvin golden GDS is not present")

    result = _load_demo_module().run_demo(
        project_root=project_root,
        run_root=tmp_path / "persistent-kelvin-demo",
    )

    assert result["ok"] is True
    assert result["demo_only"] is True
    assert result["four_call_statuses"] == [
        "intent_draft_complete",
        "plan_complete",
        "connectivity_projected",
        "measurement_package_complete",
    ]
    assert result["fresh_reload_verified"] is True
    assert result["connectivity_projection_verified"] is True
    assert result["measurement_layout_hash_match"] is True
    assert result["manifest_ancestry_revalidated"] is True
    assert result["highest_attained_state"] == "measurement_package_complete"
    assert result["production_ready"] is False
    assert result["measurement_program_ready"] is False
    assert Path(result["final_output_path"]).is_file()
