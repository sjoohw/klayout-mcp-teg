"""Self-contained, content-addressed geometry snapshots for PCellizer."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .errors import AnalysisError
from .pcellizer_contract import (
    normalize_source_layout_identity,
    validate_selection_binding,
)
from .workflow_manifest import (
    SHA256_PATTERN,
    canonical_json_bytes,
    canonical_sha256,
    immutable_json_copy,
)


SNAPSHOT_SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SOURCE_SUFFIXES = {".gds", ".oas"}


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action="Restore the exact snapshot package or create a new revision from a saved source.",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(
            "PCELLIZER_SNAPSHOT_READ_FAILED",
            "A snapshot artifact could not be read.",
            path=str(path),
            error_type=type(exc).__name__,
        )
    return digest.hexdigest()


def _stable_source_bytes(path: Path) -> bytes:
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        _fail(
            "PCELLIZER_SNAPSHOT_SOURCE_READ_FAILED",
            "The source layout could not be captured into the snapshot.",
            path=str(path),
            error_type=type(exc).__name__,
        )
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(data) != before.st_size
    ):
        _fail(
            "PCELLIZER_SOURCE_CHANGED_DURING_SNAPSHOT",
            "The source layout changed while its snapshot was being created.",
            path=str(path),
        )
    return data


def _validate_capture(capture: Mapping[str, Any]) -> dict[str, Any]:
    document = immutable_json_copy(capture)
    if document.get("kind") != "PCellizerParameterCapture":
        _fail(
            "INVALID_PCELLIZER_CAPTURE",
            "Snapshot creation requires a PCellizerParameterCapture.",
            kind=document.get("kind"),
        )
    if (
        document.get("scope") != "current_occurrence"
        or document.get("flattening_performed") is not False
        or document.get("source_layout_modified") is not False
        or document.get("production_ready") is not False
    ):
        _fail(
            "INVALID_PCELLIZER_CAPTURE_STATE",
            "Capture must be an unmodified, non-flattened current-occurrence draft.",
            scope=document.get("scope"),
            flattening_performed=document.get("flattening_performed"),
            source_layout_modified=document.get("source_layout_modified"),
            production_ready=document.get("production_ready"),
        )
    recorded_hash = document.pop("parameter_capture_sha256", None)
    expected_hash = canonical_sha256(document)
    if recorded_hash != expected_hash:
        _fail(
            "PCELLIZER_CAPTURE_HASH_MISMATCH",
            "The parameter capture changed after it was created.",
            expected_sha256=expected_hash,
            actual_sha256=recorded_hash,
        )
    source = normalize_source_layout_identity(document.get("source"))
    endpoint_manifests = document.get("endpoint_manifests")
    if not isinstance(endpoint_manifests, list) or not endpoint_manifests:
        _fail(
            "PCELLIZER_ENDPOINT_MANIFEST_REQUIRED",
            "A snapshot requires at least one exact endpoint manifest.",
        )
    for index, item in enumerate(endpoint_manifests):
        if not isinstance(item, Mapping) or not isinstance(item.get("manifest"), Mapping):
            _fail(
                "INVALID_PCELLIZER_ENDPOINT_MANIFEST",
                "Each endpoint capture must contain one SelectionManifest.",
                endpoint_index=index,
            )
        manifest = item["manifest"]
        if manifest.get("source") != source:
            _fail(
                "PCELLIZER_ENDPOINT_SOURCE_MISMATCH",
                "Every endpoint manifest must bind the same immutable source.",
                endpoint_index=index,
            )
        validate_selection_binding(
            manifest,
            current_layout_sha256=source["layout_sha256"],
            current_shape_fingerprint_sha256=manifest.get(
                "shape_fingerprint_sha256"
            ),
            current_neighborhood_fingerprint_sha256=manifest.get(
                "neighborhood_fingerprint_sha256"
            ),
        )
    document["parameter_capture_sha256"] = recorded_hash
    return document


def _session_id(value: str | None, *, capture_hash: str) -> str:
    normalized = value or f"pcellizer-{capture_hash[:16]}"
    if not isinstance(normalized, str) or not SESSION_ID_PATTERN.fullmatch(normalized):
        _fail(
            "INVALID_PCELLIZER_SESSION_ID",
            "session_id must be a short filesystem-safe identifier.",
            session_id=normalized,
        )
    return normalized


def _parent_revision(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        _fail(
            "INVALID_PCELLIZER_PARENT_REVISION",
            "parent_revision_sha256 must be a lowercase SHA-256 digest.",
            parent_revision_sha256=value,
        )
    return value


def _verify_package(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    capture_path = package_dir / "capture.json"
    if not manifest_path.is_file() or not capture_path.is_file():
        _fail(
            "INCOMPLETE_PCELLIZER_SNAPSHOT",
            "Snapshot package is missing its manifest or capture document.",
            package_dir=str(package_dir),
        )
    import json

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(
            "INVALID_PCELLIZER_SNAPSHOT_JSON",
            "Snapshot JSON could not be decoded.",
            package_dir=str(package_dir),
            error_type=type(exc).__name__,
        )
    if not isinstance(manifest, dict) or not isinstance(capture, dict):
        _fail(
            "INVALID_PCELLIZER_SNAPSHOT_SCHEMA",
            "Snapshot manifest and capture must both be JSON objects.",
            manifest_type=type(manifest).__name__,
            capture_type=type(capture).__name__,
        )
    required_manifest_keys = {
        "schema_version",
        "kind",
        "session_id",
        "parent_revision_sha256",
        "embedded_source",
        "capture",
        "flattening_performed",
        "source_geometry_modified",
        "standalone_recovery_supported",
        "production_ready",
        "snapshot_package_sha256",
    }
    if set(manifest) != required_manifest_keys:
        _fail(
            "INVALID_PCELLIZER_SNAPSHOT_SCHEMA",
            "Snapshot manifest keys do not match schema v1.",
            missing_keys=sorted(required_manifest_keys.difference(manifest)),
            unexpected_keys=sorted(set(manifest).difference(required_manifest_keys)),
        )
    embedded = manifest.get("embedded_source")
    capture_record = manifest.get("capture")
    if (
        manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or manifest.get("kind") != "PCellizerSnapshotPackage"
        or not isinstance(embedded, dict)
        or not isinstance(capture_record, dict)
        or embedded.get("filename") not in {"source.gds", "source.oas"}
        or capture_record.get("filename") != "capture.json"
        or manifest.get("flattening_performed") is not False
        or manifest.get("source_geometry_modified") is not False
        or manifest.get("standalone_recovery_supported") is not True
        or manifest.get("production_ready") is not False
    ):
        _fail(
            "INVALID_PCELLIZER_SNAPSHOT_SCHEMA",
            "Snapshot manifest violates the standalone non-flattening schema.",
        )
    for field, value in (
        ("embedded_source.sha256", embedded.get("sha256")),
        ("capture.file_sha256", capture_record.get("file_sha256")),
        (
            "capture.parameter_capture_sha256",
            capture_record.get("parameter_capture_sha256"),
        ),
    ):
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            _fail(
                "INVALID_PCELLIZER_SNAPSHOT_SCHEMA",
                f"{field} must be a lowercase SHA-256 digest.",
                field=field,
            )
    recorded_package_hash = manifest.pop("snapshot_package_sha256", None)
    expected_package_hash = canonical_sha256(manifest)
    if recorded_package_hash != expected_package_hash:
        _fail(
            "PCELLIZER_SNAPSHOT_MANIFEST_HASH_MISMATCH",
            "Snapshot manifest content has changed.",
            expected_sha256=expected_package_hash,
            actual_sha256=recorded_package_hash,
        )
    source_path = package_dir / embedded["filename"]
    checks = {
        "embedded_source_sha256": _file_sha256(source_path),
        "capture_sha256": _file_sha256(capture_path),
    }
    expected = {
        "embedded_source_sha256": embedded["sha256"],
        "capture_sha256": capture_record["file_sha256"],
    }
    if checks != expected:
        _fail(
            "PCELLIZER_SNAPSHOT_ARTIFACT_HASH_MISMATCH",
            "Snapshot package artifacts no longer match the manifest.",
            expected=expected,
            actual=checks,
        )
    normalized_capture = _validate_capture(capture)
    if (
        normalized_capture["parameter_capture_sha256"]
        != capture_record["parameter_capture_sha256"]
    ):
        _fail(
            "PCELLIZER_SNAPSHOT_CAPTURE_MISMATCH",
            "Snapshot capture identity differs from the package manifest.",
        )
    manifest["snapshot_package_sha256"] = recorded_package_hash
    return {
        "ok": True,
        "manifest": manifest,
        "capture": normalized_capture,
        "package_dir": str(package_dir.resolve()),
        "production_ready": False,
    }


def inspect_pcellizer_snapshot_package(*, package_dir: str) -> dict[str, Any]:
    """Verify and return one immutable snapshot handoff package.

    This is the public, read-only boundary between the KLayout dock and later
    recipe compilation.  Callers never need the original layout path to remain
    available because verification is performed against the embedded source.
    """

    package = _verify_package(Path(package_dir).expanduser().resolve())
    return immutable_json_copy(package)


def create_pcellizer_snapshot_package(
    *,
    capture: Mapping[str, Any],
    package_root: str,
    session_id: str | None = None,
    parent_revision_sha256: str | None = None,
) -> dict[str, Any]:
    """Atomically embed exact source bytes and capture JSON in one package."""

    normalized_capture = _validate_capture(capture)
    source = normalized_capture["source"]
    source_path = Path(source["layout_path"]).resolve()
    suffix = source_path.suffix.lower()
    if suffix not in SOURCE_SUFFIXES:
        _fail(
            "UNSUPPORTED_PCELLIZER_SNAPSHOT_FORMAT",
            "Self-contained snapshots currently support GDS and OASIS sources.",
            suffix=suffix,
        )
    source_bytes = _stable_source_bytes(source_path)
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != source["layout_sha256"]:
        _fail(
            "STALE_PCELLIZER_SOURCE",
            "Source bytes changed after GUI capture.",
            expected_layout_sha256=source["layout_sha256"],
            current_layout_sha256=source_hash,
        )
    capture_bytes = canonical_json_bytes(normalized_capture)
    capture_file_hash = hashlib.sha256(capture_bytes).hexdigest()
    capture_hash = normalized_capture["parameter_capture_sha256"]
    normalized_session = _session_id(session_id, capture_hash=capture_hash)
    parent = _parent_revision(parent_revision_sha256)
    embedded_filename = f"source{suffix}"
    manifest_core = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "PCellizerSnapshotPackage",
        "session_id": normalized_session,
        "parent_revision_sha256": parent,
        "embedded_source": {
            "filename": embedded_filename,
            "format": suffix[1:],
            "sha256": source_hash,
            "size_bytes": len(source_bytes),
            "top_cell": source["top_cell"],
            "dbu_um": source["dbu_um"],
            "external_runtime_dependency": False,
        },
        "capture": {
            "filename": "capture.json",
            "file_sha256": capture_file_hash,
            "parameter_capture_sha256": capture_hash,
        },
        "flattening_performed": False,
        "source_geometry_modified": False,
        "standalone_recovery_supported": True,
        "production_ready": False,
    }
    package_hash = canonical_sha256(manifest_core)
    manifest = {**manifest_core, "snapshot_package_sha256": package_hash}
    root = Path(package_root).expanduser().resolve()
    packages_root = root / "packages"
    packages_root.mkdir(parents=True, exist_ok=True)
    if parent is not None:
        parent_dir = packages_root / parent
        if not parent_dir.is_dir():
            _fail(
                "PCELLIZER_PARENT_REVISION_NOT_FOUND",
                "The requested parent snapshot revision is not present in this store.",
                parent_revision_sha256=parent,
            )
        parent_package = _verify_package(parent_dir)
        if parent_package["manifest"]["session_id"] != normalized_session:
            _fail(
                "PCELLIZER_PARENT_SESSION_MISMATCH",
                "A snapshot revision cannot reference a parent from another session.",
                session_id=normalized_session,
                parent_session_id=parent_package["manifest"]["session_id"],
            )
    final_dir = packages_root / package_hash
    if final_dir.exists():
        return _verify_package(final_dir)
    staging_root = root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix="pcellizer-", dir=staging_root))
    try:
        (staging_dir / embedded_filename).write_bytes(source_bytes)
        (staging_dir / "capture.json").write_bytes(capture_bytes)
        (staging_dir / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        try:
            os.replace(staging_dir, final_dir)
        except OSError:
            # Directory collision errors vary by platform (WinError 5,
            # EEXIST, ENOTEMPTY).  Treat them as idempotent only when the
            # complete content-addressed destination now exists; verification
            # below still rejects a partial or hostile package.
            if not final_dir.is_dir():
                raise
            shutil.rmtree(staging_dir)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise
    return _verify_package(final_dir)


def recover_pcellizer_snapshot_source(
    *, package_dir: str, output_path: str
) -> dict[str, Any]:
    """Recover embedded layout bytes without consulting the original source path."""

    package = _verify_package(Path(package_dir).expanduser().resolve())
    manifest = package["manifest"]
    source = manifest["embedded_source"]
    embedded_path = Path(package["package_dir"]) / source["filename"]
    destination = Path(output_path).expanduser().resolve()
    expected_suffix = Path(source["filename"]).suffix.lower()
    if destination.suffix.lower() != expected_suffix:
        _fail(
            "PCELLIZER_RECOVERY_FORMAT_MISMATCH",
            "Recovery target suffix must match the embedded layout format.",
            expected_suffix=expected_suffix,
            actual_suffix=destination.suffix.lower(),
        )
    if destination.is_dir():
        _fail(
            "PCELLIZER_RECOVERY_TARGET_IS_DIRECTORY",
            "Recovery output_path must be a file, not a directory.",
            output_path=str(destination),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = embedded_path.read_bytes()
    except OSError as exc:
        _fail(
            "PCELLIZER_RECOVERY_READ_FAILED",
            "Embedded source could not be read for recovery.",
            error_type=type(exc).__name__,
        )
    if destination.exists():
        try:
            existing = destination.read_bytes()
        except OSError as exc:
            _fail(
                "PCELLIZER_RECOVERY_TARGET_READ_FAILED",
                "Existing recovery target could not be read safely.",
                output_path=str(destination),
                error_type=type(exc).__name__,
            )
        if hashlib.sha256(existing).hexdigest() != source["sha256"]:
            _fail(
                "PCELLIZER_RECOVERY_TARGET_EXISTS",
                "Recovery target exists with different content.",
                output_path=str(destination),
            )
    else:
        try:
            destination.write_bytes(data)
        except OSError as exc:
            _fail(
                "PCELLIZER_RECOVERY_WRITE_FAILED",
                "Embedded source could not be recovered.",
                output_path=str(destination),
                error_type=type(exc).__name__,
            )
    return {
        "ok": True,
        "output_path": str(destination),
        "layout_sha256": source["sha256"],
        "source_runtime_dependency_used": False,
        "flattening_performed": False,
        "production_ready": False,
    }
