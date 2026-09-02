"""Host-side orchestration for conceptual fixed-25-Pad TEG assembly."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from .assembly import plan_teg_assembly
from .design_contract import confirm_dimension_semantics, layout_contract_status
from .errors import AnalysisError
from .klayout_adapter import LayoutSnapshot, create_layout_snapshot, run_klayout_worker
from .layermap import load_layermap
from .profiles import DEFAULT_TEG_PROFILE


SnapshotFactory = Callable[..., AbstractContextManager[LayoutSnapshot]]
WorkerRunner = Callable[..., dict[str, Any]]


def assemble_teg_service(
    *,
    padset_path: str,
    layermap_path: str,
    output_gds_path: str,
    dut_sweep: list[dict[str, Any]],
    dut_site_indices: list[int] | None,
    dimension_semantics: str | None,
    teg_name: str,
    export_static: bool,
    confirm_conceptual_export: bool,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Validate, snapshot, build, and verify a conceptual TEG artifact."""

    if not confirm_conceptual_export:
        raise AnalysisError(
            code="CONCEPTUAL_EXPORT_REQUIRES_OPT_IN",
            message=(
                "Assembly uses synthetic process geometry and cannot be treated as a "
                "production mask."
            ),
            details={"production_ready": False},
            next_action=(
                "Set confirm_conceptual_export=true only for a disposable visual/test "
                "artifact. Provide the real sample DUT before production export."
            ),
        )
    confirmed_semantics = confirm_dimension_semantics(dimension_semantics)
    if timeout_seconds <= 0:
        raise AnalysisError(
            code="INVALID_TIMEOUT",
            message="timeout_seconds must be positive.",
            details={"timeout_seconds": timeout_seconds},
            next_action="Provide a timeout greater than zero seconds.",
        )
    layermap_data = load_layermap(layermap_path)
    required_roles = {"m1", "active", "poly", "contact", "text"}
    missing_roles = sorted(required_roles.difference(layermap_data))
    if missing_roles:
        raise AnalysisError(
            code="ASSEMBLY_LAYERMAP_INCOMPLETE",
            message="Conceptual assembly requires every generated layer role explicitly.",
            details={
                "missing_layer_roles": missing_roles,
                "available_layer_roles": sorted(layermap_data),
            },
            next_action=(
                "Add explicit layer/datatype pairs for m1, active, poly, contact, and text."
            ),
        )
    roles_by_layer: dict[tuple[int, int], list[str]] = {}
    for role in sorted(required_roles):
        spec = layermap_data[role]
        roles_by_layer.setdefault((spec.layer, spec.datatype), []).append(role)
    collisions = [
        {"layer": layer, "datatype": datatype, "roles": roles}
        for (layer, datatype), roles in sorted(roles_by_layer.items())
        if len(roles) > 1
    ]
    if collisions:
        raise AnalysisError(
            code="ASSEMBLY_LAYERMAP_COLLISION",
            message="Generated conceptual geometry roles must use distinct layer/datatype pairs.",
            details={"collisions": collisions},
            next_action="Assign a distinct explicit layer/datatype pair to each generated role.",
        )

    plan = plan_teg_assembly(
        padset_path=padset_path,
        layermap_path=layermap_path,
        dut_sweep=dut_sweep,
        output_gds_path=output_gds_path,
        teg_name=teg_name,
        export_static=export_static,
        dut_site_indices=dut_site_indices,
    )
    with snapshot_factory(padset_path) as snapshot:
        request: dict[str, object] = {
            "operation": "assemble_teg",
            "padset_path": str(snapshot.path),
            "output_gds_path": plan.output_gds_path,
            "teg_name": plan.teg_name,
            "export_static": export_static,
            "conceptual_export_confirmed": True,
            "dut_sweep": plan.dut_sweep,
            "layermap": {key: value.to_dict() for key, value in layermap_data.items()},
            "expected_pad_count": DEFAULT_TEG_PROFILE.expected_pad_count,
            "source_drain_pad_count": DEFAULT_TEG_PROFILE.source_drain_pad_count,
            "expected_pad_width_um": DEFAULT_TEG_PROFILE.pad_width_um,
            "expected_pad_height_um": DEFAULT_TEG_PROFILE.pad_height_um,
            "expected_pitch_um": DEFAULT_TEG_PROFILE.pitch_um,
            "device_width_um": DEFAULT_TEG_PROFILE.device_width_um,
            "device_height_um": DEFAULT_TEG_PROFILE.device_height_um,
            "pad_tolerance_um": DEFAULT_TEG_PROFILE.default_tolerance_um,
            "landing_search_half_depth_um": (
                DEFAULT_TEG_PROFILE.default_landing_search_half_depth_um
            ),
            "m1": layermap_data["m1"].to_dict(),
        }
        result = worker_runner(
            request,
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
        if result.get("ok"):
            result["production_ready"] = False
            result["geometry_status"] = "conceptual_scaffold"
            result["electrical_connectivity_verified"] = False
            result["process_geometry_verified"] = False
            result["layout_contract"] = layout_contract_status(
                dimension_semantics=confirmed_semantics
            )
            result["input_padset"] = {
                "path": str(snapshot.source_path),
                "snapshot_sha256": snapshot.sha256,
                "snapshot_size_bytes": snapshot.size_bytes,
            }
            result["warning"] = (
                "Synthetic Active/Poly/Contact/M1 geometry is for visual testing only. "
                "Do not use this GDS as a production mask."
            )
        return result
