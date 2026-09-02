"""Atomic host orchestration for PCellizer split-table GDS batches."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from .errors import AnalysisError
from .klayout_adapter import run_klayout_worker
from .pcellizer_batch import validate_pcellizer_split_batch_plan
from .pcellizer_recipe import validate_pcellizer_single_shape_recipe
from .pcellizer_snapshot import inspect_pcellizer_snapshot_package
from .workflow_manifest import canonical_json_bytes, canonical_sha256, immutable_json_copy


BATCH_MANIFEST_SCHEMA_VERSION = 1


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action="Restore the exact batch package or regenerate it from the verified snapshot and split plan.",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(
            "PCELLIZER_BATCH_ARTIFACT_READ_FAILED",
            "A batch artifact could not be read.",
            path=str(path),
            error_type=type(exc).__name__,
        )
    return digest.hexdigest()


def inspect_pcellizer_batch_package(*, batch_dir: str) -> dict[str, Any]:
    """Freshly verify manifest, recipe, plan, and every generated GDS hash."""

    root = Path(batch_dir).expanduser().resolve()
    manifest_path = root / "batch_manifest.json"
    recipe_path = root / "recipe.json"
    plan_path = root / "batch_plan.json"
    if not all(path.is_file() for path in (manifest_path, recipe_path, plan_path)):
        _fail("INCOMPLETE_PCELLIZER_BATCH_PACKAGE", "Batch package is missing manifest, recipe, or plan.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        _fail(
            "INVALID_PCELLIZER_BATCH_JSON",
            "Batch package JSON could not be decoded.",
            error_type=type(exc).__name__,
        )
    if not isinstance(manifest, dict):
        _fail("INVALID_PCELLIZER_BATCH_MANIFEST", "Batch manifest must be an object.")
    recorded_hash = manifest.pop("pcellizer_batch_manifest_sha256", None)
    expected_hash = canonical_sha256(manifest)
    if recorded_hash != expected_hash:
        _fail(
            "PCELLIZER_BATCH_MANIFEST_HASH_MISMATCH",
            "Batch manifest content changed after generation.",
            expected_sha256=expected_hash,
            actual_sha256=recorded_hash,
        )
    validated_recipe = validate_pcellizer_single_shape_recipe(recipe)
    validated_plan = validate_pcellizer_split_batch_plan(plan)
    if (
        manifest.get("schema_version") != BATCH_MANIFEST_SCHEMA_VERSION
        or manifest.get("kind") != "PCellizerBatchManifest"
        or manifest.get("recipe_sha256") != validated_recipe["pcellizer_recipe_sha256"]
        or manifest.get("batch_plan_sha256") != validated_plan["pcellizer_batch_plan_sha256"]
        or manifest.get("snapshot_package_sha256")
        != validated_recipe["snapshot_package_sha256"]
        or manifest.get("snapshot_package_sha256")
        != validated_plan["snapshot_package_sha256"]
        or manifest.get("table_raw_sha256")
        != validated_plan["table_source"]["raw_sha256"]
        or manifest.get("flattening_performed") is not False
        or manifest.get("production_ready") is not False
        or not isinstance(manifest.get("outputs"), list)
    ):
        _fail("INVALID_PCELLIZER_BATCH_MANIFEST", "Batch manifest violates the non-flattening schema.")
    expected_filenames = [row["output_filename"] for row in validated_plan["rows"]]
    outputs = manifest["outputs"]
    if [item.get("output_filename") for item in outputs] != expected_filenames:
        _fail("PCELLIZER_BATCH_OUTPUT_SET_MISMATCH", "Manifest output order differs from the split plan.")
    verified_outputs = []
    for item in outputs:
        filename = item.get("output_filename")
        if not isinstance(filename, str):
            _fail("INVALID_PCELLIZER_BATCH_OUTPUT", "Output filename is missing.")
        path = (root / filename).resolve()
        if path.parent != root or not path.is_file():
            _fail("PCELLIZER_BATCH_OUTPUT_MISSING", "A generated GDS is missing or escaped the package.", filename=filename)
        actual_hash = _file_sha256(path)
        actual_size = path.stat().st_size
        if actual_hash != item.get("layout_sha256") or actual_size != item.get("size_bytes"):
            _fail(
                "PCELLIZER_BATCH_OUTPUT_HASH_MISMATCH",
                "Generated GDS content differs from its manifest.",
                filename=filename,
                expected_sha256=item.get("layout_sha256"),
                actual_sha256=actual_hash,
            )
        verified_outputs.append({**item, "output_path": str(path)})
    manifest["pcellizer_batch_manifest_sha256"] = recorded_hash
    return {
        "ok": True,
        "batch_dir": str(root),
        "manifest": manifest,
        "outputs": verified_outputs,
        "fresh_file_hashes_verified": True,
        "production_ready": False,
    }


def generate_pcellizer_split_batch_service(
    *,
    package_dir: str,
    recipe: Mapping[str, Any],
    batch_plan: Mapping[str, Any],
    output_root: str,
    klayout_executable: str | None = None,
    timeout_seconds: float = 180.0,
    worker_runner=run_klayout_worker,
) -> dict[str, Any]:
    """Generate a complete all-or-nothing batch under a content-addressed directory."""

    snapshot = inspect_pcellizer_snapshot_package(package_dir=package_dir)
    validated_recipe = validate_pcellizer_single_shape_recipe(recipe)
    validated_plan = validate_pcellizer_split_batch_plan(batch_plan)
    snapshot_hash = snapshot["manifest"]["snapshot_package_sha256"]
    recipe_hash = validated_recipe["pcellizer_recipe_sha256"]
    if (
        validated_recipe["snapshot_package_sha256"] != snapshot_hash
        or validated_plan["snapshot_package_sha256"] != snapshot_hash
        or validated_plan["recipe_sha256"] != recipe_hash
    ):
        _fail(
            "PCELLIZER_BATCH_BINDING_MISMATCH",
            "Snapshot, recipe, and split plan do not belong to one immutable authoring chain.",
        )
    root = Path(output_root).expanduser().resolve()
    batches_root = root / "pcellizer-batches"
    staging_root = root / ".pcellizer-staging"
    try:
        batches_root.mkdir(parents=True, exist_ok=True)
        staging_root.mkdir(parents=True, exist_ok=True)
        final_dir = batches_root / validated_plan["pcellizer_batch_plan_sha256"]
        if final_dir.is_dir():
            existing = inspect_pcellizer_batch_package(batch_dir=str(final_dir))
            if (
                existing["manifest"]["snapshot_package_sha256"] != snapshot_hash
                or existing["manifest"]["recipe_sha256"] != recipe_hash
            ):
                _fail(
                    "PCELLIZER_BATCH_EXISTING_BINDING_MISMATCH",
                    "Existing plan-addressed batch belongs to a different source chain.",
                )
            return existing
        staging = Path(tempfile.mkdtemp(prefix="batch-", dir=staging_root))
    except OSError as exc:
        _fail(
            "PCELLIZER_BATCH_OUTPUT_ROOT_FAILED",
            "Batch output root could not be prepared.",
            output_root=str(root),
            error_type=type(exc).__name__,
        )
    try:
        embedded = snapshot["manifest"]["embedded_source"]
        source_path = Path(snapshot["package_dir"]) / embedded["filename"]
        result = worker_runner(
            {
                "operation": "generate_pcellizer_batch",
                "layout_path": str(source_path),
                "recipe": validated_recipe,
                "rows": validated_plan["rows"],
                "output_dir": str(staging),
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            if isinstance(result, Mapping) and isinstance(result.get("code"), str):
                raise AnalysisError(
                    code=result["code"],
                    message=str(result.get("message", "PCellizer batch worker failed.")),
                    details=dict(result.get("details", {})),
                    next_action=result.get("next_action"),
                )
            _fail("INVALID_PCELLIZER_BATCH_WORKER_RESULT", "KLayout batch worker returned an invalid result.")
        worker_outputs = immutable_json_copy(result.get("outputs"))
        expected_names = [row["output_filename"] for row in validated_plan["rows"]]
        if not isinstance(worker_outputs, list) or [item.get("output_filename") for item in worker_outputs] != expected_names:
            _fail("PCELLIZER_BATCH_WORKER_OUTPUT_MISMATCH", "Worker outputs differ from the approved split rows.")
        output_records = []
        for item in worker_outputs:
            path = (staging / item["output_filename"]).resolve()
            if path.parent != staging or not path.is_file():
                _fail("PCELLIZER_BATCH_WORKER_OUTPUT_MISSING", "Worker did not create an expected GDS.")
            actual_hash = _file_sha256(path)
            actual_size = path.stat().st_size
            if actual_hash != item.get("layout_sha256") or actual_size != item.get("size_bytes"):
                _fail("PCELLIZER_BATCH_WORKER_HASH_MISMATCH", "Worker output hash/size evidence is stale.")
            output_records.append(immutable_json_copy(item))
        manifest_core = {
            "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
            "kind": "PCellizerBatchManifest",
            "snapshot_package_sha256": snapshot_hash,
            "recipe_sha256": recipe_hash,
            "batch_plan_sha256": validated_plan["pcellizer_batch_plan_sha256"],
            "table_raw_sha256": validated_plan["table_source"]["raw_sha256"],
            "outputs": output_records,
            "summary": immutable_json_copy(result["summary"]),
            "klayout_version": result.get("klayout_version"),
            "transaction_policy": "all_or_nothing",
            "flattening_performed": False,
            "production_ready": False,
        }
        manifest_hash = canonical_sha256(manifest_core)
        manifest = {**manifest_core, "pcellizer_batch_manifest_sha256": manifest_hash}
        (staging / "recipe.json").write_bytes(canonical_json_bytes(validated_recipe))
        (staging / "batch_plan.json").write_bytes(canonical_json_bytes(validated_plan))
        (staging / "batch_manifest.json").write_bytes(canonical_json_bytes(manifest))
        try:
            os.replace(staging, final_dir)
        except OSError:
            if not final_dir.is_dir():
                raise
            shutil.rmtree(staging)
        return inspect_pcellizer_batch_package(batch_dir=str(final_dir))
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
