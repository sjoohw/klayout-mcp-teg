from pathlib import Path
import subprocess

import pytest

from klayout_mcp.dut_corpus import (
    build_technology_adapter_candidate,
    onboard_dut_corpus,
    resolve_corpus_variations,
    score_reproduced_corpus,
)
from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.technology_registry import TechnologyAdapterRegistry


def _records():
    return [
        {
            "dut_id": f"D{index}",
            "cell_name": f"DUT_{length}",
            "parameters": {"gate_length_nm": length},
            "terminals": {"G": {"layer_role": "gate"}, "S": {"layer_role": "active"}, "D": {"layer_role": "active"}, "B": {"layer_role": "active"}},
            "topology": "nmos-core",
        }
        for index, length in enumerate((50, 100, 150), start=1)
    ]


def _identity():
    return {
        "technology": "tech-a",
        "pdk_revision": "r7",
        "adapter_kind": "transistor",
        "device_family": "finfet",
        "topology": "nmos-core",
        "package_version": "1.0.0",
    }


def _create_source(tmp_path: Path) -> tuple[Path, str]:
    try:
        executable = find_klayout_executable()
    except AnalysisError:
        pytest.skip("KLayout executable is not installed")
    source = tmp_path / "corpus.gds"
    script = Path(__file__).parent / "fixtures" / "create_dut_corpus.py"
    completed = subprocess.run(
        [str(executable), "-b", "-r", str(script), "-rd", f"output_path={source}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return source, str(executable)


def _create_reproduced(tmp_path: Path, executable: str) -> Path:
    reproduced = tmp_path / "reproduced.gds"
    script = Path(__file__).parent / "fixtures" / "create_dut_corpus.py"
    completed = subprocess.run(
        [
            executable,
            "-b",
            "-r",
            str(script),
            "-rd",
            f"output_path={reproduced}",
            "-rd",
            "variant_marker=7",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return reproduced


def test_labeled_corpus_round_trip_scoring_and_candidate_package(tmp_path: Path) -> None:
    source, executable = _create_source(tmp_path)
    corpus = onboard_dut_corpus(
        source_layout_path=str(source),
        technology_identity={"technology": "tech-a", "pdk_revision": "r7"},
        device_family="finfet",
        topology="nmos-core",
        parameter_schema={"gate_length_nm": {"unit": "nm", "kind": "continuous"}},
        dut_records=_records(),
        layer_roles={"active": {"layer": 2, "datatype": 0}, "gate": {"layer": 6, "datatype": 0}},
        validation_dut_ids=["D3"],
        package_root=tmp_path / "corpora",
        expected_dbu_um=0.001,
        klayout_executable=executable,
    )
    assert corpus["clarification_required"] is False
    invariant_names = {item["metric"] for item in corpus["corpus"]["drawing_style_profile"]["invariant_metrics"]}
    assert "active.width_um" in invariant_names
    resolution = resolve_corpus_variations(
        corpus_package_path=corpus["package_path"],
        decisions={},
        resolution_root=tmp_path / "resolutions",
        resolved_by="reviewer://device-team",
        resolved_at="2026-09-02T00:00:00Z",
    )
    replay_score = score_reproduced_corpus(
        corpus_package_path=corpus["package_path"],
        reproduced_layout_path=str(source),
        reproduced_cell_by_dut_id={"D1": "DUT_50", "D2": "DUT_100", "D3": "DUT_150"},
        scoring_policy={
            "absolute_tolerance": 1e-12,
            "relative_tolerance": 1e-12,
            "minimum_aggregate_score": 1.0,
            "exact_fingerprint_required": True,
        },
        scorecard_root=tmp_path / "scores",
        compiler_identity={"compiler_id": "reference-replay", "compiler_version": "1"},
        klayout_executable=executable,
    )
    assert replay_score["scorecard"]["reference_source_replayed"] is True
    assert replay_score["scorecard"]["all_required_cohorts_passed"] is False
    with pytest.raises(AnalysisError) as replay_error:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=resolution["package_path"],
            scorecard_package_path=replay_score["package_path"],
            adapter_identity=_identity(),
            compiler_code_sha256="f" * 64,
            adapter_root=tmp_path / "replay-adapters",
        )
    assert replay_error.value.code == "REFERENCE_LAYOUT_REPLAY_NOT_REPRODUCTION_EVIDENCE"

    reproduced = _create_reproduced(tmp_path, executable)
    score = score_reproduced_corpus(
        corpus_package_path=corpus["package_path"],
        reproduced_layout_path=str(reproduced),
        reproduced_cell_by_dut_id={"D1": "DUT_50", "D2": "DUT_100", "D3": "DUT_150"},
        scoring_policy={
            "absolute_tolerance": 1e-12,
            "relative_tolerance": 1e-12,
            "minimum_aggregate_score": 1.0,
            "exact_fingerprint_required": True,
        },
        scorecard_root=tmp_path / "scores",
        compiler_identity={"compiler_id": "fixture-regenerator", "compiler_version": "1"},
        klayout_executable=executable,
    )
    assert score["scorecard"]["reference_source_replayed"] is False
    assert score["scorecard"]["cohorts"]["logical_validation"]["passed"] is True
    candidate = build_technology_adapter_candidate(
        corpus_package_path=corpus["package_path"],
        resolution_package_path=resolution["package_path"],
        scorecard_package_path=score["package_path"],
        adapter_identity=_identity(),
        compiler_code_sha256="f" * 64,
        adapter_root=tmp_path / "adapters",
    )
    assert candidate["package"]["status"] == "candidate_scored_logical_validation_not_foundry_qualified"
    registry = TechnologyAdapterRegistry()
    registered = registry.register_package(candidate["package"])
    assert registry.resolve(_identity(), expected_package_sha256=registered["package_sha256"])["fallback_used"] is False


def test_same_parameters_different_geometry_requires_reference_choice(tmp_path: Path) -> None:
    source = tmp_path / "corpus.gds"
    source.write_bytes(b"corpus")
    records = _records()
    records[1]["parameters"] = {"gate_length_nm": 50}

    def worker(request, **kwargs):
        observations = []
        for index, record in enumerate(request["dut_records"]):
            observations.append(
                {
                    "dut_id": record["dut_id"],
                    "cell_name": record["cell_name"],
                    "geometry_fingerprint_sha256": str(index + 1) * 64,
                    "bbox_um": [0, 0, 1, 1],
                    "layer_metrics": {
                        "active": {"present": True, "polygon_count": 1, "width_um": 1.0 + index, "height_um": 1.0, "area_um2": 1.0 + index, "bbox_um": [0, 0, 1 + index, 1]}
                    },
                }
            )
        return {"ok": True, "dbu_um": 0.001, "observations": observations, "layout_cell_count": 3}

    result = onboard_dut_corpus(
        source_layout_path=str(source),
        technology_identity={"technology": "tech-a", "pdk_revision": "r7"},
        device_family="finfet",
        topology="nmos-core",
        parameter_schema={"gate_length_nm": {"unit": "nm", "kind": "continuous"}},
        dut_records=records,
        layer_roles={"active": {"layer": 2, "datatype": 0}},
        validation_dut_ids=["D3"],
        package_root=tmp_path / "corpora",
        worker_runner=worker,
    )

    assert result["clarification_required"] is True
    assert result["clarification_request"]["issues"][0]["code"] == "DUT_PARAMETER_NOT_IDENTIFIABLE"
    question = result["clarification_request"]["questions"][0]
    assert question["question_id"].startswith("same-parameters-different-geometry")
    assert {option["value"] for option in question["options"]} == {"D1", "D2"}
