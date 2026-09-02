"""Domain-neutral layout inspection and semantic-comparison services."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from .errors import AnalysisError
from .klayout_adapter import LayoutSnapshot, create_layout_snapshot, run_klayout_worker
from .layermap import load_layermap


SnapshotFactory = Callable[..., AbstractContextManager[LayoutSnapshot]]
WorkerRunner = Callable[..., dict[str, Any]]


def _validate_timeout(timeout_seconds: float) -> None:
    if timeout_seconds <= 0:
        raise AnalysisError(
            code="INVALID_TIMEOUT",
            message="timeout_seconds must be positive.",
            details={"timeout_seconds": timeout_seconds},
            next_action="Provide a timeout greater than zero seconds.",
        )


def compare_layouts_service(
    *,
    candidate_layout_path: str,
    reference_layout_path: str,
    candidate_top_cell: str | None,
    reference_top_cell: str | None,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Compare immutable snapshots of two domain-neutral GDS/OAS layouts."""

    _validate_timeout(timeout_seconds)
    with snapshot_factory(candidate_layout_path, purpose="layout") as candidate:
        with snapshot_factory(reference_layout_path, purpose="layout") as reference:
            result = worker_runner(
                {
                    "operation": "compare_layouts",
                    "candidate_layout_path": str(candidate.path),
                    "reference_layout_path": str(reference.path),
                    "candidate_top_cell": candidate_top_cell,
                    "reference_top_cell": reference_top_cell,
                },
                executable_path=klayout_executable,
                timeout_seconds=timeout_seconds,
            )
            if result.get("ok"):
                result["candidate_layout_path"] = str(candidate.source_path)
                result["reference_layout_path"] = str(reference.source_path)
                result["candidate_sha256"] = candidate.sha256
                result["reference_sha256"] = reference.sha256
            return result


def inspect_layout_service(
    *,
    layout_path: str,
    top_cell: str | None,
    layermap_path: str | None,
    text_limit: int,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Inventory one immutable domain-neutral GDS/OAS snapshot."""

    _validate_timeout(timeout_seconds)
    if isinstance(text_limit, bool) or not isinstance(text_limit, int) or text_limit < 0:
        raise AnalysisError(
            code="INVALID_TEXT_LIMIT",
            message="text_limit must be a non-negative integer.",
            details={"text_limit": text_limit},
            next_action="Use zero to omit text records or a positive integer limit.",
        )
    layers = load_layermap(layermap_path) if layermap_path else {}
    with snapshot_factory(layout_path, purpose="layout") as snapshot:
        result = worker_runner(
            {
                "operation": "inspect_layout",
                "layout_path": str(snapshot.path),
                "top_cell": top_cell,
                "layermap": {key: value.to_dict() for key, value in layers.items()},
                "text_limit": text_limit,
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if result.get("ok"):
            result["layout"]["path"] = str(snapshot.source_path)
            result["layout"]["snapshot_sha256"] = snapshot.sha256
            result["layout"]["snapshot_size_bytes"] = snapshot.size_bytes
        return result
