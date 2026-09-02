"""Host-side immutable layout-style extraction and optional profile export."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from .errors import AnalysisError
from .klayout_adapter import create_layout_snapshot, run_klayout_worker
from .layermap import load_layermap
from .workflow_manifest import canonical_sha256


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_output_path(output_profile_path: str) -> Path:
    output = Path(output_profile_path).expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise AnalysisError(
            code="STYLE_PROFILE_FORMAT_REQUIRED",
            message="Extracted style profiles are written as JSON.",
            details={"output_profile_path": str(output)},
            next_action="Use a new output path ending in .json.",
        )
    if not output.parent.is_dir():
        raise AnalysisError(
            code="OUTPUT_DIRECTORY_NOT_FOUND",
            message="Style profile output directory does not exist.",
            details={"output_profile_path": str(output)},
            next_action="Create the output directory and retry.",
        )
    if output.exists():
        raise AnalysisError(
            code="OUTPUT_ALREADY_EXISTS",
            message="Style profile output already exists and will not be overwritten.",
            details={"output_profile_path": str(output)},
            next_action="Choose a new output profile filename.",
        )
    return output


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    handle, temporary = tempfile.mkstemp(
        prefix=".layout-style-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _reference_style_descriptors(profile: dict[str, Any]) -> list[dict[str, Any]]:
    hierarchy = profile["hierarchy_style"]
    descriptors = [
        {
            "descriptor_id": "observed-hierarchy-reuse",
            "category": "hierarchy",
            "description": "Observed top-level hierarchy reuse and instance orientation.",
            "parameters": {
                "top_direct_instance_count": hierarchy["top_direct_instance_count"],
                "reused_top_child_cells": hierarchy["reused_top_child_cells"],
                "rotation_counts": hierarchy["rotation_counts"],
                "mirrored_instance_count": hierarchy["mirrored_instance_count"],
                "flattening_performed": False,
            },
        }
    ]
    for layer in profile["layer_styles"]:
        descriptors.append(
            {
                "descriptor_id": "observed-layer-%d-%d"
                % (layer["layer"], layer["datatype"]),
                "category": "layer_geometry",
                "description": "Observed geometry style for GDS layer %s."
                % layer["layer_token"],
                "parameters": {
                    "layer_token": layer["layer_token"],
                    "mapped_roles": layer["mapped_roles"],
                    "orthogonal_geometry_verified": layer[
                        "orthogonal_geometry_verified"
                    ],
                    "box_orientation_counts": layer["box_orientation_counts"],
                    "observed_box_dimensions": layer["observed_box_dimensions"],
                    "merged_topology": layer["merged_topology"],
                },
            }
        )
    return descriptors


def extract_layout_style_service(
    *,
    layout_path: str,
    top_cell: str | None,
    layermap_path: str | None,
    histogram_limit: int,
    output_profile_path: str | None,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: Callable[..., Any] = create_layout_snapshot,
    worker_runner: Callable[..., dict[str, Any]] = run_klayout_worker,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise AnalysisError(
            code="INVALID_TIMEOUT",
            message="timeout_seconds must be positive.",
            details={"timeout_seconds": timeout_seconds},
            next_action="Provide a timeout greater than zero seconds.",
        )
    if (
        isinstance(histogram_limit, bool)
        or not isinstance(histogram_limit, int)
        or histogram_limit < 1
        or histogram_limit > 100
    ):
        raise AnalysisError(
            code="INVALID_STYLE_HISTOGRAM_LIMIT",
            message="histogram_limit must be an integer from 1 to 100.",
            details={"histogram_limit": histogram_limit},
            next_action="Use a compact positive histogram limit such as 24.",
        )
    output = _validate_output_path(output_profile_path) if output_profile_path else None
    layers = load_layermap(layermap_path, require_m1=False) if layermap_path else {}
    with snapshot_factory(layout_path, purpose="layout") as snapshot:
        result = worker_runner(
            {
                "operation": "extract_layout_style",
                "layout_path": str(snapshot.path),
                "top_cell": top_cell,
                "layermap": {name: spec.to_dict() for name, spec in layers.items()},
                "histogram_limit": histogram_limit,
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if not result.get("ok"):
            return result
        core = result["style_profile"]
        core["source"] = {
            "layout_sha256": snapshot.sha256,
            "size_bytes": snapshot.size_bytes,
            "layermap_sha256": None
            if layermap_path is None
            else _file_sha256(layermap_path),
            "runtime_paths_in_profile": False,
        }
        core["reference_style_descriptors"] = _reference_style_descriptors(core)
        profile = {**core, "style_profile_sha256": canonical_sha256(core)}
        result["style_profile"] = profile
        result["style_profile_sha256"] = profile["style_profile_sha256"]
        result["production_ready"] = False
        result["source_layout_path"] = str(snapshot.source_path)
        result["layermap_path"] = (
            None
            if layermap_path is None
            else str(Path(layermap_path).expanduser().resolve())
        )
        if output is not None:
            _atomic_json(output, profile)
            result["output_profile_path"] = str(output)
            result["output_profile_fresh_read_verified"] = (
                json.loads(output.read_text(encoding="utf-8")) == profile
            )
        return result
