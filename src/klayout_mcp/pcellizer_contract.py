"""Fail-closed identity contracts for non-destructive PCellizer authoring.

This module deliberately contains no ``pya`` dependency.  KLayout workers may
produce these records, while the host validates and content-addresses them.
Coordinates are integer database units so selection identity is independent of
floating-point tolerances.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import AnalysisError
from .workflow_manifest import SHA256_PATTERN, canonical_sha256, immutable_json_copy


PCELLIZER_SCHEMA_VERSION = 1
SELECTION_SCOPES = (
    "current_occurrence",
    "approved_occurrence_group",
    "composite_dut",
    "cell_definition",
)


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details=details,
        next_action="Refresh the source inventory and explicitly reselect the exact geometry.",
    )


def _object(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("INVALID_PCELLIZER_CONTRACT", f"{field} must be an object.", field=field)
    return value


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(
            "INVALID_PCELLIZER_CONTRACT",
            f"{field} must be a non-empty string.",
            field=field,
            value=value,
        )
    return value.strip()


def _integer(value: Any, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            "INVALID_PCELLIZER_CONTRACT",
            f"{field} must be an integer database-unit value.",
            field=field,
            value=value,
        )
    if minimum is not None and value < minimum:
        _fail(
            "INVALID_PCELLIZER_CONTRACT",
            f"{field} must be at least {minimum}.",
            field=field,
            value=value,
        )
    return value


def _integer_pair(value: Any, *, field: str) -> list[int]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) != 2
    ):
        _fail(
            "INVALID_PCELLIZER_CONTRACT",
            f"{field} must contain exactly two integer DBU coordinates.",
            field=field,
            value=value,
        )
    return [
        _integer(value[0], field=f"{field}[0]"),
        _integer(value[1], field=f"{field}[1]"),
    ]


def _sha256(value: Any, *, field: str) -> str:
    normalized = _string(value, field=field)
    if not SHA256_PATTERN.fullmatch(normalized):
        _fail(
            "INVALID_PCELLIZER_HASH",
            f"{field} must be a lowercase SHA-256 digest.",
            field=field,
            value=value,
        )
    return normalized


def normalize_source_layout_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the immutable source identity embedded in authoring records."""

    source = _object(value, field="source")
    if source.get("schema_version") != PCELLIZER_SCHEMA_VERSION:
        _fail(
            "UNSUPPORTED_PCELLIZER_SCHEMA_VERSION",
            "SourceLayoutIdentity schema version is not supported.",
            schema_version=source.get("schema_version"),
            supported_schema_versions=[PCELLIZER_SCHEMA_VERSION],
        )
    if source.get("kind") != "SourceLayoutIdentity":
        _fail(
            "INVALID_PCELLIZER_SOURCE_IDENTITY",
            "Selection source must be a SourceLayoutIdentity.",
            kind=source.get("kind"),
        )
    if source.get("source_mutable") is not False:
        _fail(
            "MUTABLE_PCELLIZER_SOURCE_FORBIDDEN",
            "PCellizer authoring requires an immutable source snapshot identity.",
            source_mutable=source.get("source_mutable"),
        )
    dbu_um = source.get("dbu_um")
    if (
        isinstance(dbu_um, bool)
        or not isinstance(dbu_um, (int, float))
        or not math.isfinite(float(dbu_um))
        or float(dbu_um) <= 0
    ):
        _fail(
            "INVALID_PCELLIZER_SOURCE_IDENTITY",
            "Source DBU must be finite and positive.",
            dbu_um=dbu_um,
        )
    return {
        "schema_version": PCELLIZER_SCHEMA_VERSION,
        "kind": "SourceLayoutIdentity",
        "layout_path": _string(source.get("layout_path"), field="layout_path"),
        "layout_sha256": _sha256(
            source.get("layout_sha256"), field="source.layout_sha256"
        ),
        "size_bytes": _integer(
            source.get("size_bytes"), field="source.size_bytes", minimum=0
        ),
        "top_cell": _string(source.get("top_cell"), field="source.top_cell"),
        "dbu_um": float(dbu_um),
        "source_mutable": False,
    }


def build_source_layout_identity(
    layout_path: str,
    *,
    top_cell: str,
    dbu_um: float,
) -> dict[str, Any]:
    """Capture immutable source-file identity without modifying the layout."""

    path = Path(layout_path).expanduser().resolve()
    if not path.is_file():
        _fail(
            "PCELLIZER_SOURCE_NOT_FOUND",
            "PCellizer source layout does not exist.",
            layout_path=str(path),
        )
    if (
        isinstance(dbu_um, bool)
        or not isinstance(dbu_um, (int, float))
        or not math.isfinite(float(dbu_um))
        or float(dbu_um) <= 0
    ):
        _fail(
            "INVALID_PCELLIZER_CONTRACT",
            "dbu_um must be a finite positive number.",
            dbu_um=dbu_um,
        )
    before = path.stat()
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or size_bytes != before.st_size
    ):
        _fail(
            "PCELLIZER_SOURCE_CHANGED_DURING_CAPTURE",
            "PCellizer source changed while its identity was captured.",
            layout_path=str(path),
            size_before=before.st_size,
            size_after=after.st_size,
            mtime_ns_before=before.st_mtime_ns,
            mtime_ns_after=after.st_mtime_ns,
        )
    return normalize_source_layout_identity({
        "schema_version": PCELLIZER_SCHEMA_VERSION,
        "kind": "SourceLayoutIdentity",
        "layout_path": str(path),
        "layout_sha256": digest.hexdigest(),
        "size_bytes": size_bytes,
        "top_cell": _string(top_cell, field="top_cell"),
        "dbu_um": float(dbu_um),
        "source_mutable": False,
    })


def normalize_transform_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one local instance transform and expose its authoring gate."""

    transform = _object(value, field="transform")
    displacement = _integer_pair(
        transform.get("displacement_dbu"), field="transform.displacement_dbu"
    )
    angle = transform.get("angle_degrees")
    magnification = transform.get("magnification")
    mirror = transform.get("mirror")
    if (
        isinstance(angle, bool)
        or not isinstance(angle, (int, float))
        or not math.isfinite(float(angle))
    ):
        _fail(
            "INVALID_PCELLIZER_TRANSFORM",
            "Transform angle must be finite.",
            angle_degrees=angle,
        )
    if (
        isinstance(magnification, bool)
        or not isinstance(magnification, (int, float))
        or not math.isfinite(float(magnification))
        or float(magnification) <= 0
    ):
        _fail(
            "INVALID_PCELLIZER_TRANSFORM",
            "Transform magnification must be finite and positive.",
            magnification=magnification,
        )
    if not isinstance(mirror, bool):
        _fail(
            "INVALID_PCELLIZER_TRANSFORM",
            "Transform mirror flag must be boolean.",
            mirror=mirror,
        )

    normalized_angle = float(angle) % 360.0
    normalized_magnification = float(magnification)
    unsupported_reasons: list[str] = []
    if not math.isclose(normalized_magnification, 1.0, rel_tol=0.0, abs_tol=1e-12):
        unsupported_reasons.append("non_unit_magnification")
    if not math.isclose(normalized_angle % 90.0, 0.0, rel_tol=0.0, abs_tol=1e-12):
        unsupported_reasons.append("non_orthogonal_angle")

    return {
        "displacement_dbu": displacement,
        "angle_degrees": normalized_angle,
        "mirror": mirror,
        "magnification": normalized_magnification,
        "authoring_supported": not unsupported_reasons,
        "unsupported_reasons": unsupported_reasons,
    }


def normalize_array_member(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize one occurrence's source array and exact member identity."""

    array = {} if value is None else _object(value, field="array")
    columns = _integer(array.get("columns", 1), field="array.columns", minimum=1)
    rows = _integer(array.get("rows", 1), field="array.rows", minimum=1)
    regular = array.get("regular", True)
    if not isinstance(regular, bool):
        _fail(
            "INVALID_PCELLIZER_ARRAY_REPRESENTATION",
            "array.regular must be boolean.",
            regular=regular,
        )
    column = _integer(array.get("column", 0), field="array.column", minimum=0)
    row = _integer(array.get("row", 0), field="array.row", minimum=0)
    if column >= columns or row >= rows:
        _fail(
            "INVALID_PCELLIZER_ARRAY_MEMBER",
            "Array member index lies outside the declared source array.",
            columns=columns,
            rows=rows,
            column=column,
            row=row,
        )
    a_vector = _integer_pair(
        array.get("a_vector_dbu", [0, 0]), field="array.a_vector_dbu"
    )
    b_vector = _integer_pair(
        array.get("b_vector_dbu", [0, 0]), field="array.b_vector_dbu"
    )
    if regular and columns > 1 and a_vector == [0, 0]:
        _fail(
            "INVALID_PCELLIZER_ARRAY_BASIS",
            "A multi-column array requires a nonzero a-vector.",
            a_vector_dbu=a_vector,
        )
    if regular and rows > 1 and b_vector == [0, 0]:
        _fail(
            "INVALID_PCELLIZER_ARRAY_BASIS",
            "A multi-row array requires a nonzero b-vector.",
            b_vector_dbu=b_vector,
        )
    is_array = columns > 1 or rows > 1
    return {
        "columns": columns,
        "rows": rows,
        "column": column,
        "row": row,
        "a_vector_dbu": a_vector,
        "b_vector_dbu": b_vector,
        "regular": regular,
        "is_array": is_array,
        "representation": (
            "regular_array"
            if is_array and regular
            else "iterated_instance"
            if is_array
            else "single"
        ),
    }


def normalize_occurrence_path(
    *, top_cell: str, segments: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Create a content-addressed path to one physical hierarchy occurrence."""

    if isinstance(segments, (str, bytes, bytearray)) or not isinstance(segments, Sequence):
        _fail(
            "INVALID_PCELLIZER_OCCURRENCE_PATH",
            "Occurrence path segments must be an array.",
            segments_type=type(segments).__name__,
        )
    normalized_segments: list[dict[str, Any]] = []
    expected_parent = _string(top_cell, field="top_cell")
    authoring_supported = True
    for index, raw_segment in enumerate(segments):
        segment = _object(raw_segment, field=f"segments[{index}]")
        parent_cell = _string(segment.get("parent_cell"), field="parent_cell")
        child_cell = _string(segment.get("child_cell"), field="child_cell")
        if parent_cell != expected_parent:
            _fail(
                "DISCONTINUOUS_PCELLIZER_OCCURRENCE_PATH",
                "Occurrence path does not form a continuous parent-child chain.",
                segment_index=index,
                expected_parent=expected_parent,
                actual_parent=parent_cell,
            )
        transform = normalize_transform_descriptor(segment.get("transform"))
        array = normalize_array_member(segment.get("array"))
        raw_blockers = segment.get("authoring_blockers", [])
        if (
            isinstance(raw_blockers, (str, bytes, bytearray))
            or not isinstance(raw_blockers, Sequence)
        ):
            _fail(
                "INVALID_PCELLIZER_AUTHORING_BLOCKERS",
                "Occurrence authoring blockers must be an array of tokens.",
                segment_index=index,
            )
        blockers = sorted(
            {_string(item, field="authoring_blockers[]") for item in raw_blockers}
        )
        normalized_segment = {
            "parent_cell": parent_cell,
            "child_cell": child_cell,
            "instance_ordinal": _integer(
                segment.get("instance_ordinal"),
                field="instance_ordinal",
                minimum=0,
            ),
            "transform": transform,
            "array": array,
            "authoring_blockers": blockers,
        }
        normalized_segments.append(normalized_segment)
        expected_parent = child_cell
        authoring_supported = (
            authoring_supported
            and transform["authoring_supported"]
            and not blockers
        )

    identity_payload = {
        "top_cell": _string(top_cell, field="top_cell"),
        "segments": normalized_segments,
    }
    return {
        **identity_payload,
        "leaf_cell": expected_parent,
        "depth": len(normalized_segments),
        "occurrence_id": canonical_sha256(identity_payload),
        "authoring_supported": authoring_supported,
    }


def build_selection_manifest(
    *,
    source: Mapping[str, Any],
    occurrence_path: Mapping[str, Any],
    layer: int,
    datatype: int,
    shape_fingerprint_sha256: str,
    edge_dbu: Sequence[Sequence[int]],
    neighborhood_fingerprint_sha256: str,
    shape_ordinal: int = 0,
    duplicate_geometry_count: int = 1,
    scope: str = "current_occurrence",
    revision: int = 1,
) -> dict[str, Any]:
    """Bind one exact selected edge to source bytes and one hierarchy occurrence."""

    source_copy = normalize_source_layout_identity(
        immutable_json_copy(_object(source, field="source"))
    )
    supplied_path = immutable_json_copy(
        _object(occurrence_path, field="occurrence_path")
    )
    path_copy = normalize_occurrence_path(
        top_cell=supplied_path.get("top_cell"),
        segments=supplied_path.get("segments"),
    )
    if supplied_path.get("occurrence_id") != path_copy["occurrence_id"]:
        _fail(
            "PCELLIZER_OCCURRENCE_ID_MISMATCH",
            "Occurrence path content does not match its identity hash.",
            expected_occurrence_id=path_copy["occurrence_id"],
            actual_occurrence_id=supplied_path.get("occurrence_id"),
        )
    if scope not in SELECTION_SCOPES:
        _fail(
            "INVALID_PCELLIZER_SELECTION_SCOPE",
            "Selection scope is not supported.",
            scope=scope,
            supported_scopes=list(SELECTION_SCOPES),
        )
    if (
        isinstance(edge_dbu, (str, bytes, bytearray))
        or not isinstance(edge_dbu, Sequence)
        or len(edge_dbu) != 2
    ):
        _fail(
            "INVALID_PCELLIZER_EDGE",
            "Selected edge must contain exactly two DBU endpoints.",
            edge_dbu=edge_dbu,
        )
    endpoints = sorted(
        [
            _integer_pair(edge_dbu[0], field="edge_dbu[0]"),
            _integer_pair(edge_dbu[1], field="edge_dbu[1]"),
        ]
    )
    if endpoints[0] == endpoints[1]:
        _fail(
            "DEGENERATE_PCELLIZER_EDGE",
            "Selected edge endpoints must be distinct.",
            edge_dbu=endpoints,
        )
    manifest = {
        "schema_version": PCELLIZER_SCHEMA_VERSION,
        "kind": "SelectionManifest",
        "revision": _integer(revision, field="revision", minimum=1),
        "source": source_copy,
        "occurrence_path": path_copy,
        "layer": _integer(layer, field="layer", minimum=0),
        "datatype": _integer(datatype, field="datatype", minimum=0),
        "shape_fingerprint_sha256": _sha256(
            shape_fingerprint_sha256, field="shape_fingerprint_sha256"
        ),
        "shape_ordinal": _integer(
            shape_ordinal, field="shape_ordinal", minimum=0
        ),
        "duplicate_geometry_count": _integer(
            duplicate_geometry_count,
            field="duplicate_geometry_count",
            minimum=1,
        ),
        "edge_dbu": endpoints,
        "neighborhood_fingerprint_sha256": _sha256(
            neighborhood_fingerprint_sha256,
            field="neighborhood_fingerprint_sha256",
        ),
        "scope": scope,
        "retarget_policy": "exact_only_fail_closed",
        "fuzzy_retarget_allowed": False,
        "authoring_supported": bool(path_copy.get("authoring_supported", False)),
    }
    manifest["selection_manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def validate_selection_binding(
    manifest: Mapping[str, Any],
    *,
    current_layout_sha256: str,
    current_shape_fingerprint_sha256: str,
    current_neighborhood_fingerprint_sha256: str,
) -> dict[str, Any]:
    """Reject stale source or geometry; never repair a selection heuristically."""

    document = immutable_json_copy(_object(manifest, field="manifest"))
    recorded_manifest_hash = document.pop("selection_manifest_sha256", None)
    expected_manifest_hash = canonical_sha256(document)
    if recorded_manifest_hash != expected_manifest_hash:
        _fail(
            "PCELLIZER_MANIFEST_HASH_MISMATCH",
            "Selection manifest content has changed.",
            expected_sha256=expected_manifest_hash,
            actual_sha256=recorded_manifest_hash,
        )
    expected_source = document.get("source", {}).get("layout_sha256")
    current_source = _sha256(current_layout_sha256, field="current_layout_sha256")
    if current_source != expected_source:
        _fail(
            "STALE_PCELLIZER_SOURCE",
            "Source layout bytes changed after selection capture.",
            expected_layout_sha256=expected_source,
            current_layout_sha256=current_source,
        )
    current_shape = _sha256(
        current_shape_fingerprint_sha256,
        field="current_shape_fingerprint_sha256",
    )
    if current_shape != document.get("shape_fingerprint_sha256"):
        _fail(
            "STALE_PCELLIZER_SHAPE",
            "Selected shape no longer matches its exact fingerprint.",
            expected_shape_sha256=document.get("shape_fingerprint_sha256"),
            current_shape_sha256=current_shape,
        )
    current_neighborhood = _sha256(
        current_neighborhood_fingerprint_sha256,
        field="current_neighborhood_fingerprint_sha256",
    )
    if current_neighborhood != document.get("neighborhood_fingerprint_sha256"):
        _fail(
            "STALE_PCELLIZER_NEIGHBORHOOD",
            "Selected edge neighborhood changed after capture.",
            expected_neighborhood_sha256=document.get(
                "neighborhood_fingerprint_sha256"
            ),
            current_neighborhood_sha256=current_neighborhood,
        )
    return {
        "ok": True,
        "selection_manifest_sha256": recorded_manifest_hash,
        "binding_verified": True,
        "fuzzy_retarget_performed": False,
        "authoring_supported": bool(document.get("authoring_supported")),
        "production_ready": False,
    }
