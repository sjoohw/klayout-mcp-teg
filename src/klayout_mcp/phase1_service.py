"""High-level create-only generation for nonproduction Phase 1 scaffolds."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .drawing_service import draw_manhattan_layout_service
from .evidence_state import evaluate_evidence_ladder
from .phase1_layout import compose_phase1_direct_layout


def generate_phase1_direct_teg_service(
    *,
    output_layout_path: str,
    top_cell: str,
    process_capability: Mapping[str, Any],
    request_plan: Mapping[str, Any],
    primitive_instances: Sequence[Mapping[str, Any]],
    pad_rail_width_um: float,
    confirm_nonproduction: bool,
    klayout_executable: str | None,
    timeout_seconds: float,
    drawing_runner: Callable[..., dict[str, Any]] = draw_manhattan_layout_service,
) -> dict[str, Any]:
    """Compose, publish without clobbering a concurrent winner, and attach provenance."""

    composition = compose_phase1_direct_layout(
        output_layout_path=output_layout_path,
        top_cell=top_cell,
        process_capability=process_capability,
        request_plan=request_plan,
        primitive_instances=primitive_instances,
        pad_rail_width_um=pad_rail_width_um,
    )
    drawing = composition["drawing_request"]
    result = drawing_runner(
        output_layout_path=drawing["output_layout_path"],
        dbu_um=drawing["dbu_um"],
        top_cell=drawing["top_cell"],
        cells=drawing["cells"],
        layers=drawing["layers"],
        operations=drawing["operations"],
        confirm_nonproduction=confirm_nonproduction,
        klayout_executable=klayout_executable,
        timeout_seconds=timeout_seconds,
    )
    if not result.get("ok"):
        return result
    expected = {
        "top_cell": top_cell,
        "top_cell_count": 1,
        "dbu_um": composition["drawing_request"]["dbu_um"],
        "bbox_um": [0.0, 0.0, *composition["frame_um"]],
        "fresh_reload_verified": True,
        "integer_dbu_geometry_verified": True,
        "orthogonal_manhattan_contract": True,
    }
    mismatches = {
        key: {"expected": value, "actual": result.get(key)}
        for key, value in expected.items()
        if result.get(key) != value
    }
    if mismatches:
        return {
            "ok": False,
            "code": "PHASE1_FRESH_RELOAD_INVARIANT_FAILED",
            "message": "The generated layout did not satisfy the Phase 1 fresh-reload invariants.",
            "details": {
                "mismatches": mismatches,
                "output_layout_path": result.get("output_layout_path"),
                "output_preserved_for_review": True,
            },
            "next_action": "Inspect the newly generated output; do not treat it as the verified final GDS.",
        }
    result["drawing_scope"] = "phase1_direct_measurement_teg"
    result["phase1_manifest"] = {
        "contract_version": composition["contract_version"],
        "process": composition["process"],
        "frame_um": composition["frame_um"],
        "pad_count": composition["pad_count"],
        "primitive_duts": composition["primitive_duts"],
        "terminal_routes": composition["terminal_routes"],
        "connectivity_projection": composition["connectivity_projection"],
        "first_metal_route_fingerprint_sha256": composition[
            "first_metal_route_fingerprint_sha256"
        ],
        "first_metal_search_evidence_fingerprint_sha256": composition[
            "first_metal_search_evidence_fingerprint_sha256"
        ],
        "drawing_plan_fingerprint_sha256": composition[
            "drawing_plan_fingerprint_sha256"
        ],
        "pad_rail_width_um": composition["pad_rail_width_um"],
        "pad_rail_clear_space_um": composition["pad_rail_clear_space_um"],
    }
    projection = composition["connectivity_projection"]
    result["evidence_ladder"] = evaluate_evidence_ladder(
        {
            "draft_schema_valid": True,
            "unresolved_questions_zero": request_plan.get("required_question_ids") == [],
            # M0 records the current trust gap explicitly. A future trusted client,
            # not this drawing service, must supply approval evidence.
            "approval_backend_trusted": False,
            "approval_verified": False,
            "plan_fingerprint_verified": bool(
                composition.get("drawing_plan_fingerprint_sha256")
            ),
            "routing_plan_complete": bool(
                composition.get("first_metal_route_fingerprint_sha256")
            ),
            "fresh_reload_verified": result.get("fresh_reload_verified") is True,
            "drawing_fingerprint_verified": True,
            "connectivity_projection_verified": all(
                projection.get(field) is True
                for field in (
                    "route_set_exact",
                    "different_net_route_spacing_verified",
                    "same_net_route_connectivity_verified",
                    "primitive_terminal_component_overlap_verified",
                )
            ),
        }
    )
    result["production_ready"] = False
    return result
