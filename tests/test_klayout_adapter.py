from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.klayout_adapter import run_klayout_worker
from klayout_mcp import server
from klayout_mcp.server import analyze_padset, render_boundary_overlay


def test_worker_start_failure_is_not_reported_as_snapshot_failure(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "klayout.exe"
    executable.write_bytes(b"not executable")

    def fail_start(*args, **kwargs):
        raise PermissionError("blocked")

    monkeypatch.setattr(subprocess, "run", fail_start)

    with pytest.raises(AnalysisError) as caught:
        run_klayout_worker({"operation": "test"}, executable_path=str(executable))

    assert caught.value.code == "KLAYOUT_START_FAILED"


def test_worker_timeout_is_structured(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "klayout.exe"
    executable.write_bytes(b"placeholder")

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(AnalysisError) as caught:
        run_klayout_worker(
            {"operation": "test"},
            executable_path=str(executable),
            timeout_seconds=0.01,
        )

    assert caught.value.code == "KLAYOUT_TIMEOUT"
    assert caught.value.next_action


def test_worker_does_not_inherit_mcp_stdio_stdin(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "klayout.exe"
    executable.write_bytes(b"placeholder")

    def complete(command, **kwargs):
        assert kwargs["stdin"] is subprocess.DEVNULL
        response_definition = next(
            command[index + 1]
            for index, argument in enumerate(command[:-1])
            if argument == "-rd" and command[index + 1].startswith("response_path=")
        )
        response_path = Path(response_definition.split("=", 1)[1])
        response_path.write_text('{"worker": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", complete)

    result = run_klayout_worker(
        {"operation": "test"},
        executable_path=str(executable),
    )

    assert result == {"worker": "ok"}


@pytest.mark.parametrize("layout_suffix", [".gds", ".oas"])
def test_analyze_hierarchical_padset_with_installed_klayout(
    tmp_path, layout_suffix: str
) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / f"padset{layout_suffix}"
    layermap_path = tmp_path / "layers.yaml"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable),
            "-b",
            "-r",
            str(fixture_script),
            "-rd",
            f"output_path={layout_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert layout_path.is_file()
    layermap_path.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")

    result = analyze_padset(
        padset_path=str(layout_path),
        layermap_path=str(layermap_path),
        klayout_executable=str(executable),
    )

    assert result["ok"] is True
    assert result["layout_read_count"] == 1
    assert result["pad_count"] == 25
    assert result["dut_slot_count"] == 21
    assert result["padset"]["dbu_um"] == pytest.approx(0.002)
    assert result["padset"]["top_cell"] == "PADSET"
    assert result["padset"]["m1"] == {"layer": 10, "datatype": 2}
    assert result["m1_extraction"]["raw_box_count"] == 25
    assert result["m1_extraction"]["normalized_pad_candidate_count"] == 25
    assert result["m1_extraction"]["component_count"] == 25
    assert len(set(result["m1_connectivity"]["pad_component_ids"].values())) == 25
    assert result["dut_slots"][0]["landings"]["source"]["status"] == "resolved"
    assert result["dut_slots"][0]["landings"]["drain"]["status"] == "resolved"
    assert result["dut_slots"][0]["landings"]["gate"]["status"] == "unresolved"
    assert result["dut_slots"][0]["landings"]["body"]["status"] == "unresolved"


def test_analyze_padset_requires_existing_layermap(tmp_path) -> None:
    result = analyze_padset(
        padset_path=str(tmp_path / "missing.gds"),
        layermap_path=str(tmp_path / "missing.yaml"),
    )

    assert result["ok"] is False
    assert result["code"] == "LAYERMAP_NOT_FOUND"


def test_analyze_padset_rejects_nonpositive_timeout(tmp_path) -> None:
    result = analyze_padset(
        padset_path=str(tmp_path / "missing.gds"),
        layermap_path=str(tmp_path / "missing.yaml"),
        timeout_seconds=0,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_TIMEOUT"


def test_analyze_padset_rejects_nonpositive_landing_search_depth(tmp_path) -> None:
    result = analyze_padset(
        padset_path=str(tmp_path / "missing.gds"),
        layermap_path=str(tmp_path / "missing.yaml"),
        landing_search_half_depth_um=0,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_LANDING_SEARCH_DEPTH"


def test_boundary_overlay_validates_before_snapshot(tmp_path, monkeypatch) -> None:
    def fail_snapshot(*args, **kwargs):
        raise AssertionError("snapshot must not be created for invalid parameters")

    monkeypatch.setattr(server, "create_layout_snapshot", fail_snapshot)

    result = render_boundary_overlay(
        str(tmp_path / "missing.gds"),
        str(tmp_path / "missing.yaml"),
        str(tmp_path / "overlay.png"),
        timeout_seconds=0,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_TIMEOUT"


def test_region_normalization_handles_polygon_pads_and_attached_routes(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / "polygon-padset.gds"
    layermap = tmp_path / "layers.yaml"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable), "-b", "-r", str(fixture_script),
            "-rd", f"output_path={layout_path}",
            "-rd", "pad_shape=polygon",
            "-rd", "attach_routes=1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")

    result = analyze_padset(str(layout_path), str(layermap), klayout_executable=str(executable))

    assert result["ok"] is True
    assert result["layout_read_count"] == 1
    assert result["pad_count"] == 25
    assert result["m1_extraction"]["shape_counts"]["polygon"] >= 25
    assert result["m1_extraction"]["shape_counts"]["path"] == 1
    assert result["m1_extraction"]["normalized_pad_candidate_count"] == 25
    assert result["m1_connectivity"]["shorted_pad_groups"] == []


def test_connected_component_short_is_rejected(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / "shorted-padset.gds"
    layermap = tmp_path / "layers.yaml"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable), "-b", "-r", str(fixture_script),
            "-rd", f"output_path={layout_path}",
            "-rd", "bridge_short=1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")

    result = analyze_padset(str(layout_path), str(layermap), klayout_executable=str(executable))

    assert result["ok"] is False
    assert result["code"] == "PAD_SHORT_DETECTED"
    assert [23, 24] in result["details"]["shorted_pad_groups"]


def test_mesh_pad_outer_hulls_are_detected(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / "mesh-padset.gds"
    layermap = tmp_path / "layers.yaml"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable), "-b", "-r", str(fixture_script),
            "-rd", f"output_path={layout_path}",
            "-rd", "pad_shape=mesh",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")

    result = analyze_padset(str(layout_path), str(layermap), klayout_executable=str(executable))

    assert result["ok"] is True
    assert result["pad_count"] == 25
    assert result["m1_extraction"]["normalized_pad_candidate_count"] == 25


def test_boundary_bands_resolve_exact_m1_landing_polygons(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / "landing-padset.gds"
    layermap = tmp_path / "layers.yaml"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable), "-b", "-r", str(fixture_script),
            "-rd", f"output_path={layout_path}",
            "-rd", "landing_routes=1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")

    result = analyze_padset(
        str(layout_path),
        str(layermap),
        klayout_executable=str(executable),
        landing_search_half_depth_um=1.0,
    )

    assert result["ok"] is True
    first = result["dut_slots"][0]
    assert first["landing_status"] == "resolved"
    assert first["landings"]["source"]["bbox_um"] == pytest.approx([59.0, 10.0, 60.0, 50.0])
    assert first["landings"]["drain"]["bbox_um"] == pytest.approx([100.0, 10.0, 101.0, 50.0])
    assert first["landings"]["gate"]["bbox_um"] == pytest.approx([79.0, 49.0, 81.0, 51.0])
    assert first["landings"]["body"]["bbox_um"] == pytest.approx([79.0, 9.0, 81.0, 11.0])
    assert first["landings"]["gate"]["area_um2"] == pytest.approx(4.0)
    assert first["landings"]["gate"]["polygons_um"]
    second = result["dut_slots"][1]
    assert second["landing_status"] == "unresolved"
    assert set(second["landings"]) == {"source", "drain", "gate", "body"}
    assert result["m1_connectivity"]["unresolved_landings"]

    repeated = analyze_padset(
        str(layout_path),
        str(layermap),
        klayout_executable=str(executable),
        landing_search_half_depth_um=1.0,
    )
    assert repeated["dut_slots"][0]["landings"] == first["landings"]


def test_boundary_overlay_renders_view_markers_without_modifying_input(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / "overlay-padset.gds"
    layermap = tmp_path / "layers.yaml"
    image_path = tmp_path / "boundary-overlay.png"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable), "-b", "-r", str(fixture_script),
            "-rd", f"output_path={layout_path}",
            "-rd", "landing_routes=1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")
    input_before = layout_path.read_bytes()

    result = render_boundary_overlay(
        str(layout_path),
        str(layermap),
        str(image_path),
        klayout_executable=str(executable),
        image_width=800,
        image_height=300,
    )

    assert result["ok"] is True
    assert result["pad_count"] == 25
    assert result["dut_slot_count"] == 21
    assert result["overlay"]["input_layout_modified"] is False
    assert result["overlay"]["marker_counts"] == {
        "pads": 25,
        "labels": 46,
        "slots": 21,
        "resolved_landings": 44,
        "unresolved_landings": 40,
    }
    assert result["padset"]["path"] == str(layout_path.resolve())
    assert result["padset"]["snapshot_sha256"] == hashlib.sha256(input_before).hexdigest()
    assert result["padset"]["snapshot_size_bytes"] == len(input_before)
    assert image_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert layout_path.read_bytes() == input_before


def test_boundary_overlay_uses_one_snapshot_and_one_integrated_worker(
    tmp_path, monkeypatch
) -> None:
    layout_path = tmp_path / "changing-padset.gds"
    layermap = tmp_path / "layers.yaml"
    image_path = tmp_path / "snapshot-overlay.png"
    original_bytes = b"original-layout"
    layout_path.write_bytes(original_bytes)
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")
    observed_paths: list[Path] = []

    def fake_analyze_padset_snapshot(padset_path, **kwargs):
        snapshot_path = Path(padset_path)
        observed_paths.append(snapshot_path)
        assert snapshot_path != layout_path
        assert snapshot_path.read_bytes() == original_bytes
        assert kwargs["source_padset_path"] == str(layout_path.resolve())
        assert kwargs["snapshot_sha256"] == hashlib.sha256(original_bytes).hexdigest()
        assert kwargs["snapshot_size_bytes"] == len(original_bytes)
        assert kwargs["image_path"] == str(image_path)
        assert kwargs["image_width"] == 1600
        assert kwargs["image_height"] == 600
        layout_path.write_bytes(b"changed-after-analysis")
        return {
            "ok": True,
            "padset": {
                "path": kwargs["source_padset_path"],
                "top_cell": "TOP",
                "snapshot_sha256": kwargs["snapshot_sha256"],
                "snapshot_size_bytes": kwargs["snapshot_size_bytes"],
            },
            "pad_count": 0,
            "dut_slot_count": 0,
            "layout_read_count": 1,
            "pads": [],
            "dut_slots": [],
            "m1_connectivity": {"unresolved_landings": []},
            "overlay": {"ok": True, "image_path": str(image_path)},
        }

    monkeypatch.setattr(server, "_analyze_padset_snapshot", fake_analyze_padset_snapshot)

    result = render_boundary_overlay(str(layout_path), str(layermap), str(image_path))

    assert result["ok"] is True
    assert len(observed_paths) == 1
    assert result["padset"]["path"] == str(layout_path.resolve())
    assert result["padset"]["snapshot_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert not observed_paths[0].exists()


def test_analyze_padset_uses_one_snapshot_and_one_integrated_worker(
    tmp_path, monkeypatch
) -> None:
    layout_path = tmp_path / "changing-analysis.gds"
    layermap = tmp_path / "layers.yaml"
    original_bytes = b"stable-analysis-input"
    layout_path.write_bytes(original_bytes)
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")
    observed_paths: list[Path] = []

    def fake_worker(request, **kwargs):
        snapshot_path = Path(request["layout_path"])
        observed_paths.append(snapshot_path)
        assert snapshot_path != layout_path
        assert snapshot_path.read_bytes() == original_bytes
        assert request["operation"] == "analyze_padset_integrated"
        assert kwargs["hidden_view"] is False
        layout_path.write_bytes(b"changed-after-integrated-analysis")
        return {
            "ok": True,
            "layout": {
                "path": str(snapshot_path),
                "dbu_um": 0.002,
                "top_cell": "PADSET",
                "m1": {"layer": 10, "datatype": 2},
                "klayout_version": "test",
            },
            "pad_count": 25,
            "pads": [],
            "dut_slot_count": 21,
            "dut_slots": [],
            "m1_extraction": {},
            "m1_connectivity": {},
            "layout_read_count": 1,
        }

    monkeypatch.setattr(server, "run_klayout_worker", fake_worker)

    result = analyze_padset(str(layout_path), str(layermap))

    assert result["ok"] is True
    assert len(observed_paths) == 1
    assert result["layout_read_count"] == 1
    assert result["padset"]["path"] == str(layout_path.resolve())
    assert result["padset"]["snapshot_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert result["padset"]["snapshot_size_bytes"] == len(original_bytes)
    assert not observed_paths[0].exists()


def test_boundary_overlay_does_not_overwrite_existing_artifact(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    layout_path = tmp_path / "overlay-existing-padset.gds"
    layermap = tmp_path / "layers.yaml"
    image_path = tmp_path / "existing.png"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [str(executable), "-b", "-r", str(fixture_script), "-rd", f"output_path={layout_path}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")
    image_path.write_bytes(b"keep")

    result = render_boundary_overlay(
        str(layout_path),
        str(layermap),
        str(image_path),
        klayout_executable=str(executable),
    )

    assert result["ok"] is False
    assert result["code"] == "OUTPUT_EXISTS"
    assert image_path.read_bytes() == b"keep"


def test_no_normalized_pad_candidates_is_structured(tmp_path, monkeypatch) -> None:
    padset = tmp_path / "padset.gds"
    layermap = tmp_path / "layers.yaml"
    padset.write_bytes(b"mock-layout")
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")

    monkeypatch.setattr(
        server,
        "run_klayout_worker",
        lambda *args, **kwargs: {
            "ok": False,
            "code": "PAD_ROW_NOT_FOUND",
            "message": "No aligned pad row matches the expected count and size.",
            "details": {
                "m1_extraction": {
                    "raw_box_count": 0,
                    "shape_counts": {"box": 0, "path": 1, "polygon": 2, "other": 0},
                    "normalized_pad_candidate_count": 0,
                    "component_count": 3,
                }
            },
        },
    )

    result = analyze_padset(str(padset), str(layermap))

    assert result["ok"] is False
    assert result["code"] == "PAD_ROW_NOT_FOUND"
    assert result["details"]["m1_extraction"]["shape_counts"]["polygon"] == 2
    assert result["details"]["m1_extraction"]["normalized_pad_candidate_count"] == 0


def test_integrated_worker_missing_fields_is_structured(tmp_path, monkeypatch) -> None:
    padset = tmp_path / "padset.gds"
    layermap = tmp_path / "layers.yaml"
    padset.write_bytes(b"mock-layout")
    layermap.write_text("layers:\n  m1: [10, 2]\n", encoding="utf-8")
    monkeypatch.setattr(server, "run_klayout_worker", lambda *args, **kwargs: {"ok": True})

    result = analyze_padset(str(padset), str(layermap))

    assert result["ok"] is False
    assert result["code"] == "KLAYOUT_RESPONSE_INVALID"
    assert "layout" in result["details"]["missing_keys"]
