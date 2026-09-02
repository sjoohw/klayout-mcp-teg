import json
from pathlib import Path
import subprocess

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable, run_klayout_worker
from klayout_mcp.pad_macro import compose_pad_macro_overlay, create_pad_macro_artifact


def _instances():
    return [
        {"pad_id": "P1", "x_um": 0, "y_um": 0, "rotation_deg": 0, "mirror_x": False},
        {"pad_id": "P2", "x_um": 80, "y_um": 0, "rotation_deg": 180, "mirror_x": False},
    ]


def _mock_worker(request, **kwargs):
    return {
        "ok": True,
        "top_cell": "PAD_MACRO_40X40",
        "dbu_um": 0.001,
        "bbox_um": [0.0, 0.0, 40.0, 40.0],
        "width_um": 40.0,
        "height_um": 40.0,
        "access_layer": {"layer": 10, "datatype": 0},
        "eligible_edge_landings": [
            {"landing_id": "access-0-left", "edge": "left", "segment_um": [[0, 0], [0, 40]], "shape_bbox_um": [0, 0, 40, 40]}
        ],
        "recursive_geometry_fingerprint_sha256": "a" * 64,
        "hierarchy_cell_count": 2,
        "geometry_preservation_mode": "source_stream_and_recursive_hierarchy_immutable",
    }


def test_pad_macro_artifact_preserves_source_and_records_no_keepout(tmp_path: Path) -> None:
    source = tmp_path / "pad.gds"
    source.write_bytes(b"immutable-pad-stream")

    result = create_pad_macro_artifact(
        source_layout_path=str(source),
        top_cell="PAD_MACRO_40X40",
        access_layer={"layer": 10, "datatype": 0},
        instances=_instances(),
        package_root=tmp_path / "registry",
        expected_dbu_um=0.001,
        worker_runner=_mock_worker,
    )

    artifact = result["artifact"]
    package = Path(result["package_path"])
    assert artifact["extra_keepout"] == "none"
    assert artifact["pad_geometry_generation_allowed"] is False
    assert artifact["source_hierarchy_must_be_preserved"] is True
    assert (package / "source.gds").read_bytes() == b"immutable-pad-stream"
    assert json.loads((package / "artifact.json").read_text(encoding="utf-8")) == artifact


def test_pad_macro_compose_rejects_tampered_artifact_metadata(tmp_path: Path) -> None:
    source = tmp_path / "pad.gds"
    source.write_bytes(b"immutable-pad-stream")
    result = create_pad_macro_artifact(
        source_layout_path=str(source),
        top_cell="PAD_MACRO_40X40",
        access_layer={"layer": 10, "datatype": 0},
        instances=_instances(),
        package_root=tmp_path / "registry",
        expected_dbu_um=0.001,
        worker_runner=_mock_worker,
    )
    package = Path(result["package_path"])
    artifact_path = package / "artifact.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["instances"][0]["x_um"] = 123.0
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(AnalysisError) as caught:
        compose_pad_macro_overlay(
            package_path=package,
            output_path=str(tmp_path / "should-not-exist.gds"),
            operations=[],
            worker_runner=lambda request, **kwargs: {"ok": True},
        )

    assert caught.value.code == "PAD_MACRO_PACKAGE_ADDRESS_MISMATCH"


def test_pad_macro_artifact_rejects_dbu_mismatch_before_publication(tmp_path: Path) -> None:
    source = tmp_path / "pad.gds"
    source.write_bytes(b"pad")

    with pytest.raises(AnalysisError) as caught:
        create_pad_macro_artifact(
            source_layout_path=str(source),
            top_cell="PAD_MACRO_40X40",
            access_layer={"layer": 10, "datatype": 0},
            instances=_instances(),
            package_root=tmp_path / "registry",
            expected_dbu_um=0.005,
            worker_runner=_mock_worker,
        )

    assert caught.value.code == "PAD_MACRO_DBU_MISMATCH"
    assert list((tmp_path / "registry").iterdir()) == []


def test_installed_klayout_inspects_recursive_40um_pad_macro(tmp_path: Path) -> None:
    try:
        executable = find_klayout_executable()
    except AnalysisError:
        pytest.skip("KLayout executable is not installed")
    source = tmp_path / "pad.gds"
    script = Path(__file__).parent / "fixtures" / "create_pad_macro.py"
    completed = subprocess.run(
        [str(executable), "-b", "-r", str(script), "-rd", f"output_path={source}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    result = run_klayout_worker(
        {
            "operation": "inspect_pad_macro",
            "layout_path": str(source),
            "top_cell": "PAD_MACRO_40X40",
            "access_layer": {"layer": 10, "datatype": 0},
            "edge_tolerance_um": 0.001,
        },
        executable_path=str(executable),
    )

    assert result["ok"] is True, result
    assert result["width_um"] == 40.0
    assert result["height_um"] == 40.0
    assert result["hierarchy_cell_count"] == 2
    assert {landing["edge"] for landing in result["eligible_edge_landings"]} == {
        "left", "right", "bottom", "top"
    }


def test_internal_access_metal_is_not_reported_as_edge_landing(tmp_path: Path) -> None:
    try:
        executable = find_klayout_executable()
    except AnalysisError:
        pytest.skip("KLayout executable is not installed")
    source = tmp_path / "inset-pad.gds"
    script = Path(__file__).parent / "fixtures" / "create_inset_pad_macro.py"
    completed = subprocess.run(
        [str(executable), "-b", "-r", str(script), "-rd", f"output_path={source}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    result = run_klayout_worker(
        {
            "operation": "inspect_pad_macro",
            "layout_path": str(source),
            "top_cell": "PAD_MACRO_40X40",
            "access_layer": {"layer": 10, "datatype": 0},
            "edge_tolerance_um": 0.001,
        },
        executable_path=str(executable),
    )

    assert result["ok"] is True, result
    assert result["eligible_edge_landings"] == []


def test_installed_klayout_composes_without_editing_pad_cell(tmp_path: Path) -> None:
    try:
        executable = find_klayout_executable()
    except AnalysisError:
        pytest.skip("KLayout executable is not installed")
    source = tmp_path / "pad.gds"
    script = Path(__file__).parent / "fixtures" / "create_pad_macro.py"
    completed = subprocess.run(
        [str(executable), "-b", "-r", str(script), "-rd", f"output_path={source}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    registered = create_pad_macro_artifact(
        source_layout_path=str(source),
        top_cell="PAD_MACRO_40X40",
        access_layer={"layer": 10, "datatype": 0},
        instances=_instances(),
        package_root=tmp_path / "registry",
        expected_dbu_um=0.001,
        klayout_executable=str(executable),
    )
    output = tmp_path / "overlay.gds"

    result = compose_pad_macro_overlay(
        package_path=registered["package_path"],
        output_path=str(output),
        operations=[
            {"type": "add_box", "category": "dut", "layer": {"layer": 20, "datatype": 0}, "bbox_um": [45, 10, 55, 30]},
            {"type": "add_box", "category": "routing", "layer": {"layer": 10, "datatype": 0}, "bbox_um": [40, 19, 45, 21]},
        ],
        klayout_executable=str(executable),
    )

    assert result["ok"] is True, result
    assert result["pad_instance_count"] == 2
    assert result["source_pad_geometry_preserved"] is True
    assert result["pad_geometry_added_or_modified"] is False
    assert result["fresh_reload_verified"] is True
    assert output.is_file()
