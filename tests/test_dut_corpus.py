import json
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
from klayout_mcp.workflow_manifest import canonical_json_bytes, canonical_sha256


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


def _write_json_package(root: Path, document_name: str, document: dict) -> Path:
    package = root / canonical_sha256(document)
    package.mkdir(parents=True)
    (package / document_name).write_bytes(canonical_json_bytes(document))
    return package


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
        compiler_identity={"compiler_id": "reference-replay", "compiler_version": "1", "compiler_code_sha256": "f" * 64},
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
        compiler_identity={"compiler_id": "fixture-regenerator", "compiler_version": "1", "compiler_code_sha256": "f" * 64},
        klayout_executable=executable,
    )
    assert score["scorecard"]["reference_source_replayed"] is False
    assert score["scorecard"]["evidence_class"] == "distinct_stream_logical_validation_no_execution_receipt"
    assert score["scorecard"]["compiler_execution_receipt_verified"] is False
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

    with pytest.raises(AnalysisError) as invalid_hash:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=resolution["package_path"],
            scorecard_package_path=score["package_path"],
            adapter_identity=_identity(),
            compiler_code_sha256="z" * 64,
            adapter_root=tmp_path / "invalid-hash-adapters",
        )
    assert invalid_hash.value.code == "ADAPTER_COMPILER_HASH_INVALID"

    with pytest.raises(AnalysisError) as compiler_mismatch:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=resolution["package_path"],
            scorecard_package_path=score["package_path"],
            adapter_identity=_identity(),
            compiler_code_sha256="e" * 64,
            adapter_root=tmp_path / "compiler-mismatch-adapters",
        )
    assert compiler_mismatch.value.code == "ADAPTER_CANDIDATE_SCORECARD_BINDING_INVALID"

    with pytest.raises(AnalysisError) as identity_mismatch:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=resolution["package_path"],
            scorecard_package_path=score["package_path"],
            adapter_identity={**_identity(), "topology": "pmos-core"},
            compiler_code_sha256="f" * 64,
            adapter_root=tmp_path / "identity-mismatch-adapters",
        )
    assert identity_mismatch.value.code == "ADAPTER_CANDIDATE_IDENTITY_MISMATCH"

    forged_resolution = dict(resolution["resolution"])
    forged_resolution["unresolved_blocker_count"] = 999
    forged_resolution_path = _write_json_package(
        tmp_path / "forged-resolutions", "resolution.json", forged_resolution
    )
    with pytest.raises(AnalysisError) as unresolved:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=forged_resolution_path,
            scorecard_package_path=score["package_path"],
            adapter_identity=_identity(),
            compiler_code_sha256="f" * 64,
            adapter_root=tmp_path / "unresolved-adapters",
        )
    assert unresolved.value.code == "ADAPTER_CANDIDATE_RESOLUTION_INVALID"

    forged_scorecard = dict(score["scorecard"])
    forged_scorecard["per_dut"] = []
    forged_scorecard_path = _write_json_package(
        tmp_path / "forged-scores", "scorecard.json", forged_scorecard
    )
    with pytest.raises(AnalysisError) as arbitrary_pass:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=resolution["package_path"],
            scorecard_package_path=forged_scorecard_path,
            adapter_identity=_identity(),
            compiler_code_sha256="f" * 64,
            adapter_root=tmp_path / "forged-score-adapters",
        )
    assert arbitrary_pass.value.code == "ADAPTER_CANDIDATE_SCORECARD_STRUCTURE_INVALID"


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
        layer_roles={"active": {"layer": 2, "datatype": 0}, "gate": {"layer": 6, "datatype": 0}},
        validation_dut_ids=["D3"],
        package_root=tmp_path / "corpora",
        worker_runner=worker,
    )

    assert result["clarification_required"] is True
    assert result["clarification_request"]["issues"][0]["code"] == "DUT_PARAMETER_NOT_IDENTIFIABLE"
    question = result["clarification_request"]["questions"][0]
    assert question["question_id"].startswith("same-parameters-different-geometry")
    assert {option["value"] for option in question["options"]} == {"D1", "D2"}


def test_exact_fingerprint_and_dbu_are_threshold_independent_hard_gates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus.gds"
    source.write_bytes(b"reference corpus")
    reproduced = tmp_path / "reproduced.gds"
    reproduced.write_bytes(b"compiler output")

    def worker_with_fingerprints(fingerprints, *, dbu_um=0.001):
        def worker(request, **kwargs):
            observations = [
                {
                    "dut_id": record["dut_id"],
                    "cell_name": record["cell_name"],
                    "geometry_fingerprint_sha256": fingerprints[index] * 64,
                    "bbox_um": [0, 0, 1, 1],
                    "layer_metrics": {
                        "active": {
                            "present": True,
                            "polygon_count": 1,
                            "width_um": 1.0,
                            "height_um": 1.0,
                            "area_um2": 1.0,
                            "bbox_um": [0, 0, 1, 1],
                        }
                    },
                }
                for index, record in enumerate(request["dut_records"])
            ]
            return {
                "ok": True,
                "dbu_um": dbu_um,
                "observations": observations,
                "layout_cell_count": len(observations),
            }

        return worker

    corpus = onboard_dut_corpus(
        source_layout_path=str(source),
        technology_identity={"technology": "tech-a", "pdk_revision": "r7"},
        device_family="finfet",
        topology="nmos-core",
        parameter_schema={"gate_length_nm": {"unit": "nm", "kind": "continuous"}},
        dut_records=_records(),
        layer_roles={
            "active": {"layer": 2, "datatype": 0},
            "gate": {"layer": 6, "datatype": 0},
        },
        validation_dut_ids=["D3"],
        package_root=tmp_path / "corpora",
        worker_runner=worker_with_fingerprints(["1", "2", "3"]),
    )
    score = score_reproduced_corpus(
        corpus_package_path=corpus["package_path"],
        reproduced_layout_path=str(reproduced),
        reproduced_cell_by_dut_id={"D1": "R1", "D2": "R2", "D3": "R3"},
        scoring_policy={
            "absolute_tolerance": 0,
            "relative_tolerance": 0,
            "minimum_aggregate_score": 0,
            "exact_fingerprint_required": True,
        },
        scorecard_root=tmp_path / "scores",
        compiler_identity={
            "compiler_id": "test-compiler",
            "compiler_version": "1",
            "compiler_code_sha256": "f" * 64,
        },
        worker_runner=worker_with_fingerprints(["a", "b", "c"]),
    )

    assert score["scorecard"]["all_required_cohorts_passed"] is False
    assert all(item["passed"] is False for item in score["scorecard"]["per_dut"])
    assert all(
        item["hard_fail_reasons"] == ["EXACT_GEOMETRY_FINGERPRINT_MISMATCH"]
        for item in score["scorecard"]["per_dut"]
    )
    resolution = resolve_corpus_variations(
        corpus_package_path=corpus["package_path"],
        decisions={},
        resolution_root=tmp_path / "resolutions",
        resolved_by="reviewer://device-team",
        resolved_at="2026-09-02T00:00:00Z",
    )
    with pytest.raises(AnalysisError) as candidate_rejected:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=resolution["package_path"],
            scorecard_package_path=score["package_path"],
            adapter_identity=_identity(),
            compiler_code_sha256="f" * 64,
            adapter_root=tmp_path / "adapters",
        )
    assert candidate_rejected.value.code == "REFERENCE_SCORE_BELOW_GATE"

    with pytest.raises(AnalysisError) as dbu_mismatch:
        score_reproduced_corpus(
            corpus_package_path=corpus["package_path"],
            reproduced_layout_path=str(reproduced),
            reproduced_cell_by_dut_id={"D1": "R1", "D2": "R2", "D3": "R3"},
            scoring_policy={
                "absolute_tolerance": 0,
                "relative_tolerance": 0,
                "minimum_aggregate_score": 0,
                "exact_fingerprint_required": False,
            },
            scorecard_root=tmp_path / "dbu-scores",
            compiler_identity={
                "compiler_id": "test-compiler",
                "compiler_version": "1",
                "compiler_code_sha256": "f" * 64,
            },
            worker_runner=worker_with_fingerprints(["1", "2", "3"], dbu_um=0.002),
        )

    assert dbu_mismatch.value.code == "REPRODUCED_CORPUS_DBU_MISMATCH"


def test_collinear_doe_blockers_are_persisted_and_block_downstream_use(
    tmp_path: Path,
) -> None:
    source = tmp_path / "collinear.gds"
    source.write_bytes(b"collinear corpus")
    records = [
        {
            "dut_id": f"D{index}",
            "cell_name": f"DUT_{index}",
            "parameters": {"gate_length_nm": length, "cpp_nm": cpp},
            "terminals": {"G": {"layer_role": "active"}},
            "topology": "nmos-core",
        }
        for index, (length, cpp) in enumerate(
            ((40, 90), (50, 110), (60, 130), (70, 150)), start=1
        )
    ]

    def worker(request, **kwargs):
        observations = [
            {
                "dut_id": record["dut_id"],
                "cell_name": record["cell_name"],
                "geometry_fingerprint_sha256": f"{index:x}" * 64,
                "bbox_um": [0, 0, 1, 1],
                "layer_metrics": {
                    "active": {
                        "present": True,
                        "polygon_count": 1,
                        "width_um": 1.0,
                        "height_um": 1.0,
                        "area_um2": 1.0,
                        "bbox_um": [0, 0, 1, 1],
                    }
                },
            }
            for index, record in enumerate(request["dut_records"], start=1)
        ]
        return {
            "ok": True,
            "dbu_um": 0.001,
            "observations": observations,
            "layout_cell_count": len(observations),
        }

    corpus = onboard_dut_corpus(
        source_layout_path=str(source),
        technology_identity={"technology": "tech-a", "pdk_revision": "r7"},
        device_family="finfet",
        topology="nmos-core",
        parameter_schema={
            "gate_length_nm": {"unit": "nm", "kind": "continuous"},
            "cpp_nm": {"unit": "nm", "kind": "continuous"},
        },
        dut_records=records,
        layer_roles={"active": {"layer": 2, "datatype": 0}},
        validation_dut_ids=["D4"],
        package_root=tmp_path / "corpora",
        worker_runner=worker,
    )

    evidence = corpus["corpus"]["identifiability_evidence"]
    issue_codes = {issue["code"] for issue in evidence["issues"]}
    assert evidence["status"] == "blocked"
    assert evidence["normalized_design_matrix_rank"] == 1
    assert "DUT_PARAMETER_EFFECTS_CONFOUNDED" in issue_codes
    assert "DUT_PARAMETER_CONDITIONAL_VARIATION_MISSING" in issue_codes
    persisted = json.loads(
        (Path(corpus["package_path"]) / "corpus.json").read_text(encoding="utf-8")
    )
    assert persisted["identifiability_evidence"] == evidence

    with pytest.raises(AnalysisError) as scoring_blocked:
        score_reproduced_corpus(
            corpus_package_path=corpus["package_path"],
            reproduced_layout_path=str(tmp_path / "unused.gds"),
            reproduced_cell_by_dut_id={record["dut_id"]: record["cell_name"] for record in records},
            scoring_policy={
                "absolute_tolerance": 0,
                "relative_tolerance": 0,
                "minimum_aggregate_score": 0,
                "exact_fingerprint_required": False,
            },
            scorecard_root=tmp_path / "scores",
            compiler_identity={
                "compiler_id": "test-compiler",
                "compiler_version": "1",
                "compiler_code_sha256": "f" * 64,
            },
        )

    assert scoring_blocked.value.code == "DUT_CORPUS_IDENTIFIABILITY_BLOCKED"

    with pytest.raises(AnalysisError) as candidate_blocked:
        build_technology_adapter_candidate(
            corpus_package_path=corpus["package_path"],
            resolution_package_path=tmp_path / "unused-resolution",
            scorecard_package_path=tmp_path / "unused-score",
            adapter_identity=_identity(),
            compiler_code_sha256="f" * 64,
            adapter_root=tmp_path / "adapters",
        )

    assert candidate_blocked.value.code == "DUT_CORPUS_IDENTIFIABILITY_BLOCKED"


@pytest.mark.parametrize(
    ("parameter_schema", "records", "topology", "expected_code"),
    [
        (
            {"nfin": {"unit": "count", "kind": "integer"}},
            [
                {**record, "parameters": {"nfin": 2.5}}
                for record in _records()
            ],
            "nmos-core",
            "DUT_PARAMETER_ROW_INVALID",
        ),
        (
            {"gate_length_nm": {"unit": "nm", "kind": "continuous"}},
            [{**record, "terminals": {"G": {}}} for record in _records()],
            "nmos-core",
            "DUT_TERMINAL_MAPPING_INVALID",
        ),
        (
            {"gate_length_nm": {"unit": "nm", "kind": "continuous"}},
            [{**record, "topology": "pmos-core"} for record in _records()],
            "nmos-core",
            "DUT_TOPOLOGY_MISMATCH",
        ),
    ],
)
def test_corpus_declared_kinds_terminals_and_topology_fail_closed(
    tmp_path: Path, parameter_schema, records, topology, expected_code
) -> None:
    with pytest.raises(AnalysisError) as caught:
        onboard_dut_corpus(
            source_layout_path=str(tmp_path / "unused.gds"),
            technology_identity={"technology": "tech-a", "pdk_revision": "r7"},
            device_family="finfet",
            topology=topology,
            parameter_schema=parameter_schema,
            dut_records=records,
            layer_roles={"active": {"layer": 2, "datatype": 0}, "gate": {"layer": 6, "datatype": 0}},
            validation_dut_ids=["D3"],
            package_root=tmp_path / "corpora",
        )

    assert caught.value.code == expected_code
