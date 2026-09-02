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


def _main_effect_model_spec(*parameter_names: str, interaction: bool = False):
    terms = [{"term_id": "intercept", "kind": "intercept"}]
    terms.extend(
        {
            "term_id": f"main:{parameter_name}",
            "kind": "main_effect",
            "parameter": parameter_name,
        }
        for parameter_name in parameter_names
    )
    if interaction:
        terms.append(
            {
                "term_id": "interaction:" + "*".join(parameter_names),
                "kind": "interaction",
                "parameters": list(parameter_names),
            }
        )
    return {"schema_version": 1, "basis_terms": terms}


def _compiler_identity(corpus_result, *, compiler_id="fixture-regenerator"):
    return {
        "compiler_id": compiler_id,
        "compiler_version": "1",
        "compiler_code_sha256": "f" * 64,
        "compiler_model_spec_sha256": canonical_sha256(
            corpus_result["corpus"]["compiler_model_spec"]
        ),
    }


class TrustedQualificationAuthority:
    authority_id = "device-qualification-board-v1"
    trusted = True

    def __init__(
        self,
        *,
        minimum_aggregate_score=1.0,
        exact_fingerprint_required=True,
        required_metrics=("active.width_um",),
    ):
        self.minimum_aggregate_score = minimum_aggregate_score
        self.exact_fingerprint_required = exact_fingerprint_required
        self.required_metrics = list(required_metrics)

    def issue_policy(self, *, corpus_sha256, compiler_identity, available_metrics):
        policy = {
            "schema_version": 1,
            "artifact_type": "AdapterQualificationPolicy",
            "authority_id": self.authority_id,
            "policy_id": "transistor-geometry-qualification",
            "policy_version": "1",
            "absolute_tolerance": 1e-12,
            "relative_tolerance": 1e-12,
            "minimum_aggregate_score": self.minimum_aggregate_score,
            "exact_fingerprint_required": self.exact_fingerprint_required,
            "required_metrics": self.required_metrics,
        }
        receipt = {
            "approved": True,
            "authority_id": self.authority_id,
            "policy_sha256": canonical_sha256(policy),
            "corpus_sha256": corpus_sha256,
            "compiler_identity_sha256": canonical_sha256(compiler_identity),
            "approved_by": "device-team://qualification-board",
            "signature_or_attestation_verified": True,
            "revocation_checked": True,
            "not_revoked": True,
        }
        receipt["approval_receipt_sha256"] = canonical_sha256(receipt)
        return {"policy_document": policy, "approval_receipt": receipt}

    def verify_policy(
        self,
        *,
        policy_document,
        approval_receipt,
        corpus_sha256,
        compiler_identity,
    ):
        return {
            "verified": True,
            "authority_id": self.authority_id,
            "policy_sha256": canonical_sha256(policy_document),
            "approval_receipt_sha256": approval_receipt[
                "approval_receipt_sha256"
            ],
            "corpus_sha256": corpus_sha256,
            "compiler_identity_sha256": canonical_sha256(compiler_identity),
            "revocation_checked": True,
            "not_revoked": True,
        }


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
        compiler_model_spec=_main_effect_model_spec("gate_length_nm"),
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
        compiler_identity=_compiler_identity(corpus, compiler_id="reference-replay"),
        qualification_policy_authority=TrustedQualificationAuthority(),
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
            qualification_policy_authority=TrustedQualificationAuthority(),
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
        compiler_identity=_compiler_identity(corpus),
        qualification_policy_authority=TrustedQualificationAuthority(),
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
        qualification_policy_authority=TrustedQualificationAuthority(),
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
            qualification_policy_authority=TrustedQualificationAuthority(),
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
            qualification_policy_authority=TrustedQualificationAuthority(),
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
            qualification_policy_authority=TrustedQualificationAuthority(),
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
            qualification_policy_authority=TrustedQualificationAuthority(),
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
            qualification_policy_authority=TrustedQualificationAuthority(),
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
        compiler_model_spec=_main_effect_model_spec("gate_length_nm"),
        dut_records=records,
        layer_roles={"active": {"layer": 2, "datatype": 0}, "gate": {"layer": 6, "datatype": 0}},
        validation_dut_ids=["D3"],
        package_root=tmp_path / "corpora",
        worker_runner=worker,
    )

    assert result["clarification_required"] is True
    issue_codes = {
        issue["code"] for issue in result["clarification_request"]["issues"]
    }
    assert "DUT_COMPILER_BASIS_NOT_IDENTIFIABLE" in issue_codes
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

    def worker_with_fingerprints(
        fingerprints, *, dbu_um=0.001, geometry_scale=1.0
    ):
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
                            "width_um": geometry_scale,
                            "height_um": geometry_scale,
                            "area_um2": geometry_scale * geometry_scale,
                            "bbox_um": [0, 0, geometry_scale, geometry_scale],
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
        compiler_model_spec=_main_effect_model_spec("gate_length_nm"),
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
        compiler_identity=_compiler_identity(corpus, compiler_id="test-compiler"),
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
    assert (
        candidate_rejected.value.code
        == "ADAPTER_CANDIDATE_QUALIFICATION_POLICY_REQUIRED"
    )

    qualification_score = score_reproduced_corpus(
        corpus_package_path=corpus["package_path"],
        reproduced_layout_path=str(reproduced),
        reproduced_cell_by_dut_id={"D1": "R1", "D2": "R2", "D3": "R3"},
        scoring_policy={
            "absolute_tolerance": 0,
            "relative_tolerance": 0,
            "minimum_aggregate_score": 0,
            "exact_fingerprint_required": False,
        },
        scorecard_root=tmp_path / "qualification-scores",
        compiler_identity=_compiler_identity(corpus, compiler_id="test-compiler"),
        qualification_policy_authority=TrustedQualificationAuthority(
            minimum_aggregate_score=0.01,
            exact_fingerprint_required=False,
        ),
        worker_runner=worker_with_fingerprints(
            ["a", "b", "c"], geometry_scale=100.0
        ),
    )
    assert qualification_score["scorecard"]["policy_class"] == (
        "host_approved_candidate_qualification"
    )
    assert all(
        item["passed"] is False
        and "REQUIRED_METRIC_FAILED:active.width_um" in item["hard_fail_reasons"]
        for item in qualification_score["scorecard"]["per_dut"]
    )

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
            compiler_identity=_compiler_identity(corpus, compiler_id="test-compiler"),
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
        compiler_model_spec=_main_effect_model_spec("gate_length_nm", "cpp_nm"),
        dut_records=records,
        layer_roles={"active": {"layer": 2, "datatype": 0}},
        validation_dut_ids=["D4"],
        package_root=tmp_path / "corpora",
        worker_runner=worker,
    )

    evidence = corpus["corpus"]["identifiability_evidence"]
    issue_codes = {issue["code"] for issue in evidence["issues"]}
    assert evidence["status"] == "blocked"
    assert evidence["normalized_design_matrix_rank"] == 2
    assert issue_codes == {"DUT_COMPILER_BASIS_NOT_IDENTIFIABLE"}
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
            compiler_identity=_compiler_identity(corpus, compiler_id="test-compiler"),
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


def test_identifiability_uses_declared_interaction_basis_not_oat_heuristic(
    tmp_path: Path,
) -> None:
    source = tmp_path / "basis.gds"
    source.write_bytes(b"compiler basis corpus")

    def records(points):
        return [
            {
                "dut_id": f"D{index}",
                "cell_name": f"DUT_{index}",
                "parameters": {"gate_length_nm": length, "cpp_nm": cpp},
                "terminals": {"G": {"layer_role": "active"}},
                "topology": "nmos-core",
            }
            for index, (length, cpp) in enumerate(points, start=1)
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

    common = {
        "source_layout_path": str(source),
        "technology_identity": {"technology": "tech-a", "pdk_revision": "r7"},
        "device_family": "finfet",
        "topology": "nmos-core",
        "parameter_schema": {
            "gate_length_nm": {"unit": "nm", "kind": "continuous"},
            "cpp_nm": {"unit": "nm", "kind": "continuous"},
        },
        "layer_roles": {"active": {"layer": 2, "datatype": 0}},
        "worker_runner": worker,
    }
    baseline_points = [(40, 80), (50, 80), (40, 90), (50, 90)]
    main_only = onboard_dut_corpus(
        **common,
        compiler_model_spec=_main_effect_model_spec("gate_length_nm", "cpp_nm"),
        dut_records=records(baseline_points),
        validation_dut_ids=["D4"],
        package_root=tmp_path / "main-corpora",
    )
    with_interaction = onboard_dut_corpus(
        **common,
        compiler_model_spec=_main_effect_model_spec(
            "gate_length_nm", "cpp_nm", interaction=True
        ),
        dut_records=records(baseline_points),
        validation_dut_ids=["D4"],
        package_root=tmp_path / "interaction-corpora",
    )

    assert main_only["corpus"]["identifiability_evidence"]["status"] == "sufficient"
    assert with_interaction["corpus"]["identifiability_evidence"]["status"] == "blocked"
    assert with_interaction["corpus"]["identifiability_evidence"]["minimum_required_rank"] == 4
    assert with_interaction["corpus"]["identifiability_evidence"]["normalized_design_matrix_rank"] == 3

    general_points = [(40, 80), (45, 93), (53, 84), (61, 107), (70, 120)]
    general_doe = onboard_dut_corpus(
        **common,
        compiler_model_spec=_main_effect_model_spec(
            "gate_length_nm", "cpp_nm", interaction=True
        ),
        dut_records=records(general_points),
        validation_dut_ids=["D5"],
        package_root=tmp_path / "general-corpora",
    )
    evidence = general_doe["corpus"]["identifiability_evidence"]
    assert evidence["status"] == "sufficient"
    assert all(
        result["satisfied"] is False
        for result in evidence["conditional_variation"].values()
    )

    regime_model = _main_effect_model_spec("gate_length_nm", "cpp_nm")
    regime_model["basis_terms"].append(
        {
            "term_id": "regime:cpp>=90",
            "kind": "threshold_indicator",
            "parameter": "cpp_nm",
            "operator": ">=",
            "value": 90,
        }
    )
    blocked_regime = onboard_dut_corpus(
        **common,
        compiler_model_spec=regime_model,
        dut_records=records(baseline_points),
        validation_dut_ids=["D4"],
        package_root=tmp_path / "blocked-regime-corpora",
    )
    covered_regime = onboard_dut_corpus(
        **common,
        compiler_model_spec=regime_model,
        dut_records=records(general_points),
        validation_dut_ids=["D5"],
        package_root=tmp_path / "covered-regime-corpora",
    )
    assert blocked_regime["corpus"]["identifiability_evidence"]["status"] == "blocked"
    assert covered_regime["corpus"]["identifiability_evidence"]["status"] == "sufficient"


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
            compiler_model_spec=_main_effect_model_spec(*parameter_schema),
            dut_records=records,
            layer_roles={"active": {"layer": 2, "datatype": 0}, "gate": {"layer": 6, "datatype": 0}},
            validation_dut_ids=["D3"],
            package_root=tmp_path / "corpora",
        )

    assert caught.value.code == expected_code
