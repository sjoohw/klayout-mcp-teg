"""Labeled multi-DUT onboarding, ambiguity handling, and conformance scoring."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from .errors import AnalysisError
from .file_publication import (
    OutputAlreadyExistsError,
    publication_staging_prefix,
    publish_new_directory,
)
from .klayout_adapter import create_layout_snapshot, run_klayout_worker
from .validation_report import ActionableIssue, ClarificationQuestion, ValidationReport
from .workflow_manifest import canonical_json_bytes, canonical_sha256, immutable_json_copy


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "corpus_onboarding", **dict(details)},
        next_action=next_action,
    )


def _validate_inputs(
    *,
    parameter_schema: Mapping[str, Any],
    dut_records: list[Mapping[str, Any]],
    layer_roles: Mapping[str, Any],
    validation_dut_ids: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(parameter_schema, Mapping) or not parameter_schema:
        _fail(
            "DUT_PARAMETER_SCHEMA_REQUIRED",
            "A named parameter schema with unit and kind is required.",
            details={"field": "parameter_schema", "received_type": type(parameter_schema).__name__},
            next_action="Define every Gate length/CPP/Width/nFin/cell-height parameter and unit.",
        )
    schema = {}
    for name, definition in sorted(parameter_schema.items()):
        if not isinstance(name, str) or not name.strip() or not isinstance(definition, Mapping):
            _fail(
                "DUT_PARAMETER_DEFINITION_INVALID",
                "Each parameter needs a stable name and definition object.",
                details={"field": f"parameter_schema.{name}"},
                next_action="Provide {unit, kind} for this parameter.",
            )
        unit = definition.get("unit")
        kind = definition.get("kind")
        if not isinstance(unit, str) or not unit.strip() or kind not in {"continuous", "integer"}:
            _fail(
                "DUT_PARAMETER_DEFINITION_INVALID",
                "Parameter definition requires a unit and continuous/integer kind.",
                details={"field": f"parameter_schema.{name}", "received": dict(definition)},
                next_action="Specify the physical unit and numeric parameter kind.",
            )
        schema[name] = {"unit": unit.strip(), "kind": kind}
    if not isinstance(dut_records, list) or len(dut_records) < 2:
        _fail(
            "DUT_CORPUS_INSUFFICIENT",
            "At least two labeled DUT examples are required.",
            details={"field": "dut_records", "received": len(dut_records) if isinstance(dut_records, list) else None, "minimum": 2},
            next_action="Provide multiple DUT cells and one complete parameter row per DUT.",
        )
    normalized = []
    ids = set()
    for index, record in enumerate(dut_records):
        field = f"dut_records[{index}]"
        if not isinstance(record, Mapping):
            _fail(
                "DUT_RECORD_INVALID",
                "Every DUT record must be an object.",
                details={"field": field, "received_type": type(record).__name__},
                next_action="Provide dut_id, cell_name, parameters, terminals, and topology.",
            )
        dut_id = record.get("dut_id")
        cell_name = record.get("cell_name")
        parameters = record.get("parameters")
        terminals = record.get("terminals")
        if not isinstance(dut_id, str) or not dut_id.strip() or dut_id in ids:
            _fail(
                "DUT_ID_INVALID",
                "dut_id must be a unique non-empty string.",
                details={"field": f"{field}.dut_id", "value": dut_id},
                next_action="Assign one stable unique DUT ID per labeled example.",
            )
        if not isinstance(cell_name, str) or not cell_name.strip():
            _fail(
                "DUT_CELL_NAME_REQUIRED",
                "Each DUT needs its exact source cell name.",
                details={"field": f"{field}.cell_name", "value": cell_name, "dut_id": dut_id},
                next_action="Choose the exact DUT cell from the source layout inventory.",
            )
        if not isinstance(parameters, Mapping):
            _fail(
                "DUT_PARAMETER_ROW_REQUIRED",
                "Each DUT needs one complete parameter row.",
                details={"field": f"{field}.parameters", "dut_id": dut_id},
                next_action="Provide a numeric value for every declared parameter.",
            )
        missing = sorted(set(schema).difference(parameters))
        unexpected = sorted(set(parameters).difference(schema))
        invalid = sorted(
            name
            for name, value in parameters.items()
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
        )
        if missing or unexpected or invalid:
            _fail(
                "DUT_PARAMETER_ROW_INVALID",
                "A DUT parameter row is incomplete or contains a non-numeric value.",
                details={"field": f"{field}.parameters", "dut_id": dut_id, "missing": missing, "unexpected": unexpected, "invalid": invalid},
                next_action="Match the parameter schema exactly and provide finite numeric values.",
            )
        if not isinstance(terminals, Mapping) or not terminals:
            _fail(
                "DUT_TERMINAL_MAPPING_REQUIRED",
                "Each DUT requires explicit terminal names and layer/landing references.",
                details={"field": f"{field}.terminals", "dut_id": dut_id},
                next_action="Provide the G/D/S/B (or device-specific) terminal mapping; it is never inferred silently.",
            )
        ids.add(dut_id)
        normalized.append(
            {
                "dut_id": dut_id.strip(),
                "cell_name": cell_name.strip(),
                "parameters": {name: float(parameters[name]) for name in schema},
                "terminals": immutable_json_copy(terminals),
                "topology": str(record.get("topology", "unspecified")),
            }
        )
    if not isinstance(layer_roles, Mapping) or not layer_roles:
        _fail(
            "DUT_LAYER_ROLES_REQUIRED",
            "Semantic layer roles are required for corpus comparison.",
            details={"field": "layer_roles"},
            next_action="Map gate/active/contact/metals and other required roles to layer/datatype.",
        )
    roles = {}
    for role, layer in sorted(layer_roles.items()):
        if not isinstance(layer, Mapping) or not isinstance(layer.get("layer"), int) or not isinstance(layer.get("datatype"), int):
            _fail(
                "DUT_LAYER_ROLE_INVALID",
                "Each layer role requires integer layer and datatype.",
                details={"field": f"layer_roles.{role}", "received": layer},
                next_action="Provide the exact semantic layermap entry.",
            )
        roles[str(role)] = {"layer": layer["layer"], "datatype": layer["datatype"]}
    holdout = set(validation_dut_ids)
    unknown = sorted(holdout.difference(ids))
    if not holdout or unknown or holdout == ids:
        _fail(
            "DUT_CORPUS_HOLDOUT_INVALID",
            "A non-empty logical validation subset distinct from training DUTs is required before fitting.",
            details={"field": "validation_dut_ids", "unknown": unknown, "dut_ids": sorted(ids)},
            next_action="Select at least one existing DUT for logical validation and leave at least one training DUT.",
        )
    return schema, normalized, roles


def _flatten_metrics(observation: Mapping[str, Any]) -> dict[str, float]:
    flattened = {}
    for role, metrics in sorted(observation["layer_metrics"].items()):
        if not metrics.get("present"):
            flattened[f"{role}.present"] = 0.0
            continue
        flattened[f"{role}.present"] = 1.0
        for name in ("polygon_count", "width_um", "height_um", "area_um2"):
            flattened[f"{role}.{name}"] = float(metrics[name])
    return flattened


def _coverage(schema, records):
    return {
        name: {
            "unit": schema[name]["unit"],
            "kind": schema[name]["kind"],
            "minimum": min(record["parameters"][name] for record in records),
            "maximum": max(record["parameters"][name] for record in records),
            "distinct_count": len({record["parameters"][name] for record in records}),
        }
        for name in schema
    }


def onboard_dut_corpus(
    *,
    source_layout_path: str,
    technology_identity: Mapping[str, Any],
    device_family: str,
    topology: str,
    parameter_schema: Mapping[str, Any],
    dut_records: list[Mapping[str, Any]],
    layer_roles: Mapping[str, Any],
    validation_dut_ids: list[str],
    package_root: str | Path,
    expected_dbu_um: float | None = None,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
    worker_runner=run_klayout_worker,
) -> dict[str, Any]:
    """Create a labeled corpus artifact and explicit ambiguity questions."""

    schema, records, roles = _validate_inputs(
        parameter_schema=parameter_schema,
        dut_records=dut_records,
        layer_roles=layer_roles,
        validation_dut_ids=validation_dut_ids,
    )
    if not isinstance(technology_identity, Mapping) or not technology_identity:
        _fail(
            "DUT_CORPUS_TECHNOLOGY_REQUIRED",
            "Exact technology/PDK identity is required for a reusable corpus.",
            details={"field": "technology_identity"},
            next_action="Provide the exact technology and PDK revision identity.",
        )
    root = Path(package_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with create_layout_snapshot(source_layout_path, purpose="layout") as snapshot:
        analysis = worker_runner(
            {
                "operation": "inspect_dut_corpus",
                "layout_path": str(snapshot.path),
                "dut_records": [{"dut_id": record["dut_id"], "cell_name": record["cell_name"]} for record in records],
                "layer_roles": roles,
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if not analysis.get("ok"):
            return analysis
        if expected_dbu_um is not None and abs(float(analysis["dbu_um"]) - float(expected_dbu_um)) > 1e-15:
            _fail(
                "DUT_CORPUS_DBU_MISMATCH",
                "Corpus DBU differs from the exact technology DBU.",
                details={"field": "expected_dbu_um", "expected": expected_dbu_um, "received": analysis["dbu_um"]},
                next_action="Use a corpus stream with the registered technology DBU.",
            )
        observations_by_id = {item["dut_id"]: item for item in analysis["observations"]}
        enriched = []
        for record in records:
            observation = observations_by_id.get(record["dut_id"])
            if observation is None:
                _fail(
                    "DUT_CORPUS_WORKER_RESPONSE_INCOMPLETE",
                    "KLayout did not return one observation for every labeled DUT.",
                    details={"dut_id": record["dut_id"]},
                    next_action="Inspect the source layout and KLayout worker response.",
                )
            enriched.append({**record, **observation, "flat_metrics": _flatten_metrics(observation)})
        duplicate_groups = defaultdict(list)
        for record in enriched:
            key = tuple((name, record["parameters"][name]) for name in sorted(schema))
            duplicate_groups[key].append(record)
        ambiguities = []
        questions = []
        for group_index, group in enumerate(duplicate_groups.values(), start=1):
            fingerprints = {record["geometry_fingerprint_sha256"] for record in group}
            if len(group) > 1 and len(fingerprints) > 1:
                ambiguity_id = f"same-parameters-different-geometry-{group_index:03d}"
                dut_ids = [record["dut_id"] for record in group]
                ambiguities.append(
                    {
                        "ambiguity_id": ambiguity_id,
                        "dut_ids": dut_ids,
                        "parameters": group[0]["parameters"],
                        "geometry_fingerprints": {record["dut_id"]: record["geometry_fingerprint_sha256"] for record in group},
                    }
                )
                questions.append(
                    ClarificationQuestion(
                        question_id=ambiguity_id,
                        question="같은 parameter인데 geometry가 다릅니다. 신규 DUT는 어느 reference DUT의 drawing을 따를까요?",
                        reason="The difference may be an accepted drawing precedent or an unintended edit; the system cannot choose safely.",
                        answer_schema={"type": "string", "enum": dut_ids},
                        options=tuple(
                            {"value": dut_id, "impact": f"Use {dut_id} as the binding drawing precedent for this parameter point."}
                            for dut_id in dut_ids
                        ),
                    )
                )
        training = [record for record in enriched if record["dut_id"] not in set(validation_dut_ids)]
        identifiability_issues = []
        for parameter_name in sorted(schema):
            distinct = sorted(
                {record["parameters"][parameter_name] for record in training}
            )
            if len(distinct) < 2:
                identifiability_issues.append(
                    ActionableIssue(
                        code="DUT_PARAMETER_NOT_IDENTIFIABLE",
                        category="coverage_or_identifiability",
                        severity="blocker",
                        stage="corpus_onboarding",
                        message=(
                            f"{parameter_name} does not vary in the training examples, so its "
                            "geometry dependencies cannot be learned."
                        ),
                        field_path=f"/parameter_schema/{parameter_name}",
                        received={"distinct_training_values": distinct},
                        expected={"minimum_distinct_training_values": 2, "unit": schema[parameter_name]["unit"]},
                        reason="A fixed training value is indistinguishable from drawing style or an unrelated constant.",
                        fix="Add independently varied labeled DUT examples before compiling this parameter.",
                    )
                )
        all_metric_names = sorted(set.intersection(*(set(record["flat_metrics"]) for record in training)))
        invariants = []
        for metric in all_metric_names:
            values = [record["flat_metrics"][metric] for record in training]
            if max(values) - min(values) <= 1e-12:
                invariants.append({"metric": metric, "value": values[0], "supporting_dut_ids": [record["dut_id"] for record in training]})
        partition = {
            "train_dut_ids": [record["dut_id"] for record in training],
            "validation_dut_ids": list(validation_dut_ids),
            "split_policy": "user_selected_before_model_fit",
            # The preserved source stream and metadata still contain every DUT.
            # This partition prevents accidental fitting in this module, but it
            # is not a permission boundary and must not be sold as a sealed eval.
            "isolation_level": "logical_partition_only_source_geometry_visible",
            "generalization_claim_allowed": False,
        }
        corpus = {
            "schema_version": 1,
            "artifact_type": "DutCorpusArtifact",
            "technology_identity": immutable_json_copy(technology_identity),
            "device_family": device_family,
            "topology": topology,
            "source_layout_sha256": snapshot.sha256,
            "source_size_bytes": snapshot.size_bytes,
            "dbu_um": analysis["dbu_um"],
            "parameter_schema": schema,
            "layer_roles": roles,
            "dut_records": enriched,
            "coverage_matrix": _coverage(schema, enriched),
            "partition": partition,
            "drawing_style_profile": {
                "invariant_metrics": invariants,
                "scope": {"technology_identity": immutable_json_copy(technology_identity), "device_family": device_family, "topology": topology},
                "application_policy": "apply_only_after_ambiguity_resolution_and_holdout_gate",
            },
            "unexplained_variations": ambiguities,
        }
        corpus["corpus_fingerprint_sha256"] = canonical_sha256(corpus)
        package_sha256 = canonical_sha256(corpus)
        final = root / package_sha256
        staging = Path(tempfile.mkdtemp(prefix=publication_staging_prefix("dut-corpus", directory=True), dir=root))
        try:
            suffix = snapshot.source_path.suffix.lower() or ".gds"
            shutil.copyfile(snapshot.path, staging / f"source{suffix}")
            (staging / "corpus.json").write_bytes(canonical_json_bytes(corpus))
            try:
                publish_new_directory(staging, final)
            except OutputAlreadyExistsError:
                if not final.joinpath("corpus.json").is_file() or final.joinpath("corpus.json").read_bytes() != canonical_json_bytes(corpus):
                    _fail(
                        "DUT_CORPUS_PACKAGE_COLLISION",
                        "Existing corpus package differs at the same content address.",
                        details={"path": str(final)},
                        next_action="Quarantine and restore the corpus registry.",
                    )
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    clarification = ValidationReport.build(
        summary=(
            f"corpus onboarding에서 blocker {len(identifiability_issues)}건과 사용자 결정 {len(questions)}건이 남았습니다."
            if questions or identifiability_issues
            else "corpus onboarding이 완료되었으며 unresolved geometry variation이 없습니다."
        ),
        issues=identifiability_issues,
        questions=tuple(questions),
        next_action=(
            "Add missing parameter coverage and resolve every drawing-precedent question before fitting."
            if questions or identifiability_issues
            else "Fit on the training IDs and score the separate logical validation IDs."
        ),
        retry_stage="corpus_resolution",
        resume_token=package_sha256,
    ).to_dict()
    return {
        "ok": True,
        "corpus": corpus,
        "corpus_sha256": package_sha256,
        "package_path": str(final),
        "clarification_required": bool(questions or identifiability_issues),
        "clarification_request": clarification,
        "production_ready": False,
    }


def _load_corpus_package(package_path: str | Path) -> tuple[Path, dict[str, Any], str]:
    package = Path(package_path).expanduser().resolve()
    corpus_path = package / "corpus.json"
    sources = sorted(package.glob("source.*"))
    if not corpus_path.is_file() or len(sources) != 1:
        _fail(
            "DUT_CORPUS_PACKAGE_INVALID",
            "Corpus package must contain corpus.json and one preserved source stream.",
            details={"field": "package_path", "value": str(package)},
            next_action="Restore the exact content-addressed corpus package.",
        )
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "DUT_CORPUS_PACKAGE_INVALID",
            "Corpus package metadata is unreadable.",
            details={"field": "package_path", "error_type": type(exc).__name__},
            next_action="Restore the exact content-addressed corpus package.",
        )
    source_sha256 = hashlib.sha256(sources[0].read_bytes()).hexdigest()
    if source_sha256 != corpus.get("source_layout_sha256"):
        _fail(
            "DUT_CORPUS_SOURCE_HASH_MISMATCH",
            "Preserved corpus stream differs from its recorded SHA-256.",
            details={"expected": corpus.get("source_layout_sha256"), "received": source_sha256},
            next_action="Restore the exact content-addressed corpus package.",
        )
    corpus_sha256 = canonical_sha256(corpus)
    if package.name != corpus_sha256:
        _fail(
            "DUT_CORPUS_PACKAGE_ADDRESS_MISMATCH",
            "Corpus package directory does not match corpus content.",
            details={"expected": corpus_sha256, "received": package.name},
            next_action="Use the canonical corpus package path.",
        )
    return package, corpus, corpus_sha256


def _publish_json_package(*, root: Path, kind: str, document_name: str, document: Mapping[str, Any]) -> tuple[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    digest = canonical_sha256(document)
    final = root / digest
    staging = Path(tempfile.mkdtemp(prefix=publication_staging_prefix(kind, directory=True), dir=root))
    try:
        (staging / document_name).write_bytes(canonical_json_bytes(document))
        try:
            publish_new_directory(staging, final)
        except OutputAlreadyExistsError:
            target = final / document_name
            if not target.is_file() or target.read_bytes() != canonical_json_bytes(document):
                _fail(
                    "DUT_CORPUS_CONTENT_ADDRESS_COLLISION",
                    "Existing corpus-derived artifact differs at the same content address.",
                    details={"path": str(final)},
                    next_action="Quarantine and restore the corpus artifact registry.",
                )
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return digest, final


def resolve_corpus_variations(
    *,
    corpus_package_path: str | Path,
    decisions: Mapping[str, str],
    resolution_root: str | Path,
    resolved_by: str,
    resolved_at: str,
) -> dict[str, Any]:
    """Bind every unexplained variation to an explicit human-selected precedent."""

    _, corpus, corpus_sha256 = _load_corpus_package(corpus_package_path)
    ambiguities = corpus.get("unexplained_variations", [])
    expected = {item["ambiguity_id"]: item for item in ambiguities}
    missing = sorted(set(expected).difference(decisions))
    unexpected = sorted(set(decisions).difference(expected))
    invalid = sorted(
        question_id
        for question_id, selected in decisions.items()
        if question_id in expected and selected not in expected[question_id]["dut_ids"]
    )
    if missing or unexpected or invalid:
        _fail(
            "DUT_CORPUS_RESOLUTION_INCOMPLETE",
            "Variation decisions must answer every current ambiguity with one listed DUT.",
            details={"field": "decisions", "missing": missing, "unexpected": unexpected, "invalid": invalid},
            next_action="Answer each stable ambiguity ID using one offered reference DUT ID.",
        )
    if not isinstance(resolved_by, str) or not resolved_by.strip() or not isinstance(resolved_at, str) or not resolved_at.strip():
        _fail(
            "DUT_CORPUS_RESOLUTION_PROVENANCE_REQUIRED",
            "Resolution requires a reviewer reference and timestamp.",
            details={"field": "resolution_provenance"},
            next_action="Record who selected the precedent and when.",
        )
    manifest = {
        "schema_version": 1,
        "artifact_type": "CorpusResolutionManifest",
        "corpus_sha256": corpus_sha256,
        "decisions": [
            {
                "ambiguity_id": ambiguity_id,
                "selected_reference_dut_id": decisions[ambiguity_id],
                "candidate_dut_ids": expected[ambiguity_id]["dut_ids"],
            }
            for ambiguity_id in sorted(expected)
        ],
        "resolved_by": resolved_by.strip(),
        "resolved_at": resolved_at.strip(),
        "unresolved_blocker_count": 0,
    }
    digest, path = _publish_json_package(
        root=Path(resolution_root).expanduser().resolve(),
        kind="corpus-resolution",
        document_name="resolution.json",
        document=manifest,
    )
    return {"ok": True, "resolution": manifest, "resolution_sha256": digest, "package_path": str(path)}


def _metric_similarity(reference: float, candidate: float, absolute_tolerance: float, relative_tolerance: float) -> tuple[float, float, bool]:
    delta = abs(candidate - reference)
    allowed = max(absolute_tolerance, abs(reference) * relative_tolerance)
    if delta <= allowed:
        return 1.0, delta, True
    scale = max(abs(reference), absolute_tolerance, 1e-15)
    return max(0.0, 1.0 - delta / scale), delta, False


def score_reproduced_corpus(
    *,
    corpus_package_path: str | Path,
    reproduced_layout_path: str,
    reproduced_cell_by_dut_id: Mapping[str, str],
    scoring_policy: Mapping[str, Any],
    scorecard_root: str | Path,
    compiler_identity: Mapping[str, Any],
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
    worker_runner=run_klayout_worker,
) -> dict[str, Any]:
    """Score a reproduced layout against training and logical-validation DUTs."""

    _, corpus, corpus_sha256 = _load_corpus_package(corpus_package_path)
    required_policy = {"absolute_tolerance", "relative_tolerance", "minimum_aggregate_score", "exact_fingerprint_required"}
    missing_policy = sorted(required_policy.difference(scoring_policy))
    if missing_policy:
        _fail(
            "CORPUS_SCORING_POLICY_INCOMPLETE",
            "Conformance scoring policy is incomplete.",
            details={"field": "scoring_policy", "missing": missing_policy},
            next_action="Set explicit tolerances, aggregate threshold, and fingerprint policy before scoring.",
        )
    absolute_tolerance = float(scoring_policy["absolute_tolerance"])
    relative_tolerance = float(scoring_policy["relative_tolerance"])
    threshold = float(scoring_policy["minimum_aggregate_score"])
    if any(not math.isfinite(value) or value < 0 for value in (absolute_tolerance, relative_tolerance, threshold)) or threshold > 1:
        _fail(
            "CORPUS_SCORING_POLICY_INVALID",
            "Scoring tolerances must be non-negative and aggregate threshold must be within 0..1.",
            details={"field": "scoring_policy", "received": dict(scoring_policy)},
            next_action="Correct the explicit numeric scoring thresholds.",
        )
    reference_by_id = {record["dut_id"]: record for record in corpus["dut_records"]}
    missing_cells = sorted(set(reference_by_id).difference(reproduced_cell_by_dut_id))
    unexpected_cells = sorted(set(reproduced_cell_by_dut_id).difference(reference_by_id))
    if missing_cells or unexpected_cells:
        _fail(
            "REPRODUCED_CORPUS_CELL_MAP_INCOMPLETE",
            "Reproduced layout cell mapping must cover every reference DUT exactly.",
            details={"field": "reproduced_cell_by_dut_id", "missing": missing_cells, "unexpected": unexpected_cells},
            next_action="Map every reference DUT ID to its reproduced output cell.",
        )
    with create_layout_snapshot(reproduced_layout_path, purpose="layout") as snapshot:
        reference_source_replayed = snapshot.sha256 == corpus.get(
            "source_layout_sha256"
        )
        analysis = worker_runner(
            {
                "operation": "inspect_dut_corpus",
                "layout_path": str(snapshot.path),
                "dut_records": [
                    {"dut_id": dut_id, "cell_name": reproduced_cell_by_dut_id[dut_id]}
                    for dut_id in sorted(reference_by_id)
                ],
                "layer_roles": corpus["layer_roles"],
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if not analysis.get("ok"):
            return analysis
        candidate_by_id = {item["dut_id"]: item for item in analysis["observations"]}
        holdout = set(
            corpus["partition"].get(
                "validation_dut_ids",
                corpus["partition"].get("sealed_holdout_dut_ids", []),
            )
        )
        per_dut = []
        for dut_id in sorted(reference_by_id):
            reference = reference_by_id[dut_id]
            candidate = candidate_by_id[dut_id]
            reference_metrics = reference["flat_metrics"]
            candidate_metrics = _flatten_metrics(candidate)
            metric_names = sorted(set(reference_metrics) | set(candidate_metrics))
            metrics = []
            for metric in metric_names:
                if metric not in reference_metrics or metric not in candidate_metrics:
                    metrics.append({"metric": metric, "status": "missing", "score": 0.0, "hard_fail": True})
                    continue
                score, delta, passed = _metric_similarity(
                    float(reference_metrics[metric]),
                    float(candidate_metrics[metric]),
                    absolute_tolerance,
                    relative_tolerance,
                )
                metrics.append({
                    "metric": metric,
                    "reference": reference_metrics[metric],
                    "candidate": candidate_metrics[metric],
                    "absolute_delta": delta,
                    "score": score,
                    "status": "passed" if passed else "failed",
                    "hard_fail": False,
                })
            fingerprint_match = reference["geometry_fingerprint_sha256"] == candidate["geometry_fingerprint_sha256"]
            metric_score = sum(item["score"] for item in metrics) / len(metrics) if metrics else 0.0
            exact_required = bool(scoring_policy["exact_fingerprint_required"])
            dut_score = metric_score if not exact_required or fingerprint_match else 0.0
            per_dut.append({
                "dut_id": dut_id,
                "cohort": "logical_validation" if dut_id in holdout else "train_reference",
                "reference_geometry_fingerprint_sha256": reference["geometry_fingerprint_sha256"],
                "candidate_geometry_fingerprint_sha256": candidate["geometry_fingerprint_sha256"],
                "exact_geometry_match": fingerprint_match,
                "metrics": metrics,
                "aggregate_score": dut_score,
                "passed": dut_score >= threshold and not any(item["hard_fail"] for item in metrics),
            })
    cohorts = {}
    for cohort in ("train_reference", "logical_validation"):
        items = [item for item in per_dut if item["cohort"] == cohort]
        score = sum(item["aggregate_score"] for item in items) / len(items) if items else 0.0
        cohorts[cohort] = {
            "dut_count": len(items),
            "aggregate_score": score,
            "passed": bool(items) and score >= threshold and all(item["passed"] for item in items),
        }
    scorecard = {
        "schema_version": 1,
        "artifact_type": "AdapterConformanceScorecard",
        "corpus_sha256": corpus_sha256,
        "partition_sha256": canonical_sha256(corpus["partition"]),
        "scoring_policy": immutable_json_copy(scoring_policy),
        "scoring_policy_sha256": canonical_sha256(scoring_policy),
        "compiler_identity": immutable_json_copy(compiler_identity),
        "reproduced_layout_sha256": snapshot.sha256,
        "reference_source_replayed": reference_source_replayed,
        "evidence_class": (
            "reference_replay_not_reproduction"
            if reference_source_replayed
            else "independent_stream_logical_validation"
        ),
        "holdout_isolation_level": corpus["partition"].get(
            "isolation_level", "legacy_unspecified"
        ),
        "generalization_claim_allowed": False,
        "per_dut": per_dut,
        "cohorts": cohorts,
        "all_required_cohorts_passed": (
            not reference_source_replayed
            and all(item["passed"] for item in cohorts.values())
        ),
        "scoring_scope": "recursive_polygon_fingerprint_and_layer_geometry_metrics",
        "foundry_drc_lvs_pex_included": False,
        "production_ready": False,
    }
    digest, path = _publish_json_package(
        root=Path(scorecard_root).expanduser().resolve(),
        kind="corpus-score",
        document_name="scorecard.json",
        document=scorecard,
    )
    return {"ok": True, "scorecard": scorecard, "scorecard_sha256": digest, "package_path": str(path)}


def build_technology_adapter_candidate(
    *,
    corpus_package_path: str | Path,
    resolution_package_path: str | Path,
    scorecard_package_path: str | Path,
    adapter_identity: Mapping[str, Any],
    compiler_code_sha256: str,
    adapter_root: str | Path,
) -> dict[str, Any]:
    """Package a scored candidate without claiming foundry qualification."""

    _, corpus, corpus_sha256 = _load_corpus_package(corpus_package_path)
    try:
        resolution = json.loads(Path(resolution_package_path).joinpath("resolution.json").read_text(encoding="utf-8"))
        scorecard = json.loads(Path(scorecard_package_path).joinpath("scorecard.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "ADAPTER_CANDIDATE_EVIDENCE_MISSING",
            "Resolution or scorecard package is unreadable.",
            details={"error_type": type(exc).__name__},
            next_action="Provide the exact content-addressed resolution and scorecard packages.",
        )
    if resolution.get("corpus_sha256") != corpus_sha256 or scorecard.get("corpus_sha256") != corpus_sha256:
        _fail(
            "ADAPTER_CANDIDATE_EVIDENCE_MISMATCH",
            "Resolution and scorecard must bind to the same exact corpus.",
            details={"expected": corpus_sha256, "resolution": resolution.get("corpus_sha256"), "scorecard": scorecard.get("corpus_sha256")},
            next_action="Regenerate evidence from the exact immutable corpus package.",
        )
    if not scorecard.get("all_required_cohorts_passed"):
        if scorecard.get("reference_source_replayed"):
            _fail(
                "REFERENCE_LAYOUT_REPLAY_NOT_REPRODUCTION_EVIDENCE",
                "The original corpus stream was submitted as its own reproduced output.",
                details={
                    "field": "scorecard.reference_source_replayed",
                    "received": True,
                },
                next_action=(
                    "Run the candidate compiler to create a separate output stream, "
                    "then score that output."
                ),
            )
        _fail(
            "REFERENCE_SCORE_BELOW_GATE",
            "The candidate did not pass every train/reference and logical-validation score gate.",
            details={"field": "scorecard.cohorts", "received": scorecard.get("cohorts")},
            next_action="Correct the compiler, reproduce the corpus again, and create a new scorecard.",
        )
    if not isinstance(compiler_code_sha256, str) or len(compiler_code_sha256) != 64:
        _fail(
            "ADAPTER_COMPILER_HASH_INVALID",
            "compiler_code_sha256 must pin the exact candidate implementation.",
            details={"field": "compiler_code_sha256", "value": compiler_code_sha256},
            next_action="Provide the lowercase SHA-256 of the reviewed compiler code artifact.",
        )
    package = {
        "schema_version": 1,
        "identity": immutable_json_copy(adapter_identity),
        "artifact_type": "TechnologyAdapterPackage",
        "status": "candidate_scored_logical_validation_not_foundry_qualified",
        "corpus_sha256": corpus_sha256,
        "partition_sha256": canonical_sha256(corpus["partition"]),
        "drawing_style_profile_sha256": canonical_sha256(corpus["drawing_style_profile"]),
        "resolution_manifest_sha256": canonical_sha256(resolution),
        "scorecard_sha256": canonical_sha256(scorecard),
        "compiler_code_sha256": compiler_code_sha256,
        "parameter_schema": corpus["parameter_schema"],
        "coverage_matrix": corpus["coverage_matrix"],
        "technology_identity": corpus["technology_identity"],
        "device_family": corpus["device_family"],
        "topology": corpus["topology"],
        "foundry_drc_lvs_pex_included": False,
        "production_ready": False,
    }
    digest, path = _publish_json_package(
        root=Path(adapter_root).expanduser().resolve(),
        kind="tech-adapter",
        document_name="package.json",
        document=package,
    )
    return {
        "ok": True,
        "package": package,
        "package_sha256": digest,
        "package_path": str(path),
        "production_ready": False,
        "next_gate": "Register exact identity/hash, then run foundry DRC/LVS/PEX pilot qualification.",
    }
