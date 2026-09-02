"""Host-side integrated Padset analysis service."""

from .errors import AnalysisError
from .klayout_adapter import run_klayout_worker
from .layermap import load_layermap
from .profiles import DEFAULT_TEG_PROFILE

def analyze_padset_snapshot(
    padset_path: str,
    layermap_path: str,
    top_cell: str | None = None,
    klayout_executable: str | None = None,
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count,
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count,
    pad_size_um: float = DEFAULT_TEG_PROFILE.pad_width_um,
    pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um,
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um,
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um,
    landing_search_half_depth_um: float = (
        DEFAULT_TEG_PROFILE.default_landing_search_half_depth_um
    ),
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
    timeout_seconds: float = 60.0,
    *,
    source_padset_path: str,
    snapshot_sha256: str,
    snapshot_size_bytes: int,
    image_path: str | None = None,
    image_width: int = 1600,
    image_height: int = 600,
    worker_runner=run_klayout_worker,
) -> dict[str, object]:
    """Run complete padset analysis in one KLayout process and one layout read."""

    try:
        if timeout_seconds <= 0:
            raise AnalysisError(
                code="INVALID_TIMEOUT",
                message="timeout_seconds must be positive.",
                details={"timeout_seconds": timeout_seconds},
                next_action="Provide a timeout greater than zero seconds.",
            )
        if landing_search_half_depth_um <= 0:
            raise AnalysisError(
                code="INVALID_LANDING_SEARCH_DEPTH",
                message="landing_search_half_depth_um must be positive.",
                details={
                    "landing_search_half_depth_um": landing_search_half_depth_um,
                },
                next_action="Provide a positive M1 boundary search half-depth in microns.",
            )
        m1 = load_layermap(layermap_path)["m1"]
        request: dict[str, object] = {
            "operation": "analyze_padset_integrated",
            "layout_path": padset_path,
            "top_cell": top_cell,
            "m1": m1.to_dict(),
            "expected_pad_count": expected_pad_count,
            "source_drain_pad_count": source_drain_pad_count,
            "expected_pad_width_um": pad_size_um,
            "expected_pad_height_um": pad_size_um,
            "expected_pitch_um": pitch_um,
            "device_width_um": device_width_um,
            "device_height_um": device_height_um,
            "pad_tolerance_um": tolerance_um,
            "landing_search_half_depth_um": landing_search_half_depth_um,
            "render_overlay": image_path is not None,
        }
        if image_path is not None:
            request.update(
                {
                    "image_path": image_path,
                    "image_width": image_width,
                    "image_height": image_height,
                }
            )
        analysis = worker_runner(
            request,
            executable_path=klayout_executable,
            timeout_seconds=timeout_seconds,
            hidden_view=image_path is not None,
        )
        if not analysis.get("ok"):
            details = dict(analysis.get("details", {}))
            if details.get("padset_path") == padset_path:
                details["padset_path"] = source_padset_path
            analysis["details"] = details
            return analysis

        required_keys = {
            "layout",
            "pad_count",
            "pads",
            "dut_slot_count",
            "dut_slots",
            "m1_extraction",
            "m1_connectivity",
            "layout_read_count",
        }
        if image_path is not None:
            required_keys.add("overlay")
        missing_keys = sorted(required_keys.difference(analysis))
        if missing_keys or not isinstance(analysis.get("layout"), dict):
            raise AnalysisError(
                code="KLAYOUT_RESPONSE_INVALID",
                message="Integrated KLayout analysis returned an incomplete response.",
                details={"missing_keys": missing_keys},
                next_action="Inspect the installed KLayout version and worker response.",
            )
        layout = analysis.pop("layout")
        missing_layout_keys = sorted(
            {"dbu_um", "top_cell", "m1", "klayout_version"}.difference(layout)
        )
        if missing_layout_keys:
            raise AnalysisError(
                code="KLAYOUT_RESPONSE_INVALID",
                message="Integrated KLayout analysis returned incomplete layout metadata.",
                details={"missing_layout_keys": missing_layout_keys},
                next_action="Inspect the installed KLayout version and worker response.",
            )
        analysis["padset"] = {
            "path": source_padset_path,
            "dbu_um": layout["dbu_um"],
            "top_cell": layout["top_cell"],
            "m1": layout["m1"],
            "klayout_version": layout["klayout_version"],
            "snapshot_sha256": snapshot_sha256,
            "snapshot_size_bytes": snapshot_size_bytes,
        }
        return analysis
    except AnalysisError as exc:
        return exc.to_result()
