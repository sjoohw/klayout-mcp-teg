"""Host-side immutable snapshot service for PCellizer hierarchy inventory."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from .errors import AnalysisError
from .klayout_adapter import LayoutSnapshot, create_layout_snapshot, run_klayout_worker
from .pcellizer_contract import PCELLIZER_SCHEMA_VERSION
from .workflow_manifest import canonical_sha256


SnapshotFactory = Callable[..., AbstractContextManager[LayoutSnapshot]]
WorkerRunner = Callable[..., dict[str, Any]]


def inventory_pcellizer_hierarchy_service(
    *,
    layout_path: str,
    top_cell: str | None,
    max_occurrences: int,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Inventory occurrence identity from one immutable source snapshot."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise AnalysisError(
            code="INVALID_TIMEOUT",
            message="timeout_seconds must be positive.",
            details={"timeout_seconds": timeout_seconds},
            next_action="Provide a timeout greater than zero seconds.",
        )
    if (
        isinstance(max_occurrences, bool)
        or not isinstance(max_occurrences, int)
        or max_occurrences <= 0
    ):
        raise AnalysisError(
            code="INVALID_PCELLIZER_OCCURRENCE_LIMIT",
            message="max_occurrences must be a positive integer.",
            details={"max_occurrences": max_occurrences},
            next_action="Use a positive limit sized for the source hierarchy.",
        )

    with snapshot_factory(layout_path, purpose="layout") as snapshot:
        result = worker_runner(
            {
                "operation": "inventory_pcellizer_hierarchy",
                "layout_path": str(snapshot.path),
                "top_cell": top_cell,
                "max_occurrences": max_occurrences,
            },
            executable_path=klayout_executable,
            timeout_seconds=float(timeout_seconds),
        )
        if not result.get("ok"):
            return result
        source_identity = {
            "schema_version": PCELLIZER_SCHEMA_VERSION,
            "kind": "SourceLayoutIdentity",
            "layout_path": str(snapshot.source_path),
            "layout_sha256": snapshot.sha256,
            "size_bytes": snapshot.size_bytes,
            "top_cell": result["layout"]["top_cell"],
            "dbu_um": result["layout"]["dbu_um"],
            "source_mutable": False,
        }
        result["layout"]["path"] = str(snapshot.source_path)
        result["source_identity"] = source_identity
        result["source_identity_sha256"] = canonical_sha256(source_identity)
        result["snapshot_read_count"] = 1
        result["input_layout_modified"] = False
        return result
