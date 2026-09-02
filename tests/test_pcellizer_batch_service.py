from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
from pathlib import Path
import threading

import pytest

from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.pcellizer_batch import plan_pcellizer_split_batch
from klayout_mcp.pcellizer_batch_service import (
    generate_pcellizer_split_batch_service,
    inspect_pcellizer_batch_package,
)
from klayout_mcp.pcellizer_intent import build_pcellizer_parameter_intent
from klayout_mcp.pcellizer_recipe import compile_pcellizer_single_shape_recipe
from klayout_mcp.pcellizer_snapshot import create_pcellizer_snapshot_package
from klayout_mcp.workflow_manifest import canonical_sha256


def _source_and_capture(tmp_path: Path) -> tuple[Path, dict, str]:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    root = Path(__file__).resolve().parents[1]
    source_path = tmp_path / "batch-source.gds"
    capture_path = tmp_path / "capture.json"
    script = Path(__file__).parent / "fixtures" / "create_pcellizer_batch_source.py"
    completed = subprocess.run(
        [
            str(executable), "-b", "-r", str(script),
            "-rd", f"project_root={root}",
            "-rd", f"source_path={source_path}",
            "-rd", f"capture_path={capture_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return source_path, json.loads(capture_path.read_text(encoding="utf-8")), str(executable)


def _chain(tmp_path: Path, *, table_text: str | None = None):
    _, capture, executable = _source_and_capture(tmp_path)
    snapshot = create_pcellizer_snapshot_package(
        capture=capture, package_root=str(tmp_path / "snapshot-store")
    )
    intent = build_pcellizer_parameter_intent(
        snapshot_package_sha256=snapshot["manifest"]["snapshot_package_sha256"],
        parameter_name="gate_length",
        min_um=0.05,
        nominal_um=0.1,
        max_um=0.2,
        step_um=0.05,
        dbu_um=0.001,
        manufacturing_grid_um=0.005,
        dimension_semantics="longitudinal_length",
        anchor_policy="p1_fixed",
    )
    recipe = compile_pcellizer_single_shape_recipe(
        package_dir=snapshot["package_dir"], parameter_intent=intent
    )
    plan = plan_pcellizer_split_batch(
        recipe=recipe,
        table_text=table_text or (
            "split_id\tgate_length_nm\tmeta.corner\n"
            "short\t50\tA\n"
            "nominal\t100\tB\n"
            "long\t200\tC\n"
            "nominal_repeat\t100\tD\n"
        ),
    )
    return snapshot, recipe, plan, executable


def _verify_layout(executable: str, layout_path: Path, result_path: Path) -> dict:
    script = Path(__file__).parent / "fixtures" / "verify_pcellizer_batch_output.py"
    completed = subprocess.run(
        [
            executable, "-b", "-r", str(script),
            "-rd", f"layout_path={layout_path}",
            "-rd", f"result_path={result_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_batch_generates_one_or_many_hierarchy_safe_gds_and_verifies_hashes(tmp_path) -> None:
    snapshot, recipe, plan, executable = _chain(tmp_path)

    generated = generate_pcellizer_split_batch_service(
        package_dir=snapshot["package_dir"],
        recipe=recipe,
        batch_plan=plan,
        output_root=str(tmp_path / "outputs"),
        klayout_executable=executable,
    )
    inspected = inspect_pcellizer_batch_package(batch_dir=generated["batch_dir"])

    assert inspected["fresh_file_hashes_verified"] is True
    assert len(inspected["outputs"]) == 4
    assert inspected["manifest"]["summary"]["unique_variant_count"] == 3
    assert inspected["outputs"][3]["reused_identical_variant"] is True
    assert inspected["outputs"][1]["layout_sha256"] == inspected["outputs"][3]["layout_sha256"]
    expected_widths = {"short.gds": 50, "nominal.gds": 100, "long.gds": 200}
    for filename, width in expected_widths.items():
        report = _verify_layout(
            executable,
            Path(generated["batch_dir"]) / filename,
            tmp_path / f"{filename}.json",
        )
        assert report["dbu_um"] == 0.001
        assert report["top_bbox_dbu"] == [0, 0, 2_000_000, 60_000]
        assert report["direct_instance_count"] == 2
        assert report["occurrence_boxes_dbu"] == [
            [1_000, 5_000, 1_000 + width, 5_050],
            [2_000, 5_000, 2_100, 5_050],
        ]
        assert report["top_cells"] == ["TOP"]

    second = generate_pcellizer_split_batch_service(
        package_dir=snapshot["package_dir"],
        recipe=recipe,
        batch_plan=plan,
        output_root=str(tmp_path / "outputs"),
        klayout_executable=executable,
    )
    assert second["batch_dir"] == generated["batch_dir"]


def test_batch_concurrent_same_plan_directory_publish_is_idempotent(tmp_path) -> None:
    snapshot, recipe, plan, executable = _chain(tmp_path)
    output_root = tmp_path / "outputs"
    barrier = threading.Barrier(2)

    def generate() -> dict:
        barrier.wait()
        return generate_pcellizer_split_batch_service(
            package_dir=snapshot["package_dir"],
            recipe=recipe,
            batch_plan=plan,
            output_root=str(output_root),
            klayout_executable=executable,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: generate(), range(2)))

    assert all(result["ok"] is True for result in results)
    assert results[0]["batch_dir"] == results[1]["batch_dir"]
    assert inspect_pcellizer_batch_package(batch_dir=results[0]["batch_dir"])[
        "fresh_file_hashes_verified"
    ] is True
    assert list(
        (output_root / "pcellizer-batches").glob(
            ".klayout-stage-dir-pcellizer-batch-*"
        )
    ) == []


def test_batch_package_detects_output_tampering(tmp_path) -> None:
    snapshot, recipe, plan, executable = _chain(tmp_path)
    generated = generate_pcellizer_split_batch_service(
        package_dir=snapshot["package_dir"],
        recipe=recipe,
        batch_plan=plan,
        output_root=str(tmp_path / "outputs"),
        klayout_executable=executable,
    )
    output = Path(generated["batch_dir"]) / "short.gds"
    output.write_bytes(b"tampered")

    from klayout_mcp.errors import AnalysisError

    with pytest.raises(AnalysisError) as error:
        inspect_pcellizer_batch_package(batch_dir=generated["batch_dir"])
    assert error.value.code == "PCELLIZER_BATCH_OUTPUT_HASH_MISMATCH"


def test_batch_runs_nominal_xor_even_when_table_omits_nominal(tmp_path) -> None:
    snapshot, recipe, plan, executable = _chain(
        tmp_path,
        table_text="split_id,gate_length_nm\nshort,50\nlong,200\n",
    )
    generated = generate_pcellizer_split_batch_service(
        package_dir=snapshot["package_dir"],
        recipe=recipe,
        batch_plan=plan,
        output_root=str(tmp_path / "outputs"),
        klayout_executable=executable,
    )

    assert generated["manifest"]["summary"]["nominal_xor_verified"] is True
    assert not (Path(generated["batch_dir"]) / ".nominal_verify.gds").exists()


def test_batch_package_detects_manifest_chain_rebinding(tmp_path) -> None:
    snapshot, recipe, plan, executable = _chain(tmp_path)
    generated = generate_pcellizer_split_batch_service(
        package_dir=snapshot["package_dir"],
        recipe=recipe,
        batch_plan=plan,
        output_root=str(tmp_path / "outputs"),
        klayout_executable=executable,
    )
    manifest_path = Path(generated["batch_dir"]) / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("pcellizer_batch_manifest_sha256")
    manifest["table_raw_sha256"] = "0" * 64
    manifest["pcellizer_batch_manifest_sha256"] = canonical_sha256(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    from klayout_mcp.errors import AnalysisError

    with pytest.raises(AnalysisError) as error:
        inspect_pcellizer_batch_package(batch_dir=generated["batch_dir"])
    assert error.value.code == "INVALID_PCELLIZER_BATCH_MANIFEST"
