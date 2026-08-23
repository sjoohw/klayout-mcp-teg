from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from klayout_mcp import server
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.server import inspect_sample_dut


def _write_layermap(path: Path) -> None:
    path.write_text(
        "layers:\n"
        "  active: [1, 0]\n"
        "  poly: [2, 0]\n"
        "  contact: [3, 0]\n"
        "  m1: [10, 2]\n"
        "  label: [100, 0]\n",
        encoding="utf-8",
    )


def _create_sample(tmp_path: Path, *defines: str) -> tuple[Path, Path, Path]:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    sample = tmp_path / "sample-dut.gds"
    layermap = tmp_path / "layers.yaml"
    fixture = Path(__file__).parent / "fixtures" / "create_sample_dut.py"
    command = [
        str(executable),
        "-b",
        "-r",
        str(fixture),
        "-rd",
        f"output_path={sample}",
    ]
    for define in defines:
        command.extend(["-rd", define])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    _write_layermap(layermap)
    return executable, sample, layermap


def test_inspect_sample_dut_reports_hierarchy_layers_texts_and_snapshot(tmp_path) -> None:
    executable, sample, layermap = _create_sample(tmp_path)
    original = sample.read_bytes()

    result = inspect_sample_dut(
        str(sample),
        str(layermap),
        sample_description="Four-unit NMOS array. Labels S, D, G, B mark terminals.",
        klayout_executable=str(executable),
    )

    assert result["ok"] is True
    assert result["layout_read_count"] == 1
    assert result["input_layout_modified"] is False
    assert result["sample"]["path"] == str(sample.resolve())
    assert result["sample"]["dbu_um"] == pytest.approx(0.001)
    assert result["sample"]["top_cell"] == "SAMPLE_DUT"
    assert result["sample"]["cell_count"] == 2
    assert result["sample"]["snapshot_sha256"] == hashlib.sha256(original).hexdigest()
    assert sample.read_bytes() == original

    assert result["layermap_coverage"]["mapped_roles_present"] == [
        "active",
        "contact",
        "label",
        "m1",
        "poly",
    ]
    assert result["layermap_coverage"]["mapped_roles_absent"] == []
    assert result["layermap_coverage"]["unmapped_used_layers"] == []
    assert result["layermap_coverage"]["role_inference_performed"] is False
    assert {record["string"] for record in result["texts"]["records"]} == {
        "S",
        "D",
        "G",
        "B",
    }
    cells = {cell["name"]: cell for cell in result["cells"]}
    assert cells["SAMPLE_DUT"]["direct_instance_count"] == 4
    assert cells["SAMPLE_DUT"]["child_cells"] == ["TR_UNIT"]
    assert result["pcell_readiness"]["production_ready"] is False
    assert result["pcell_readiness"]["sample_description_received"] is True


def test_inspect_sample_dut_reports_unmapped_used_layer_without_guessing(tmp_path) -> None:
    executable, sample, layermap = _create_sample(tmp_path, "unmapped_layer=1")

    result = inspect_sample_dut(
        str(sample), str(layermap), klayout_executable=str(executable)
    )

    assert result["ok"] is True
    assert result["layermap_coverage"]["unmapped_used_layers"] == [
        {"layer": 99, "datatype": 7}
    ]
    assert "Sample device and parameter explanation is missing." in result[
        "pcell_readiness"
    ]["blockers"]


def test_inspect_sample_dut_requires_explicit_top_when_ambiguous(tmp_path) -> None:
    executable, sample, layermap = _create_sample(tmp_path, "second_top=1")

    result = inspect_sample_dut(
        str(sample), str(layermap), klayout_executable=str(executable)
    )

    assert result["ok"] is False
    assert result["code"] == "TOP_CELL_AMBIGUOUS"
    assert sorted(result["details"]["top_cells"]) == ["EXTRA_TOP", "SAMPLE_DUT"]
    assert "sample DUT" in result["next_action"]


def test_inspect_sample_dut_uses_sample_specific_missing_error(tmp_path) -> None:
    layermap = tmp_path / "layers.yaml"
    _write_layermap(layermap)

    result = inspect_sample_dut(str(tmp_path / "missing.gds"), str(layermap))

    assert result["ok"] is False
    assert result["code"] == "SAMPLE_LAYOUT_NOT_FOUND"
    assert "sample_layout_path" in result["details"]


def test_inspect_sample_dut_rejects_incomplete_worker_response(
    tmp_path, monkeypatch
) -> None:
    sample = tmp_path / "sample.gds"
    layermap = tmp_path / "layers.yaml"
    sample.write_bytes(b"sample")
    _write_layermap(layermap)
    monkeypatch.setattr(server, "run_klayout_worker", lambda *args, **kwargs: {"ok": True})

    result = inspect_sample_dut(str(sample), str(layermap))

    assert result["ok"] is False
    assert result["code"] == "KLAYOUT_RESPONSE_INVALID"
    assert "layout" in result["details"]["missing_keys"]
