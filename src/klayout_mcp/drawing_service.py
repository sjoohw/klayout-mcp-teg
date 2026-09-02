"""Application service for domain-neutral Manhattan layout generation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .errors import AnalysisError
from .klayout_adapter import run_klayout_worker
from .manhattan_drawing import build_manhattan_drawing_plan
from .workflow_manifest import immutable_json_copy


def draw_manhattan_layout_service(
    *,
    output_layout_path: str,
    dbu_um: float,
    top_cell: str,
    cells: Sequence[str],
    layers: Sequence[Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
    confirm_nonproduction: bool,
    reference_citations: Sequence[Mapping[str, Any]] = (),
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Validate, execute, and fresh-reload one additive drawing transaction."""

    if not confirm_nonproduction:
        raise AnalysisError(
            code="DRAWING_EXPORT_REQUIRES_OPT_IN",
            message="Generic drawing output has no foundry DRC/LVS sign-off evidence.",
            details={"production_ready": False},
            next_action=(
                "Set confirm_nonproduction=true only after accepting that the new layout "
                "is not a fabrication-approved mask."
            ),
        )
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
    plan = build_manhattan_drawing_plan(
        output_layout_path=output_layout_path,
        dbu_um=dbu_um,
        top_cell=top_cell,
        cells=cells,
        layers=layers,
        operations=operations,
    )
    result = run_klayout_worker(
        plan,
        executable_path=klayout_executable,
        timeout_seconds=float(timeout_seconds),
    )
    if result.get("ok"):
        result["production_ready"] = False
        result["drawing_scope"] = "general_purpose_manhattan"
        result["input_layout_modified"] = False
        result["reference_guided"] = bool(reference_citations)
        result["reference_citations"] = immutable_json_copy(reference_citations)
    return result
