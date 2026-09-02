"""Service boundary for immutable reference-layout registration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from .klayout_adapter import create_layout_snapshot, run_klayout_worker
from .layout_service import inspect_layout_service
from .errors import AnalysisError
from .reference_library import ReferenceLibrary


def default_reference_library_root() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(
        os.environ.get(
            "KLAYOUT_MCP_REFERENCE_ROOT",
            str(project_root / "output" / "reference-library"),
        )
    ).expanduser().resolve()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    resolved = Path(path).expanduser().resolve()
    try:
        with resolved.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise AnalysisError(
            code="REFERENCE_LAYERMAP_READ_FAILED",
            message="The reference layermap could not be hashed.",
            details={"layermap_path": str(resolved), "error_type": type(exc).__name__},
            next_action="Provide the exact readable layermap used for this process reference.",
        ) from exc
    return digest.hexdigest()


def register_reference_layout_service(
    *,
    layout_path: str,
    process_node: str,
    process_option: str,
    process_revision: str,
    top_cell: str | None,
    layermap_path: str | None,
    profile_name: str | None,
    profile_version: str | None,
    purpose_tags: list[str],
    description: str | None,
    library_root: str | None,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: Callable[..., Any] = create_layout_snapshot,
    worker_runner: Callable[..., dict[str, Any]] = run_klayout_worker,
) -> dict[str, Any]:
    """Capture one stable full GDS, inspect it once, and store it by content."""

    root = Path(library_root).expanduser().resolve() if library_root else default_reference_library_root()
    layermap_sha256 = _sha256_file(layermap_path) if layermap_path else None
    with snapshot_factory(layout_path, purpose="layout") as snapshot:
        inventory = inspect_layout_service(
            layout_path=str(snapshot.path),
            top_cell=top_cell,
            layermap_path=layermap_path,
            text_limit=0,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=snapshot_factory,
            worker_runner=worker_runner,
        )
        result = ReferenceLibrary(root).register(
            source_layout_path=str(snapshot.path),
            provenance_source_path=str(snapshot.source_path),
            process_node=process_node,
            process_option=process_option,
            process_revision=process_revision,
            inventory=inventory,
            profile_name=profile_name,
            profile_version=profile_version,
            layermap_sha256=layermap_sha256,
            purpose_tags=purpose_tags,
            description=description,
        )
    return {
        "ok": True,
        "reference": result,
        "library_root": str(root),
        "next_action": "List the registered reference, choose one cell/ROI/concern, and prepare a KLayout reference view.",
        "production_ready": False,
    }
