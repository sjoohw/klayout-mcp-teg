from pathlib import Path

import pytest

from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.layout_service import compare_layouts_service
from klayout_mcp.phase1_service import generate_phase1_direct_teg_service


def test_high_level_service_passes_one_atomic_request_and_attaches_manifest(tmp_path: Path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs
    observed = {}

    def fake_drawing_runner(**kwargs):
        observed.update(kwargs)
        return {
            "ok": True,
            "output_layout_path": kwargs["output_layout_path"],
            "top_cell": kwargs["top_cell"],
            "bbox_um": [0.0, 0.0, 2000.0, 54.0],
            "top_cell_count": 1,
            "dbu_um": 0.001,
            "fresh_reload_verified": True,
            "integer_dbu_geometry_verified": True,
            "orthogonal_manhattan_contract": True,
        }

    result = generate_phase1_direct_teg_service(
        output_layout_path=str(tmp_path / "phase1.gds"),
        top_cell="PHASE1_DIRECT_TEG",
        process_capability=profile,
        request_plan=request_plan,
        primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
        pad_rail_width_um=0.3,
        confirm_nonproduction=True,
        klayout_executable=None,
        timeout_seconds=60.0,
        drawing_runner=fake_drawing_runner,
    )

    assert observed["confirm_nonproduction"] is True
    assert observed["top_cell"] == "PHASE1_DIRECT_TEG"
    assert observed["dbu_um"] == 0.001
    assert result["drawing_scope"] == "phase1_direct_measurement_teg"
    assert result["fresh_reload_verified"] is True
    assert result["phase1_manifest"]["pad_count"] == 25
    assert result["phase1_manifest"]["primitive_duts"] == ["R1"]
    assert result["evidence_ladder"]["highest_attained_state"] == "intent_draft_complete"
    assert "drawing_complete" in result["evidence_ladder"]["observed_satisfied_states"]
    assert result["evidence_ladder"]["production_ready"] is False
    assert result["production_ready"] is False


def test_high_level_service_does_not_attach_manifest_after_draw_failure(tmp_path: Path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs

    def fail_drawing_runner(**kwargs):
        return {"ok": False, "code": "KLAYOUT_NOT_FOUND", "details": {}}

    result = generate_phase1_direct_teg_service(
        output_layout_path=str(tmp_path / "phase1.gds"),
        top_cell="PHASE1_DIRECT_TEG",
        process_capability=profile,
        request_plan=request_plan,
        primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
        pad_rail_width_um=0.3,
        confirm_nonproduction=True,
        klayout_executable=None,
        timeout_seconds=60.0,
        drawing_runner=fail_drawing_runner,
    )

    assert result["ok"] is False
    assert "phase1_manifest" not in result


def test_high_level_service_rejects_wrong_fresh_reload_bbox(tmp_path: Path, ready_phase1_inputs) -> None:
    profile, primitive, request_plan = ready_phase1_inputs

    def wrong_bbox_runner(**kwargs):
        return {
            "ok": True,
            "output_layout_path": kwargs["output_layout_path"],
            "top_cell": kwargs["top_cell"],
            "top_cell_count": 1,
            "dbu_um": 0.001,
            "bbox_um": [0.0, 0.0, 2000.0, 60.0],
            "fresh_reload_verified": True,
            "integer_dbu_geometry_verified": True,
            "orthogonal_manhattan_contract": True,
        }

    result = generate_phase1_direct_teg_service(
        output_layout_path=str(tmp_path / "wrong.gds"),
        top_cell="PHASE1_DIRECT_TEG",
        process_capability=profile,
        request_plan=request_plan,
        primitive_instances=[{"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}],
        pad_rail_width_um=0.3,
        confirm_nonproduction=True,
        klayout_executable=None,
        timeout_seconds=60.0,
        drawing_runner=wrong_bbox_runner,
    )

    assert result["ok"] is False
    assert result["code"] == "PHASE1_FRESH_RELOAD_INVARIANT_FAILED"
    assert result["details"]["mismatches"]["bbox_um"]["actual"] == [0.0, 0.0, 2000.0, 60.0]


def test_phase1_service_real_roundtrip_is_deterministic(
    tmp_path: Path,
    ready_phase1_inputs,
) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    profile, primitive, request_plan = ready_phase1_inputs
    common = {
        "top_cell": "PHASE1_DIRECT_TEG",
        "process_capability": profile,
        "request_plan": request_plan,
        "primitive_instances": [
            {"dut": "R1", "primitive": primitive, "origin_um": [960.0, 27.0]}
        ],
        "pad_rail_width_um": 0.3,
        "confirm_nonproduction": True,
        "klayout_executable": str(executable),
        "timeout_seconds": 120.0,
    }
    first = generate_phase1_direct_teg_service(
        output_layout_path=str(tmp_path / "phase1-first.gds"),
        **common,
    )
    second = generate_phase1_direct_teg_service(
        output_layout_path=str(tmp_path / "phase1-second.gds"),
        **common,
    )

    assert first["ok"] is True, first
    assert second["ok"] is True, second
    assert first["fresh_reload_verified"] is True
    assert first["bbox_um"] == [0.0, 0.0, 2000.0, 54.0]
    assert first["phase1_manifest"] == second["phase1_manifest"]
    comparison = compare_layouts_service(
        candidate_layout_path=first["output_layout_path"],
        reference_layout_path=second["output_layout_path"],
        candidate_top_cell="PHASE1_DIRECT_TEG",
        reference_top_cell="PHASE1_DIRECT_TEG",
        klayout_executable=str(executable),
        timeout_seconds=120.0,
    )
    assert comparison["ok"] is True, comparison
    assert comparison["comparison"]["equivalent"] is True
    assert all(
        layer["geometry_xor_clean"]
        for layer in comparison["comparison"]["layers"]
    )
