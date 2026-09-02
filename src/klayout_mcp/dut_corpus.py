"""Labeled multi-DUT onboarding, ambiguity handling, and conformance scoring."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
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


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ADAPTER_IDENTITY_FIELDS = frozenset(
    {
        "technology",
        "pdk_revision",
        "adapter_kind",
        "device_family",
        "topology",
        "package_version",
    }
)


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "corpus_onboarding", **dict(details)},
        next_action=next_action,
    )


def _validate_inputs(
    *,
    topology: str,
    parameter_schema: Mapping[str, Any],
    dut_records: list[Mapping[str, Any]],
    layer_roles: Mapping[str, Any],
    validation_dut_ids: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(topology, str) or not topology.strip():
        _fail(
            "DUT_CORPUS_TOPOLOGY_REQUIRED",
            "The corpus requires one explicit non-empty topology.",
            details={"field": "topology", "value": topology},
            next_action="Provide the exact topology shared by every DUT record.",
        )
    normalized_topology = topology.strip()
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
        nonintegral = sorted(
            name
            for name, value in parameters.items()
            if name in schema
            and schema[name]["kind"] == "integer"
            and name not in invalid
            and not float(value).is_integer()
        )
        if missing or unexpected or invalid or nonintegral:
            _fail(
                "DUT_PARAMETER_ROW_INVALID",
                "A DUT parameter row is incomplete or violates its declared numeric kind.",
                details={"field": f"{field}.parameters", "dut_id": dut_id, "missing": missing, "unexpected": unexpected, "invalid": invalid, "nonintegral": nonintegral},
                next_action="Match the parameter schema exactly; integer parameters must be whole numbers.",
            )
        if not isinstance(terminals, Mapping) or not terminals:
            _fail(
                "DUT_TERMINAL_MAPPING_REQUIRED",
                "Each DUT requires explicit terminal names and layer/landing references.",
                details={"field": f"{field}.terminals", "dut_id": dut_id},
                next_action="Provide the G/D/S/B (or device-specific) terminal mapping; it is never inferred silently.",
            )
        invalid_terminals = sorted(
            str(name)
            for name, definition in terminals.items()
            if not isinstance(name, str)
            or not name.strip()
            or not isinstance(definition, Mapping)
            or not isinstance(definition.get("layer_role"), str)
            or not definition.get("layer_role", "").strip()
        )
        if invalid_terminals:
            _fail(
                "DUT_TERMINAL_MAPPING_INVALID",
                "Each terminal requires a non-empty name and layer_role.",
                details={"field": f"{field}.terminals", "dut_id": dut_id, "invalid_terminals": invalid_terminals},
                next_action="Map every terminal to an explicit semantic layer_role.",
            )
        record_topology = record.get("topology")
        if not isinstance(record_topology, str) or record_topology.strip() != normalized_topology:
            _fail(
                "DUT_TOPOLOGY_MISMATCH",
                "Every DUT record must match the corpus topology exactly.",
                details={"field": f"{field}.topology", "dut_id": dut_id, "expected": normalized_topology, "received": record_topology},
                next_action="Correct the DUT topology or onboard it as a separate corpus.",
            )
        ids.add(dut_id)
        normalized.append(
            {
                "dut_id": dut_id.strip(),
                "cell_name": cell_name.strip(),
                "parameters": {
                    name: (
                        int(parameters[name])
                        if schema[name]["kind"] == "integer"
                        else float(parameters[name])
                    )
                    for name in schema
                },
                "terminals": immutable_json_copy(terminals),
                "topology": normalized_topology,
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
    for record in normalized:
        unknown_terminal_roles = sorted(
            {
                definition["layer_role"]
                for definition in record["terminals"].values()
                if definition["layer_role"] not in roles
            }
        )
        if unknown_terminal_roles:
            _fail(
                "DUT_TERMINAL_LAYER_ROLE_UNKNOWN",
                "A terminal refers to a layer_role that is not declared by the corpus.",
                details={"field": "terminals.layer_role", "dut_id": record["dut_id"], "unknown": unknown_terminal_roles, "available": sorted(roles)},
                next_action="Add the semantic layer role or correct the terminal mapping.",
            )
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
        topology=topology,
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
    if not isinstance(corpus, dict) or corpus.get("schema_version") != 1 or corpus.get("artifact_type") != "DutCorpusArtifact":
        _fail(
            "DUT_CORPUS_PACKAGE_SCHEMA_INVALID",
            "Corpus metadata does not match the supported DutCorpusArtifact schema.",
            details={"field": "corpus.json", "schema_version": corpus.get("schema_version") if isinstance(corpus, dict) else None, "artifact_type": corpus.get("artifact_type") if isinstance(corpus, dict) else None},
            next_action="Restore a schema_version 1 DutCorpusArtifact package.",
        )
    required_mappings = (
        "technology_identity",
        "parameter_schema",
        "layer_roles",
        "partition",
        "drawing_style_profile",
    )
    missing_mappings = [name for name in required_mappings if not isinstance(corpus.get(name), Mapping)]
    invalid_scalars = [
        name
        for name in ("device_family", "topology")
        if not isinstance(corpus.get(name), str) or not corpus.get(name, "").strip()
    ]
    if (
        missing_mappings
        or invalid_scalars
        or not isinstance(corpus.get("dut_records"), list)
        or not corpus.get("dut_records")
        or not isinstance(corpus.get("unexplained_variations"), list)
    ):
        _fail(
            "DUT_CORPUS_PACKAGE_SCHEMA_INVALID",
            "Corpus metadata is missing required typed fields.",
            details={"field": "corpus.json", "missing_or_invalid_mappings": missing_mappings, "invalid_scalars": invalid_scalars},
            next_action="Restore the complete content-addressed corpus package.",
        )
    technology_identity = corpus["technology_identity"]
    invalid_technology_fields = [
        name
        for name in ("technology", "pdk_revision")
        if not isinstance(technology_identity.get(name), str)
        or not technology_identity.get(name, "").strip()
    ]
    if invalid_technology_fields:
        _fail(
            "DUT_CORPUS_PACKAGE_SCHEMA_INVALID",
            "Corpus technology identity is incomplete.",
            details={"field": "technology_identity", "invalid_fields": invalid_technology_fields},
            next_action="Restore exact technology and PDK revision identity.",
        )
    dut_ids: list[str] = []
    for index, record in enumerate(corpus["dut_records"]):
        if (
            not isinstance(record, Mapping)
            or not isinstance(record.get("dut_id"), str)
            or not record.get("dut_id", "").strip()
            or not isinstance(record.get("parameters"), Mapping)
            or not isinstance(record.get("terminals"), Mapping)
            or record.get("topology") != corpus["topology"]
            or not SHA256_PATTERN.fullmatch(str(record.get("geometry_fingerprint_sha256", "")))
            or not isinstance(record.get("flat_metrics"), Mapping)
        ):
            _fail(
                "DUT_CORPUS_PACKAGE_SCHEMA_INVALID",
                "A persisted DUT record is incomplete or inconsistent with the corpus.",
                details={"field": f"dut_records[{index}]"},
                next_action="Restore the exact generated corpus package.",
            )
        dut_ids.append(record["dut_id"])
    if len(dut_ids) != len(set(dut_ids)):
        _fail(
            "DUT_CORPUS_PACKAGE_SCHEMA_INVALID",
            "Persisted DUT IDs are duplicated.",
            details={"field": "dut_records", "dut_ids": dut_ids},
            next_action="Restore a corpus with one unique record per DUT.",
        )
    train_ids = corpus["partition"].get("train_dut_ids")
    validation_ids = corpus["partition"].get(
        "validation_dut_ids",
        corpus["partition"].get("sealed_holdout_dut_ids"),
    )
    if (
        not isinstance(train_ids, list)
        or not isinstance(validation_ids, list)
        or any(not isinstance(item, str) for item in train_ids + validation_ids)
        or len(train_ids) != len(set(train_ids))
        or len(validation_ids) != len(set(validation_ids))
        or set(train_ids) & set(validation_ids)
        or set(train_ids) | set(validation_ids) != set(dut_ids)
    ):
        _fail(
            "DUT_CORPUS_PACKAGE_SCHEMA_INVALID",
            "Corpus partition must cover every DUT exactly once.",
            details={"field": "partition", "dut_ids": sorted(dut_ids), "train_dut_ids": train_ids, "validation_dut_ids": validation_ids},
            next_action="Restore the exact corpus partition manifest.",
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


def _load_json_package(
    package_path: str | Path,
    *,
    document_name: str,
    artifact_type: str,
) -> tuple[Path, dict[str, Any], str]:
    package = Path(package_path).expanduser().resolve()
    document_path = package / document_name
    try:
        document = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "CORPUS_EVIDENCE_PACKAGE_INVALID",
            "A corpus evidence package is missing or unreadable.",
            details={"field": "package_path", "value": str(package), "document_name": document_name, "error_type": type(exc).__name__},
            next_action="Restore the exact content-addressed evidence package.",
        )
    if not isinstance(document, dict) or document.get("schema_version") != 1 or document.get("artifact_type") != artifact_type:
        _fail(
            "CORPUS_EVIDENCE_SCHEMA_INVALID",
            "Corpus evidence metadata has the wrong schema or artifact type.",
            details={"path": str(document_path), "expected_artifact_type": artifact_type, "schema_version": document.get("schema_version") if isinstance(document, dict) else None, "artifact_type": document.get("artifact_type") if isinstance(document, dict) else None},
            next_action="Regenerate the evidence using the supported workflow.",
        )
    digest = canonical_sha256(document)
    if not SHA256_PATTERN.fullmatch(package.name) or package.name != digest:
        _fail(
            "CORPUS_EVIDENCE_PACKAGE_ADDRESS_MISMATCH",
            "Evidence content no longer matches its content-addressed directory.",
            details={"path": str(package), "expected": digest, "received": package.name},
            next_action="Quarantine the modified package and restore the exact original evidence.",
        )
    return package, document, digest


def _validate_resolution_evidence(
    *, corpus: Mapping[str, Any], corpus_sha256: str, resolution: Mapping[str, Any]
) -> None:
    if (
        resolution.get("corpus_sha256") != corpus_sha256
        or resolution.get("unresolved_blocker_count") != 0
        or not isinstance(resolution.get("resolved_by"), str)
        or not resolution.get("resolved_by", "").strip()
        or not isinstance(resolution.get("resolved_at"), str)
        or not resolution.get("resolved_at", "").strip()
    ):
        _fail(
            "ADAPTER_CANDIDATE_RESOLUTION_INVALID",
            "Resolution evidence must bind to this corpus with zero unresolved blockers.",
            details={"expected_corpus_sha256": corpus_sha256, "received_corpus_sha256": resolution.get("corpus_sha256"), "unresolved_blocker_count": resolution.get("unresolved_blocker_count")},
            next_action="Resolve every ambiguity and regenerate the content-addressed resolution package.",
        )
    expected = {
        item["ambiguity_id"]: item
        for item in corpus.get("unexplained_variations", [])
        if isinstance(item, Mapping) and isinstance(item.get("ambiguity_id"), str)
    }
    decisions = resolution.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(expected):
        _fail(
            "ADAPTER_CANDIDATE_RESOLUTION_INVALID",
            "Resolution decisions do not cover every corpus ambiguity exactly.",
            details={"expected_ambiguity_ids": sorted(expected), "decision_count": len(decisions) if isinstance(decisions, list) else None},
            next_action="Regenerate the resolution from the exact corpus package.",
        )
    seen: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, Mapping):
            _fail(
                "ADAPTER_CANDIDATE_RESOLUTION_INVALID",
                "A resolution decision is not an object.",
                details={"received_type": type(decision).__name__},
                next_action="Regenerate the resolution package.",
            )
        ambiguity_id = decision.get("ambiguity_id")
        source = expected.get(ambiguity_id)
        selected = decision.get("selected_reference_dut_id")
        if source is None or ambiguity_id in seen or selected not in source.get("dut_ids", []):
            _fail(
                "ADAPTER_CANDIDATE_RESOLUTION_INVALID",
                "A resolution decision is unknown, duplicated, or selects an invalid DUT.",
                details={"ambiguity_id": ambiguity_id, "selected_reference_dut_id": selected},
                next_action="Select one offered DUT for every exact ambiguity ID.",
            )
        if decision.get("candidate_dut_ids") != source.get("dut_ids"):
            _fail(
                "ADAPTER_CANDIDATE_RESOLUTION_INVALID",
                "Resolution candidate DUTs drifted from the source corpus.",
                details={"ambiguity_id": ambiguity_id, "expected": source.get("dut_ids"), "received": decision.get("candidate_dut_ids")},
                next_action="Regenerate the resolution from the exact corpus package.",
            )
        seen.add(ambiguity_id)


def _validate_scorecard_evidence(
    *,
    corpus: Mapping[str, Any],
    corpus_sha256: str,
    scorecard: Mapping[str, Any],
    compiler_code_sha256: str,
) -> None:
    partition_sha256 = canonical_sha256(corpus["partition"])
    policy = scorecard.get("scoring_policy")
    compiler_identity = scorecard.get("compiler_identity")
    if (
        scorecard.get("corpus_sha256") != corpus_sha256
        or scorecard.get("partition_sha256") != partition_sha256
        or not isinstance(policy, Mapping)
        or scorecard.get("scoring_policy_sha256") != canonical_sha256(policy)
        or not isinstance(compiler_identity, Mapping)
        or scorecard.get("compiler_identity_sha256") != canonical_sha256(compiler_identity)
        or compiler_identity.get("compiler_code_sha256") != compiler_code_sha256
    ):
        _fail(
            "ADAPTER_CANDIDATE_SCORECARD_BINDING_INVALID",
            "Scorecard hashes or compiler identity do not bind to this exact candidate.",
            details={"expected_corpus_sha256": corpus_sha256, "expected_partition_sha256": partition_sha256, "compiler_code_sha256": compiler_code_sha256},
            next_action="Rescore the exact compiler output and use its untouched package.",
        )
    if (
        scorecard.get("reference_source_replayed") is not False
        or scorecard.get("evidence_class") != "distinct_stream_logical_validation_no_execution_receipt"
        or scorecard.get("compiler_execution_receipt_verified") is not False
        or scorecard.get("all_required_cohorts_passed") is not True
    ):
        _fail(
            "ADAPTER_CANDIDATE_SCORECARD_GATE_FAILED",
            "Scorecard is replayed, misclassified, or did not pass every required cohort.",
            details={"reference_source_replayed": scorecard.get("reference_source_replayed"), "evidence_class": scorecard.get("evidence_class"), "all_required_cohorts_passed": scorecard.get("all_required_cohorts_passed")},
            next_action="Run the scorer on a distinct compiler output stream and preserve the resulting package.",
        )
    expected_validation = set(corpus["partition"].get("validation_dut_ids", []))
    expected_ids = {record["dut_id"] for record in corpus["dut_records"]}
    per_dut = scorecard.get("per_dut")
    if not isinstance(per_dut, list) or len(per_dut) != len(expected_ids):
        _fail(
            "ADAPTER_CANDIDATE_SCORECARD_STRUCTURE_INVALID",
            "Scorecard must contain one result for every corpus DUT.",
            details={"expected_dut_ids": sorted(expected_ids), "received_count": len(per_dut) if isinstance(per_dut, list) else None},
            next_action="Regenerate the scorecard from the exact corpus.",
        )
    seen: set[str] = set()
    for item in per_dut:
        dut_id = item.get("dut_id") if isinstance(item, Mapping) else None
        expected_cohort = "logical_validation" if dut_id in expected_validation else "train_reference"
        if (
            dut_id not in expected_ids
            or dut_id in seen
            or item.get("cohort") != expected_cohort
            or item.get("passed") is not True
            or not SHA256_PATTERN.fullmatch(str(item.get("reference_geometry_fingerprint_sha256", "")))
            or not SHA256_PATTERN.fullmatch(str(item.get("candidate_geometry_fingerprint_sha256", "")))
        ):
            _fail(
                "ADAPTER_CANDIDATE_SCORECARD_STRUCTURE_INVALID",
                "A per-DUT score result is missing, duplicated, failed, or bound to the wrong cohort.",
                details={"dut_id": dut_id, "expected_cohort": expected_cohort},
                next_action="Regenerate the scorecard using the exact corpus and compiler output.",
            )
        seen.add(dut_id)
    cohorts = scorecard.get("cohorts")
    if not isinstance(cohorts, Mapping) or set(cohorts) != {"train_reference", "logical_validation"} or any(not isinstance(value, Mapping) or value.get("passed") is not True for value in cohorts.values()):
        _fail(
            "ADAPTER_CANDIDATE_SCORECARD_STRUCTURE_INVALID",
            "Both required scorecard cohorts must exist and pass.",
            details={"cohorts": cohorts},
            next_action="Regenerate the scorecard and correct every failed cohort.",
        )


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
    if (
        not isinstance(compiler_identity, Mapping)
        or not isinstance(compiler_identity.get("compiler_id"), str)
        or not compiler_identity.get("compiler_id", "").strip()
        or not isinstance(compiler_identity.get("compiler_version"), str)
        or not compiler_identity.get("compiler_version", "").strip()
        or not isinstance(compiler_identity.get("compiler_code_sha256"), str)
        or not SHA256_PATTERN.fullmatch(compiler_identity.get("compiler_code_sha256", ""))
    ):
        _fail(
            "CORPUS_SCORING_COMPILER_IDENTITY_INVALID",
            "Scoring requires compiler_id, compiler_version, and an exact lowercase code SHA-256.",
            details={"field": "compiler_identity", "received": dict(compiler_identity) if isinstance(compiler_identity, Mapping) else compiler_identity},
            next_action="Identify the exact compiler implementation before scoring its output.",
        )
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
        "compiler_identity_sha256": canonical_sha256(compiler_identity),
        "reproduced_layout_sha256": snapshot.sha256,
        "reference_source_replayed": reference_source_replayed,
        "evidence_class": (
            "reference_replay_not_reproduction"
            if reference_source_replayed
            else "distinct_stream_logical_validation_no_execution_receipt"
        ),
        "compiler_execution_receipt_verified": False,
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
    _, resolution, _ = _load_json_package(
        resolution_package_path,
        document_name="resolution.json",
        artifact_type="CorpusResolutionManifest",
    )
    _, scorecard, _ = _load_json_package(
        scorecard_package_path,
        document_name="scorecard.json",
        artifact_type="AdapterConformanceScorecard",
    )
    _validate_resolution_evidence(
        corpus=corpus,
        corpus_sha256=corpus_sha256,
        resolution=resolution,
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
    if not isinstance(compiler_code_sha256, str) or not SHA256_PATTERN.fullmatch(compiler_code_sha256):
        _fail(
            "ADAPTER_COMPILER_HASH_INVALID",
            "compiler_code_sha256 must pin the exact candidate implementation.",
            details={"field": "compiler_code_sha256", "value": compiler_code_sha256},
            next_action="Provide the lowercase SHA-256 of the reviewed compiler code artifact.",
        )
    _validate_scorecard_evidence(
        corpus=corpus,
        corpus_sha256=corpus_sha256,
        scorecard=scorecard,
        compiler_code_sha256=compiler_code_sha256,
    )
    if not isinstance(adapter_identity, Mapping) or set(adapter_identity) != ADAPTER_IDENTITY_FIELDS:
        _fail(
            "ADAPTER_CANDIDATE_IDENTITY_INVALID",
            "Adapter identity must contain the exact registered identity fields.",
            details={"field": "adapter_identity", "missing": sorted(ADAPTER_IDENTITY_FIELDS.difference(adapter_identity)) if isinstance(adapter_identity, Mapping) else sorted(ADAPTER_IDENTITY_FIELDS), "unexpected": sorted(set(adapter_identity).difference(ADAPTER_IDENTITY_FIELDS)) if isinstance(adapter_identity, Mapping) else []},
            next_action="Provide the exact technology, PDK, kind, family, topology, and package version.",
        )
    expected_identity = {
        "technology": corpus["technology_identity"].get("technology"),
        "pdk_revision": corpus["technology_identity"].get("pdk_revision"),
        "adapter_kind": "transistor",
        "device_family": corpus["device_family"],
        "topology": corpus["topology"],
    }
    mismatches = {
        field: {"expected": expected, "received": adapter_identity.get(field)}
        for field, expected in expected_identity.items()
        if not isinstance(adapter_identity.get(field), str)
        or not adapter_identity.get(field, "").strip()
        or adapter_identity.get(field) != expected
    }
    if not isinstance(adapter_identity.get("package_version"), str) or not adapter_identity.get("package_version", "").strip():
        mismatches["package_version"] = {"expected": "non-empty exact version", "received": adapter_identity.get("package_version")}
    if mismatches:
        _fail(
            "ADAPTER_CANDIDATE_IDENTITY_MISMATCH",
            "Adapter identity does not match the exact corpus technology and topology.",
            details={"field": "adapter_identity", "mismatches": mismatches},
            next_action="Use an identity that exactly matches the corpus or onboard a separate corpus.",
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
