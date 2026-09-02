import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from klayout_mcp.klayout_adapter import find_klayout_executable


def _run_capture(tmp_path: Path) -> tuple[Path, dict]:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    root = Path(__file__).resolve().parents[1]
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "capture-source.gds"
    result_path = tmp_path / "capture-result.json"
    package_root = tmp_path / "snapshot-store"
    recovered_path = tmp_path / "recovered.gds"
    script = Path(__file__).parent / "fixtures" / "capture_pcellizer_selection.py"
    completed = subprocess.run(
        [
            str(executable),
            "-b",
            "-r",
            str(script),
            "-rd",
            f"project_root={root}",
            "-rd",
            f"source_path={source_path}",
            "-rd",
            f"result_path={result_path}",
            "-rd",
            f"package_root={package_root}",
            "-rd",
            f"recovered_path={recovered_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return source_path, json.loads(result_path.read_text(encoding="utf-8"))


def test_capture_binds_exact_edges_without_modifying_source(tmp_path) -> None:
    source_path, result = _run_capture(tmp_path)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

    assert result["kind"] == "PCellizerParameterCapture"
    assert result["source"]["layout_sha256"] == source_hash
    assert result["ruler"]["ruler_dbu"] == [[11000, 5500], [12000, 5500]]
    assert [item["selection_index"] for item in result["ruler"]["endpoint_bindings"]] == [0, 1]
    assert len(result["endpoint_manifests"]) == 2
    assert result["auto_resolution"] == {
        "endpoint_manifests_match": True,
        "selection_count": 2,
        "selection_mode": "selected_layer_and_ruler_auto_resolved",
    }
    assert result["serialized_shape_kinds"] == ["box", "polygon", "path", "edge"]
    assert result["unsaved_source_error"] == "UNSAVED_PCELLIZER_SOURCE"
    assert result["snapshot_roundtrip"] == {
        "cell_names": ["LEAF", "TOP"],
        "flattening_performed": False,
        "layout_sha256": source_hash,
        "source_runtime_dependency_used": False,
        "top_cells": ["TOP"],
    }
    assert (tmp_path / "recovered.gds").read_bytes() == source_path.read_bytes()
    assert all(item["manifest"]["layer"] == 10 for item in result["endpoint_manifests"])
    assert all(
        len(item["manifest"]["occurrence_path"]["segments"]) == 1
        for item in result["endpoint_manifests"]
    )
    assert all(
        item["manifest"]["occurrence_path"]["segments"][0]["child_cell"] == "LEAF"
        for item in result["endpoint_manifests"]
    )
    assert result["source_layout_modified"] is False
    assert result["flattening_performed"] is False
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash


def test_capture_is_deterministic_after_independent_fresh_reloads(tmp_path) -> None:
    first_source, first = _run_capture(tmp_path)
    second_source, second = _run_capture(tmp_path)

    assert first_source.read_bytes() == second_source.read_bytes()
    assert first == second


def test_pcellizer_dock_loads_in_hidden_klayout_gui(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    root = Path(__file__).resolve().parents[1]
    result_path = tmp_path / "panel-smoke.json"
    script = Path(__file__).parent / "fixtures" / "smoke_pcellizer_panel.py"
    completed = subprocess.run(
        [
            str(executable),
            "-z",
            "-nc",
            "-rx",
            "-r",
            str(script),
            "-rd",
            f"project_root={root}",
            "-rd",
            f"result_path={result_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result == {
        "capture_status": "Captured 2 exact endpoint manifests.",
        "copy_enabled": False,
        "copy_enabled_after_capture": True,
        "object_name": "teg_pcellizer_dock",
        "package_path_enabled_after_snapshot": True,
        "title": "TEG PCellizer",
        "visible": False,
        "snapshot_enabled_after_capture": True,
        "snapshot_status": "Standalone snapshot: aaaaaaaaaaaa",
    }
