from pathlib import Path
import subprocess

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable, run_klayout_worker


def _topology_cases(tmp_path: Path) -> tuple[Path, str]:
    try:
        executable = find_klayout_executable()
    except AnalysisError:
        pytest.skip("KLayout executable is not installed")
    layout_path = tmp_path / "dut-corpus-topology-cases.gds"
    script = Path(__file__).parent / "fixtures" / "create_dut_corpus_topology_cases.py"
    completed = subprocess.run(
        [
            str(executable),
            "-b",
            "-r",
            str(script),
            "-rd",
            f"output_path={layout_path}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return layout_path, str(executable)


def test_fingerprint_keeps_holes_and_terminal_component_evidence(tmp_path: Path) -> None:
    layout_path, executable = _topology_cases(tmp_path)
    terminals = {
        "G": {"layer_role": "active", "landing_bbox_um": [-0.5, -0.1, -0.4, 0.1]},
        "D": {"layer_role": "active", "landing_bbox_um": [0.4, -0.1, 0.5, 0.1]},
    }
    result = run_klayout_worker(
        {
            "operation": "inspect_dut_corpus",
            "layout_path": str(layout_path),
            "layer_roles": {"active": {"layer": 2, "datatype": 0}},
            "dut_records": [
                {"dut_id": "H1", "cell_name": "DONUT_CENTER", "terminals": {}},
                {"dut_id": "H2", "cell_name": "DONUT_SHIFTED", "terminals": {}},
                {"dut_id": "T0", "cell_name": "TERMINALS_CONNECTED", "terminals": terminals},
                {"dut_id": "T1", "cell_name": "TERMINALS_BALANCED", "terminals": terminals},
                {"dut_id": "T2", "cell_name": "TERMINALS_UNBALANCED", "terminals": terminals},
            ],
        },
        executable_path=executable,
        timeout_seconds=30,
    )
    assert result["ok"] is True
    observations = {item["dut_id"]: item for item in result["observations"]}

    first_hole = observations["H1"]
    moved_hole = observations["H2"]
    assert first_hole["geometry_fingerprint_sha256"] != moved_hole["geometry_fingerprint_sha256"]
    assert first_hole["layer_metrics"]["active"]["geometry_fingerprint_sha256"] != moved_hole["layer_metrics"]["active"]["geometry_fingerprint_sha256"]
    for metric in ("polygon_count", "hole_count", "width_um", "height_um", "area_um2"):
        assert first_hole["layer_metrics"]["active"][metric] == moved_hole["layer_metrics"]["active"][metric]

    connected = observations["T0"]
    balanced = observations["T1"]
    unbalanced = observations["T2"]
    for metric in ("polygon_count", "hole_count", "width_um", "height_um", "area_um2"):
        assert balanced["layer_metrics"]["active"][metric] == unbalanced["layer_metrics"]["active"][metric]
    assert balanced["layer_metrics"]["active"]["geometry_fingerprint_sha256"] != unbalanced["layer_metrics"]["active"]["geometry_fingerprint_sha256"]
    assert balanced["terminal_metrics"]["G"]["landing_present"] is True
    assert unbalanced["terminal_metrics"]["G"]["landing_present"] is True
    assert balanced["terminal_metrics"]["G"]["touched_component_area_um2"] != unbalanced["terminal_metrics"]["G"]["touched_component_area_um2"]
    assert balanced["terminal_metrics"]["G"]["touched_component_fingerprint_sha256"] != unbalanced["terminal_metrics"]["G"]["touched_component_fingerprint_sha256"]
    assert balanced["terminal_pair_metrics"]["D__G"]["same_component"] is False
    assert connected["terminal_pair_metrics"]["D__G"]["same_component"] is True
    assert balanced["terminal_connectivity_verified"] is False
