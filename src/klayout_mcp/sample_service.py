"""Host-side orchestration for sample DUT inventory."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from .errors import AnalysisError
from .klayout_adapter import LayoutSnapshot, create_layout_snapshot, run_klayout_worker
from .layermap import load_layermap


SnapshotFactory = Callable[..., AbstractContextManager[LayoutSnapshot]]
WorkerRunner = Callable[..., dict[str, Any]]


def inspect_sample_dut_service(
    *,
    sample_layout_path: str,
    layermap_path: str,
    sample_description: str | None,
    top_cell: str | None,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Snapshot and inventory a sample without inferring electrical meaning."""

    if timeout_seconds <= 0:
        raise AnalysisError(
            code="INVALID_TIMEOUT",
            message="timeout_seconds must be positive.",
            details={"timeout_seconds": timeout_seconds},
            next_action="Provide a timeout greater than zero seconds.",
        )
    layers = load_layermap(layermap_path)
    description_received = bool(
        sample_description is not None and sample_description.strip()
    )
    with snapshot_factory(sample_layout_path, purpose="sample") as snapshot:
        result = worker_runner(
            {
                "operation": "inspect_sample_layout",
                "layout_path": str(snapshot.path),
                "top_cell": top_cell,
                "layermap": {
                    name: spec.to_dict() for name, spec in sorted(layers.items())
                },
                "text_limit": 200,
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if not result.get("ok"):
            details = dict(result.get("details", {}))
            if details.get("sample_layout_path") == str(snapshot.path):
                details["sample_layout_path"] = str(snapshot.source_path)
            result["details"] = details
            return result

        required = {
            "layout",
            "layers",
            "shape_totals",
            "cells",
            "texts",
            "layermap_coverage",
            "layout_read_count",
            "input_layout_modified",
        }
        missing = sorted(required.difference(result))
        if missing or not isinstance(result.get("layout"), dict):
            raise AnalysisError(
                code="KLAYOUT_RESPONSE_INVALID",
                message="Sample DUT inventory returned an incomplete response.",
                details={"missing_keys": missing},
                next_action="Inspect the KLayout worker response and installed version.",
            )
        layout = result.pop("layout")
        layout["path"] = str(snapshot.source_path)
        layout["snapshot_sha256"] = snapshot.sha256
        layout["snapshot_size_bytes"] = snapshot.size_bytes
        result["sample"] = layout

    blockers = [
        "S/D/G/B labels and geometry are not treated as verified electrical connectivity.",
        "PCell parameters and sweep mapping are not inferred from geometry.",
    ]
    if not description_received:
        blockers.insert(0, "Sample device and parameter explanation is missing.")
    if result["layermap_coverage"]["unmapped_used_layers"]:
        blockers.append("Some used sample layers have no explicit layermap role.")
    result["pcell_readiness"] = {
        "inventory_complete": True,
        "production_ready": False,
        "sample_description_received": description_received,
        "blockers": blockers,
        "next_action": (
            "Review the inventory, map every production layer, and confirm S/D/G/B "
            "plus sweep parameters before generating Python PCell source."
        ),
    }
    return result
