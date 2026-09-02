import hashlib
import subprocess
from pathlib import Path

import pytest

from klayout_mcp.klayout_adapter import find_klayout_executable, run_klayout_worker
from klayout_mcp.server import inventory_pcellizer_hierarchy


def _create_fixture(tmp_path: Path, executable: Path) -> Path:
    layout_path = tmp_path / "pcellizer-hierarchy.gds"
    fixture = Path(__file__).parent / "fixtures" / "create_pcellizer_hierarchy.py"
    completed = subprocess.run(
        [
            str(executable),
            "-b",
            "-r",
            str(fixture),
            "-rd",
            f"output_path={layout_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return layout_path


def test_h0_inventory_preserves_hierarchy_array_member_and_transform(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    layout_path = _create_fixture(tmp_path, executable)
    before = hashlib.sha256(layout_path.read_bytes()).hexdigest()

    result = run_klayout_worker(
        {
            "operation": "inventory_pcellizer_hierarchy",
            "layout_path": str(layout_path),
            "top_cell": "TOP",
        },
        executable_path=str(executable),
    )

    assert result["ok"] is True
    assert result["layout"]["dbu_um"] == pytest.approx(0.001)
    assert result["summary"] == {
        "occurrence_count": 13,
        "source_array_count": 8,
        "authoring_supported_occurrence_count": 12,
        "authoring_blocked_occurrence_count": 1,
        "flattening_performed": False,
        "geometry_modified": False,
        "composite_dut_membership_inferred": False,
    }
    mid_occurrences = [item for item in result["occurrences"] if item["cell"] == "MID"]
    assert len(mid_occurrences) == 6
    first_mid = next(
        item for item in mid_occurrences if item["array_member"] == {"column": 0, "row": 0}
    )
    # The GDS writer canonicalizes the rotated AREF to a negative a-vector and
    # shifts its base.  H0 records the fresh-reload source representation in DBU.
    assert first_mid["local_transform"]["displacement_dbu"] == [20000, 20000]
    last_mid = next(
        item for item in mid_occurrences if item["array_member"] == {"column": 2, "row": 0}
    )
    assert last_mid["local_transform"]["displacement_dbu"] == [10000, 20000]
    assert {(item["array_member"]["column"], item["array_member"]["row"]) for item in mid_occurrences} == {
        (0, 0),
        (1, 0),
        (2, 0),
        (0, 1),
        (1, 1),
        (2, 1),
    }
    nested_leaf = next(
        item
        for item in result["occurrences"]
        if item["cell"] == "LEAF" and item["depth"] == 2
    )
    assert nested_leaf["local_transform"]["angle_degrees"] == pytest.approx(90)
    assert nested_leaf["local_transform"]["mirror"] is True
    assert nested_leaf["local_transform"]["displacement_dbu"] == [3000, 4000]
    assert nested_leaf["authoring_supported"] is True
    complex_leaf = next(
        item
        for item in result["occurrences"]
        if item["cell"] == "LEAF" and item["depth"] == 1
    )
    assert complex_leaf["authoring_supported"] is False
    assert complex_leaf["local_transform"]["displacement_dbu"] == [50000, 10000]
    assert set(complex_leaf["authoring_blockers"]) == {
        "non_orthogonal_angle",
        "non_unit_magnification",
    }
    assert hashlib.sha256(layout_path.read_bytes()).hexdigest() == before


def test_h0_inventory_is_deterministic_after_fresh_reload(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    layout_path = _create_fixture(tmp_path, executable)
    request = {
        "operation": "inventory_pcellizer_hierarchy",
        "layout_path": str(layout_path),
        "top_cell": "TOP",
    }

    first = run_klayout_worker(request, executable_path=str(executable))
    second = run_klayout_worker(request, executable_path=str(executable))

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["source_arrays"] == second["source_arrays"]
    assert first["occurrences"] == second["occurrences"]


def test_h0_inventory_fails_closed_on_occurrence_limit(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    layout_path = _create_fixture(tmp_path, executable)

    result = run_klayout_worker(
        {
            "operation": "inventory_pcellizer_hierarchy",
            "layout_path": str(layout_path),
            "top_cell": "TOP",
            "max_occurrences": 2,
        },
        executable_path=str(executable),
    )

    assert result["ok"] is False
    assert result["code"] == "PCELLIZER_OCCURRENCE_LIMIT_EXCEEDED"


def test_mcp_h0_tool_binds_inventory_to_immutable_source_snapshot(tmp_path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    layout_path = _create_fixture(tmp_path, executable)
    source_bytes = layout_path.read_bytes()

    result = inventory_pcellizer_hierarchy(
        layout_path=str(layout_path),
        top_cell="TOP",
        klayout_executable=str(executable),
    )

    assert result["ok"] is True
    assert result["layout"]["path"] == str(layout_path.resolve())
    assert result["source_identity"]["layout_sha256"] == hashlib.sha256(
        source_bytes
    ).hexdigest()
    assert result["source_identity"]["source_mutable"] is False
    assert result["snapshot_read_count"] == 1
    assert result["summary"]["flattening_performed"] is False
    assert layout_path.read_bytes() == source_bytes


def test_mcp_h0_tool_validates_limit_before_touching_layout(tmp_path) -> None:
    result = inventory_pcellizer_hierarchy(
        layout_path=str(tmp_path / "missing.gds"),
        max_occurrences=0,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_PCELLIZER_OCCURRENCE_LIMIT"
