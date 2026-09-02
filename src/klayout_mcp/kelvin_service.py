"""Host-side orchestration services for the optional Kelvin M1 profile."""

from collections.abc import Callable
from contextlib import AbstractContextManager
import os
from typing import Any

from .errors import AnalysisError
from .klayout_adapter import LayoutSnapshot, create_layout_snapshot, run_klayout_worker
from .kelvin_routing import build_kelvin_routing_spec, kelvin_routing_plan_result


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


def _prepare_work_directory(work_directory_path: str) -> str:
    work_directory = os.path.abspath(work_directory_path)
    try:
        os.makedirs(work_directory, exist_ok=True)
    except OSError as exc:
        raise AnalysisError(
            code="OUTPUT_DIRECTORY_FAILED",
            message="Kelvin routing work directory could not be created.",
            details={"work_directory_path": work_directory, "error": str(exc)},
            next_action="Provide a writable directory under the project's output folder.",
        ) from exc
    return work_directory


def _require_path_inside_directory(path: str, directory: str) -> str:
    resolved = os.path.abspath(path)
    try:
        inside = os.path.commonpath([resolved, directory]) == directory
    except ValueError:
        inside = False
    if not inside:
        raise AnalysisError(
            code="KELVIN_OUTPUT_OUTSIDE_WORK_DIRECTORY",
            message="Provisional Kelvin output must stay inside its work directory.",
            details={"output_gds_path": resolved, "work_directory_path": directory},
            next_action=(
                "Choose an output_gds_path below the project output work directory; "
                "promote a verified final separately."
            ),
        )
    return resolved


def plan_kelvin_m1_routing_service(
    *,
    dimension_semantics: str | None,
    confirm_routing_contract: bool,
    splits: list[dict[str, int]] | None,
    site_origins_um: list[list[float]] | None,
) -> dict[str, Any]:
    """Build the pure deterministic Kelvin routing plan."""

    spec = build_kelvin_routing_spec(
        dimension_semantics=dimension_semantics,
        confirm_routing_contract=confirm_routing_contract,
        splits=splits,
        site_origins_um=site_origins_um,
    )
    return kelvin_routing_plan_result(spec)


def generate_kelvin_m1_teg_service(
    *,
    template_gds_path: str,
    output_gds_path: str,
    work_directory_path: str,
    dimension_semantics: str | None,
    confirm_routing_contract: bool,
    splits: list[dict[str, int]] | None,
    site_origins_um: list[list[float]] | None,
    reference_gds_path: str | None,
    reference_top_cell: str | None,
    top_cell: str | None,
    require_reference_equivalence: bool,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Validate, snapshot, generate, verify, and promote one Kelvin layout."""

    _validate_timeout(timeout_seconds)
    build_kelvin_routing_spec(
        dimension_semantics=dimension_semantics,
        confirm_routing_contract=confirm_routing_contract,
        splits=splits,
        site_origins_um=site_origins_um,
    )
    work_directory = _prepare_work_directory(work_directory_path)
    output_path = _require_path_inside_directory(output_gds_path, work_directory)
    with snapshot_factory(
        template_gds_path,
        purpose="padset",
        temporary_root=work_directory,
    ) as snapshot:
        result = worker_runner(
            {
                "operation": "generate_kelvin_m1_teg",
                "template_gds_path": str(snapshot.path),
                "output_gds_path": output_path,
                "work_directory_path": work_directory,
                "top_cell": top_cell,
                "reference_gds_path": reference_gds_path,
                "reference_top_cell": reference_top_cell,
                "require_reference_equivalence": require_reference_equivalence,
                "routing_spec": {
                    "dimension_semantics": dimension_semantics,
                    "confirm_routing_contract": confirm_routing_contract,
                    "splits": splits,
                    "site_origins_um": site_origins_um,
                },
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
            temporary_root=work_directory,
        )
    if result.get("ok"):
        result["template_gds_path"] = os.path.abspath(template_gds_path)
        result["template_snapshot_sha256"] = snapshot.sha256
        result["temporary_files_policy"] = (
            "all_worker_files_under_work_directory_and_cleaned"
        )
    return result


def compare_kelvin_layouts_service(
    *,
    candidate_gds_path: str,
    reference_gds_path: str,
    work_directory_path: str,
    candidate_top_cell: str | None,
    reference_top_cell: str | None,
    m1_layer: int,
    m1_datatype: int,
    klayout_executable: str | None,
    timeout_seconds: float,
    snapshot_factory: SnapshotFactory = create_layout_snapshot,
    worker_runner: WorkerRunner = run_klayout_worker,
) -> dict[str, Any]:
    """Compare immutable Kelvin snapshots including M1 topology."""

    _validate_timeout(timeout_seconds)
    work_directory = _prepare_work_directory(work_directory_path)
    with snapshot_factory(
        candidate_gds_path,
        purpose="sample",
        temporary_root=work_directory,
    ) as candidate_snapshot, snapshot_factory(
        reference_gds_path,
        purpose="sample",
        temporary_root=work_directory,
    ) as reference_snapshot:
        result = worker_runner(
            {
                "operation": "compare_kelvin_layouts",
                "candidate_gds_path": str(candidate_snapshot.path),
                "reference_gds_path": str(reference_snapshot.path),
                "candidate_top_cell": candidate_top_cell,
                "reference_top_cell": reference_top_cell,
                "m1": {"layer": m1_layer, "datatype": m1_datatype},
            },
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
            temporary_root=work_directory,
        )
    if result.get("ok"):
        result["candidate_gds_path"] = os.path.abspath(candidate_gds_path)
        result["reference_gds_path"] = os.path.abspath(reference_gds_path)
        result["temporary_files_policy"] = (
            "all_worker_files_under_work_directory_and_cleaned"
        )
    return result
