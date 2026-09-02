"""Host-side immutable pad macro intake and content-addressed packaging."""

from __future__ import annotations

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
from .workflow_manifest import canonical_json_bytes, canonical_sha256


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "pad_macro_intake", **dict(details)},
        next_action=next_action,
    )


def _instances(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        _fail(
            "PAD_MACRO_INSTANCES_REQUIRED",
            "At least one pad_id and instance transform is required.",
            details={"field": "instances", "received_type": type(value).__name__},
            next_action="Provide each pad ID with x_um, y_um, rotation_deg, and mirror_x.",
        )
    normalized = []
    seen = set()
    for index, item in enumerate(value):
        field = f"instances[{index}]"
        if not isinstance(item, Mapping):
            _fail(
                "PAD_MACRO_INSTANCE_INVALID",
                "Every pad instance must be an object.",
                details={"field": field, "received_type": type(item).__name__},
                next_action="Provide a pad_id and explicit transform object for this item.",
            )
        required = {"pad_id", "x_um", "y_um", "rotation_deg", "mirror_x"}
        missing = sorted(required.difference(item))
        if missing:
            _fail(
                "PAD_MACRO_INSTANCE_FIELD_MISSING",
                "A pad instance transform is incomplete.",
                details={"field": field, "missing": missing},
                next_action="Add every missing transform field; transforms are never inferred.",
            )
        pad_id = item["pad_id"]
        if not isinstance(pad_id, str) or not pad_id.strip() or pad_id in seen:
            _fail(
                "PAD_MACRO_PAD_ID_INVALID",
                "pad_id must be a unique non-empty string.",
                details={"field": f"{field}.pad_id", "value": pad_id},
                next_action="Assign one stable unique ID to every pad occurrence.",
            )
        numbers = (item["x_um"], item["y_um"], item["rotation_deg"])
        if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)) for number in numbers):
            _fail(
                "PAD_MACRO_INSTANCE_TRANSFORM_INVALID",
                "Pad transform coordinates and rotation must be finite numbers.",
                details={"field": field, "received": dict(item)},
                next_action="Correct the explicit pad occurrence transform values.",
            )
        rotation = float(item["rotation_deg"])
        if rotation not in {0.0, 90.0, 180.0, 270.0} or not isinstance(item["mirror_x"], bool):
            _fail(
                "PAD_MACRO_INSTANCE_TRANSFORM_UNSUPPORTED",
                "Pad transforms support orthogonal rotations and an explicit mirror_x boolean.",
                details={"field": field, "received": dict(item), "allowed": [0, 90, 180, 270]},
                next_action="Use the exact orthogonal transform represented in the source padset.",
            )
        seen.add(pad_id)
        normalized.append(
            {
                "pad_id": pad_id.strip(),
                "x_um": float(item["x_um"]),
                "y_um": float(item["y_um"]),
                "rotation_deg": rotation,
                "mirror_x": item["mirror_x"],
            }
        )
    return normalized


def create_pad_macro_artifact(
    *,
    source_layout_path: str,
    top_cell: str | None,
    access_layer: Mapping[str, Any],
    instances: list[Mapping[str, Any]],
    package_root: str | Path,
    expected_width_um: float = 40.0,
    expected_height_um: float = 40.0,
    expected_dbu_um: float | None = None,
    tolerance_um: float = 0.001,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
    worker_runner=run_klayout_worker,
) -> dict[str, Any]:
    """Inspect and preserve a source pad cell without synthesizing pad geometry."""

    normalized_instances = _instances(instances)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
        for value in (expected_width_um, expected_height_um, tolerance_um, timeout_seconds)
    ):
        _fail(
            "PAD_MACRO_EXPECTATION_INVALID",
            "Expected size, tolerance, and timeout must be finite positive numbers.",
            details={"field": "pad_macro_expectation"},
            next_action="Provide positive micron dimensions and timeout values.",
        )
    root = Path(package_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with create_layout_snapshot(source_layout_path, purpose="layout") as snapshot:
        result = worker_runner(
            {
                "operation": "inspect_pad_macro",
                "layout_path": str(snapshot.path),
                "top_cell": top_cell,
                "access_layer": dict(access_layer),
                "edge_tolerance_um": float(tolerance_um),
            },
            executable_path=klayout_executable,
            timeout_seconds=float(timeout_seconds),
        )
        if not result.get("ok"):
            return result
        for dimension, expected in (("width_um", expected_width_um), ("height_um", expected_height_um)):
            actual = result.get(dimension)
            if not isinstance(actual, (int, float)) or abs(float(actual) - float(expected)) > float(tolerance_um):
                _fail(
                    "PAD_MACRO_SIZE_MISMATCH",
                    "The recursive pad cell bbox does not match the asserted macro size.",
                    details={
                        "field": dimension,
                        "received": actual,
                        "expected": float(expected),
                        "unit": "um",
                        "top_cell": result.get("top_cell"),
                    },
                    next_action="Select the correct pad cell or correct the asserted common size.",
                )
        if expected_dbu_um is not None and abs(float(result["dbu_um"]) - float(expected_dbu_um)) > 1e-15:
            _fail(
                "PAD_MACRO_DBU_MISMATCH",
                "The pad stream DBU differs from the asserted common layout DBU.",
                details={"field": "expected_dbu_um", "expected": expected_dbu_um, "received": result["dbu_um"], "unit": "um"},
                next_action="Use a stream with the common DBU; implicit rescaling is forbidden.",
            )
        if not result.get("eligible_edge_landings"):
            _fail(
                "PAD_MACRO_EDGE_ACCESS_NOT_FOUND",
                "No access-metal shape reaches a pad macro bbox edge.",
                details={"field": "access_layer", "received": dict(access_layer), "top_cell": result.get("top_cell")},
                next_action="Correct the access layer or explicitly identify a valid edge landing.",
            )
        artifact = {
            "schema_version": 1,
            "artifact_type": "PadMacroArtifact",
            "source_file_sha256": snapshot.sha256,
            "source_size_bytes": snapshot.size_bytes,
            "source_cell": result["top_cell"],
            "recursive_source_cell_fingerprint_sha256": result["recursive_geometry_fingerprint_sha256"],
            "dbu_um": result["dbu_um"],
            "local_bbox_um": result["bbox_um"],
            "asserted_size_um": [float(expected_width_um), float(expected_height_um)],
            "access_layer": result["access_layer"],
            "eligible_edge_landings": result["eligible_edge_landings"],
            "instances": normalized_instances,
            "extra_keepout": "none",
            "pad_geometry_generation_allowed": False,
            "source_hierarchy_must_be_preserved": True,
        }
        artifact_sha256 = canonical_sha256(artifact)
        final = root / artifact_sha256
        staging = Path(
            tempfile.mkdtemp(
                prefix=publication_staging_prefix("pad-macro", directory=True),
                dir=root,
            )
        )
        try:
            suffix = snapshot.source_path.suffix.lower() or ".gds"
            shutil.copyfile(snapshot.path, staging / f"source{suffix}")
            (staging / "artifact.json").write_bytes(canonical_json_bytes(artifact))
            try:
                publish_new_directory(staging, final)
            except OutputAlreadyExistsError:
                existing = final / "artifact.json"
                if not existing.is_file() or existing.read_bytes() != canonical_json_bytes(artifact):
                    _fail(
                        "PAD_MACRO_PACKAGE_COLLISION",
                        "Existing pad macro package differs at the same content address.",
                        details={"path": str(final), "expected": artifact_sha256},
                        next_action="Quarantine and restore the immutable pad macro registry.",
                    )
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {
        "ok": True,
        "artifact": artifact,
        "artifact_sha256": artifact_sha256,
        "package_path": str(final),
        "source_preserved": True,
        "geometry_modified": False,
        "production_ready": False,
        "next_gate": "Bind this artifact to an exact technology adapter and routing plan.",
    }


def compose_pad_macro_overlay(
    *,
    package_path: str | Path,
    output_path: str,
    operations: list[Mapping[str, Any]],
    output_top_cell: str = "TEG_PAD_MACRO_OVERLAY",
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
    worker_runner=run_klayout_worker,
) -> dict[str, Any]:
    """Compose a new top cell while structurally forbidding pad geometry edits."""

    package = Path(package_path).expanduser().resolve()
    artifact_path = package / "artifact.json"
    sources = sorted(package.glob("source.*"))
    if not artifact_path.is_file() or len(sources) != 1:
        _fail(
            "PAD_MACRO_PACKAGE_INVALID",
            "Pad macro package must contain one source stream and artifact.json.",
            details={"field": "package_path", "value": str(package)},
            next_action="Restore the exact content-addressed pad macro package.",
        )
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "PAD_MACRO_PACKAGE_INVALID",
            "Pad macro artifact.json is unreadable.",
            details={"field": "package_path", "value": str(package), "error_type": type(exc).__name__},
            next_action="Restore the exact content-addressed pad macro package.",
        )
    digest = hashlib.sha256(sources[0].read_bytes()).hexdigest()
    if digest != artifact.get("source_file_sha256"):
        _fail(
            "PAD_MACRO_PACKAGE_SOURCE_HASH_MISMATCH",
            "Preserved pad source stream differs from the artifact hash.",
            details={"expected": artifact.get("source_file_sha256"), "received": digest},
            next_action="Restore the exact content-addressed pad macro package.",
        )
    if not isinstance(operations, list):
        _fail(
            "PAD_MACRO_COMPOSE_OPERATIONS_INVALID",
            "operations must be an array of separate DUT/routing boxes.",
            details={"field": "operations", "received_type": type(operations).__name__},
            next_action="Provide explicit DUT/routing add_box operations.",
        )
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping) or operation.get("category") not in {"dut", "routing"}:
            _fail(
                "PAD_MACRO_COMPOSE_OPERATION_FORBIDDEN",
                "Pad modification operations are forbidden during overlay composition.",
                details={"field": f"operations[{index}]", "received": operation},
                next_action="Remove pad edits; only DUT and routing geometry may be added.",
            )
    return worker_runner(
        {
            "operation": "compose_pad_macro_overlay",
            "source_layout_path": str(sources[0]),
            "source_cell": artifact["source_cell"],
            "recursive_source_cell_fingerprint_sha256": artifact[
                "recursive_source_cell_fingerprint_sha256"
            ],
            "instances": artifact["instances"],
            "operations": [dict(operation) for operation in operations],
            "output_path": output_path,
            "output_top_cell": output_top_cell,
        },
        executable_path=klayout_executable,
        timeout_seconds=timeout_seconds,
    )
