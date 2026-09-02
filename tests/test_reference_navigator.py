import json
import subprocess
from pathlib import Path

import pytest

from klayout_mcp.klayout_adapter import find_klayout_executable


def test_reference_navigator_requires_open_before_confirm(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    root = Path(__file__).resolve().parents[1]
    result_path = tmp_path / "reference-panel-smoke.json"
    script = Path(__file__).parent / "fixtures" / "smoke_reference_navigator.py"
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
        "object_name": "teg_reference_navigator_dock",
        "title": "TEG Reference Navigator",
        "open_enabled_before_load": False,
        "confirm_enabled_before_open": False,
        "load_status": "Loaded ref-123 / contact_array / reference_precedent",
        "open_enabled_after_load": True,
        "copy_path_enabled_after_load": True,
        "copy_path_status": "Full Ref GDS path copied to clipboard.",
        "open_status": "Inspecting full GDS: TOP / reference_precedent",
        "confirm_enabled_after_open": True,
        "confirm_status": "Confirmed view-123 as reference_precedent",
        "confirm_enabled_after_confirm": False,
    }


def test_reference_navigator_opens_full_hierarchical_gds_and_marks_roi(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    root = Path(__file__).resolve().parents[1]
    result_path = tmp_path / "reference-open.json"
    image_path = tmp_path / "reference-open.png"
    source_path = tmp_path / "reference-source.gds"
    script = Path(__file__).parent / "fixtures" / "open_reference_navigator_layout.py"
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
            "-rd",
            f"image_path={image_path}",
            "-rd",
            f"source_path={source_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["active_cell"] == "REFERENCE_TOP"
    assert result["full_reference_opened"] is True
    assert result["source_geometry_modified"] is False
    assert result["image_exists"] is True
