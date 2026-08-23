"""STDIO MCP server."""

from __future__ import annotations

from importlib.metadata import version as package_version
import os
import platform
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import __version__
from .assembly import plan_teg_assembly
from .assembly import plan_teg_dut_sequence as plan_dut_sequence
from .drc_guardrails import DesignRuleConfig, verify_dut_design_rules
from .dut_geometry import DutParameters, build_dut_geometry, describe_dut_pcell_contract
from .errors import AnalysisError
from .geometry import Box
from .klayout_adapter import create_layout_snapshot, run_klayout_worker
from .layermap import load_layermap
from .mcp_protocol import ADDITIVE_WRITE, READ_ONLY, McpToolResult, protocol_tool
from .padset import PadDetectionConfig, analyze_pad_boxes as analyze_boxes
from .pcell_library import generate_pcell_python_source
from .profiles import DEFAULT_TEG_PROFILE
from .selection import plan_transistor_array as plan_array
from .selection import select_routed_units as select_units


mcp = FastMCP(
    "klayout-teg-mcp",
    instructions=(
        "Drawing and PCell MCP. Padset and layermap are mandatory for production work. "
        "Use padset DBU. Do not infer production layers. Do not run DRC or LVS by default."
    ),
)


@protocol_tool(mcp, annotations=READ_ONLY)
def server_status() -> McpToolResult:
    """Return server capabilities that do not require KLayout."""

    return {
        "ok": True,
        "server": "klayout-teg-mcp",
        "version": __version__,
        "capabilities": [
            "analyze_pad_boxes",
            "analyze_padset",
            "render_boundary_overlay",
            "select_routed_units",
            "plan_transistor_array",
            "describe_dut_pcell",
            "generate_dut_geometry",
            "inspect_sample_dut",
            "plan_teg_dut_sequence",
            "verify_design_rules",
            "assemble_teg",
            "export_pcell_code",
        ],
        "klayout_adapter": "subprocess",
        "runtime": {
            "python": platform.python_version(),
            "mcp_sdk": package_version("mcp"),
        },
        "klayout_support": {
            "minimum_version": "0.30.0",
            "validated_version": "0.30.10",
            "version_reported_by_layout_tools": True,
        },
    }


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def export_pcell_code(
    layermap_path: Annotated[
        str,
        Field(
            description=(
                "Required layermap with explicit m1, active, poly, and contact layers."
            )
        ),
    ],
    output_script_path: Annotated[
        str | None,
        Field(description="Optional file path to save the generated Python PCell script."),
    ] = None,
    confirm_conceptual_export: Annotated[
        bool,
        Field(
            description=(
                "Must be true to acknowledge that the generated PCell is conceptual, "
                "non-production, and electrically unverified."
            )
        ),
    ] = False,
) -> McpToolResult:
    """Generate explicitly acknowledged conceptual Python PCell code for KLayout."""

    try:
        if not confirm_conceptual_export:
            raise AnalysisError(
                code="CONCEPTUAL_PCELL_EXPORT_REQUIRES_OPT_IN",
                message=(
                    "PCell source uses synthetic process geometry and is not a production "
                    "device."
                ),
                details={"production_ready": False},
                next_action=(
                    "Set confirm_conceptual_export=true only for disposable GUI and "
                    "automation testing."
                ),
            )
        layermap_data = load_layermap(layermap_path)
        source_code = generate_pcell_python_source(layermap_data)

        if output_script_path:
            out_path = os.path.abspath(output_script_path)
            out_dir = os.path.dirname(out_path)
            try:
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(out_path, "x", encoding="utf-8") as handle:
                    handle.write(source_code)
            except FileExistsError:
                raise AnalysisError(
                    code="OUTPUT_EXISTS",
                    message="PCell script output already exists.",
                    details={"output_script_path": out_path},
                    next_action=(
                        "Provide a new output_script_path. Existing files are not overwritten."
                    ),
                ) from None
            except OSError as exc:
                raise AnalysisError(
                    code="OUTPUT_WRITE_FAILED",
                    message="PCell script output could not be written.",
                    details={
                        "output_script_path": out_path,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    next_action=(
                        "Provide a writable output path in an accessible directory."
                    ),
                ) from exc
            return {
                "ok": True,
                "production_ready": False,
                "geometry_status": "conceptual_scaffold",
                "electrical_connectivity_verified": False,
                "output_script_path": out_path,
                "code_length": len(source_code),
                "source_code": source_code,
                "warning": (
                    "Synthetic process geometry is for GUI and automation testing only. "
                    "Do not use this PCell as a fabrication mask."
                ),
            }

        return {
            "ok": True,
            "production_ready": False,
            "geometry_status": "conceptual_scaffold",
            "electrical_connectivity_verified": False,
            "code_length": len(source_code),
            "source_code": source_code,
            "warning": (
                "Synthetic process geometry is for GUI and automation testing only. "
                "Do not use this PCell as a fabrication mask."
            ),
        }
    except AnalysisError as exc:
        return exc.to_result()



@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def assemble_teg(
    padset_path: Annotated[
        str,
        Field(description="Existing padset GDS/OAS path. The input file is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap path containing layer definitions."),
    ],
    output_gds_path: Annotated[
        str,
        Field(description="Path where the final assembled TEG GDS will be written."),
    ],
    dut_sweep: Annotated[
        list[dict[str, Any]],
        Field(description="Sweep list containing either 1 common config or 21 site configs."),
    ],
    teg_name: str = "TEG_DUT_ARRAY_V1",
    export_static: bool = True,
    confirm_conceptual_export: Annotated[
        bool,
        Field(
            description=(
                "Must be true to acknowledge that synthetic process geometry is "
                "non-production and electrically unverified."
            )
        ),
    ] = False,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Export an explicitly acknowledged, non-production 21-site GDS scaffold."""

    try:
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
            {
                "layer": layer,
                "datatype": datatype,
                "roles": roles,
            }
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
        )

        with create_layout_snapshot(padset_path) as snapshot:
            request: dict[str, object] = {
                "operation": "assemble_teg",
                "padset_path": str(snapshot.path),
                "output_gds_path": plan.output_gds_path,
                "teg_name": plan.teg_name,
                "export_static": export_static,
                "conceptual_export_confirmed": True,
                "dut_sweep": plan.dut_sweep,
                "layermap": {k: v.to_dict() for k, v in layermap_data.items()},
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
            result = run_klayout_worker(
                request,
                executable_path=klayout_executable,
                timeout_seconds=timeout_seconds,
            )
            if result.get("ok"):
                result["production_ready"] = False
                result["geometry_status"] = "conceptual_scaffold"
                result["electrical_connectivity_verified"] = False
                result["process_geometry_verified"] = False
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
    except AnalysisError as exc:
        return exc.to_result()



@protocol_tool(mcp, annotations=READ_ONLY)
def plan_teg_dut_sequence(
    dut_slots: Annotated[
        list[dict[str, Any]],
        Field(description="The 21 DUT slots returned by analyze_padset or analyze_pad_boxes."),
    ],
    site_parameter_sets: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Exactly 21 entries shaped as {site: N, parameters: {...}}. "
                "Each site number must appear once."
            )
        ),
    ],
    defaults: Annotated[
        dict[str, Any] | None,
        Field(description="Optional common DUT parameters applied before site overrides."),
    ] = None,
) -> McpToolResult:
    """Plan the fixed 21-site DUT sequence without writing a production layout."""

    try:
        return plan_dut_sequence(dut_slots, site_parameter_sets, defaults)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def verify_design_rules(
    dut_geometry: Annotated[
        dict[str, object],
        Field(description="Generated DUT geometry dictionary containing shape boxes and terminals."),
    ],
    min_m1_width_um: float = 0.28,
    min_m1_space_um: float = 0.28,
    min_landing_overlap_um: float = 0.1,
    min_poly_width_um: float = 0.08,
    min_contact_size_um: float = 0.18,
    comparison_tolerance_um: float = 1e-9,
) -> McpToolResult:
    """Verify Key Design Rules (width, space, overlap, size) on DUT geometry."""

    try:
        rules = DesignRuleConfig(
            min_m1_width_um=min_m1_width_um,
            min_m1_space_um=min_m1_space_um,
            min_landing_overlap_um=min_landing_overlap_um,
            min_poly_width_um=min_poly_width_um,
            min_contact_size_um=min_contact_size_um,
            comparison_tolerance_um=comparison_tolerance_um,
        )
        return verify_dut_design_rules(dut_geometry, rules)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def describe_dut_pcell() -> McpToolResult:
    """Return the abstract DUT PCell parameter and S/D/G/B terminal contract."""

    return describe_dut_pcell_contract()



@protocol_tool(mcp, annotations=READ_ONLY)
def generate_dut_geometry(
    w_um: float = 1.0,
    l_um: float = 0.1,
    array_rows: int = 4,
    array_cols: int = 8,
    pitch_x_um: float = 2.0,
    pitch_y_um: float = 2.0,
    routed_device_count: int = 10,
    m1_width_um: float = 0.4,
    m1_overlap_um: float = 0.2,
    device_window_um: Annotated[
        list[float] | None,
        Field(description="DUT-local device window [x1,y1,x2,y2] in microns."),
    ] = None,
    routing_boundary_um: Annotated[
        list[float] | None,
        Field(description="DUT-local routing boundary [x1,y1,x2,y2] in microns."),
    ] = None,
) -> McpToolResult:
    """Generate a non-production DUT geometry scaffold and terminal stubs."""

    try:
        dev_win = (
            Box.from_sequence(device_window_um)
            if device_window_um is not None
            else Box(-17.5, -20.0, 17.5, 20.0)
        )
        rout_bound = (
            Box.from_sequence(routing_boundary_um)
            if routing_boundary_um is not None
            else Box(-20.0, -20.0, 20.0, 20.0)
        )
        params = DutParameters(
            w_um=w_um,
            l_um=l_um,
            array_rows=array_rows,
            array_cols=array_cols,
            pitch_x_um=pitch_x_um,
            pitch_y_um=pitch_y_um,
            routed_device_count=routed_device_count,
            m1_width_um=m1_width_um,
            m1_overlap_um=m1_overlap_um,
            device_window_um=dev_win,
            routing_boundary_um=rout_bound,
        )
        result = build_dut_geometry(params)
        return result.to_dict()
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def inspect_sample_dut(
    sample_layout_path: Annotated[
        str,
        Field(description="Existing sample DUT GDS/OAS path. The input is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap with explicit process layer roles."),
    ],
    sample_description: Annotated[
        str | None,
        Field(description="User explanation of device type, terminals, and sweep meaning."),
    ] = None,
    top_cell: str | None = None,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Inventory a sample DUT before any PCell source or production geometry is generated."""

    try:
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
        with create_layout_snapshot(sample_layout_path, purpose="sample") as snapshot:
            result = run_klayout_worker(
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
    except AnalysisError as exc:
        return exc.to_result()



@protocol_tool(mcp, annotations=READ_ONLY)
def analyze_pad_boxes(
    boxes_um: Annotated[
        list[list[float]],
        Field(description="M1 pad candidate boxes as [x1,y1,x2,y2] in microns."),
    ],
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count,
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count,
    pad_size_um: float = DEFAULT_TEG_PROFILE.pad_width_um,
    pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um,
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um,
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um,
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
) -> McpToolResult:
    """Detect one horizontal pad row and derive Source/Drain DUT slot boundaries."""

    try:
        config = PadDetectionConfig(
            expected_pad_count=expected_pad_count,
            source_drain_pad_count=source_drain_pad_count,
            expected_pad_width_um=pad_size_um,
            expected_pad_height_um=pad_size_um,
            expected_pitch_um=pitch_um,
            size_tolerance_um=tolerance_um,
            alignment_tolerance_um=tolerance_um,
            pitch_tolerance_um=tolerance_um,
            device_width_um=device_width_um,
            device_height_um=device_height_um,
        )
        return analyze_boxes(boxes_um, config)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def select_routed_units(
    unit_centers_um: Annotated[
        list[list[float]],
        Field(
            description=(
                "Transistor centers as [x_um,y_um] in stable array-index order. "
                "Returned indices are 1-based positions in this list."
            )
        ),
    ],
    device_window_um: Annotated[
        list[float],
        Field(description="DUT-local device window [x1,y1,x2,y2] in microns."),
    ],
    routed_device_count: Annotated[
        int,
        Field(description="Number of transistor units to connect for measurement."),
    ],
    edge_inset_um: Annotated[
        float,
        Field(description="Excluded distance from every device-window edge in microns."),
    ] = 5.0,
) -> McpToolResult:
    """Choose one reusable, deterministic routed-transistor index pattern."""

    try:
        return select_units(
            unit_centers_um,
            device_window_um,
            routed_device_count,
            edge_inset_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_transistor_array(
    array_rows: int,
    array_cols: int,
    pitch_x_um: float,
    pitch_y_um: float,
    routed_device_count: int,
    device_window_um: Annotated[
        list[float] | None,
        Field(description="DUT-local device window [x1,y1,x2,y2] in microns."),
    ] = None,
    edge_inset_um: float = 5.0,
) -> McpToolResult:
    """Plan one centered transistor grid and its reusable routed-unit pattern."""

    try:
        return plan_array(
            array_rows,
            array_cols,
            pitch_x_um,
            pitch_y_um,
            routed_device_count,
            (
                device_window_um
                if device_window_um is not None
                else [-17.5, -20.0, 17.5, 20.0]
            ),
            edge_inset_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


def _analyze_padset_snapshot(
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
        analysis = run_klayout_worker(
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


@protocol_tool(mcp, annotations=READ_ONLY)
def analyze_padset(
    padset_path: Annotated[
        str,
        Field(description="Existing padset GDS/OAS path. The input file is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap path containing an explicit layers.m1 entry."),
    ],
    top_cell: str | None = None,
    klayout_executable: str | None = None,
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count,
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count,
    pad_size_um: float = DEFAULT_TEG_PROFILE.pad_width_um,
    pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um,
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um,
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um,
    landing_search_half_depth_um: Annotated[
        float,
        Field(description="Half-depth of each M1 boundary search band in microns."),
    ] = DEFAULT_TEG_PROFILE.default_landing_search_half_depth_um,
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Analyze one immutable copy of the padset input."""

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
        load_layermap(layermap_path)
        with create_layout_snapshot(padset_path) as snapshot:
            return _analyze_padset_snapshot(
                padset_path=str(snapshot.path),
                layermap_path=layermap_path,
                top_cell=top_cell,
                klayout_executable=klayout_executable,
                expected_pad_count=expected_pad_count,
                source_drain_pad_count=source_drain_pad_count,
                pad_size_um=pad_size_um,
                pitch_um=pitch_um,
                device_width_um=device_width_um,
                device_height_um=device_height_um,
                landing_search_half_depth_um=landing_search_half_depth_um,
                tolerance_um=tolerance_um,
                timeout_seconds=timeout_seconds,
                source_padset_path=str(snapshot.source_path),
                snapshot_sha256=snapshot.sha256,
                snapshot_size_bytes=snapshot.size_bytes,
            )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def render_boundary_overlay(
    padset_path: Annotated[
        str,
        Field(description="Existing padset GDS/OAS path. The input file is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap path containing an explicit layers.m1 entry."),
    ],
    image_path: Annotated[
        str,
        Field(description="New PNG path for the debug-only boundary overlay."),
    ],
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
    image_width: int = 1600,
    image_height: int = 600,
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Analyze a padset and render pads, slots, and landing states as view markers."""
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
        load_layermap(layermap_path)
        with create_layout_snapshot(padset_path) as snapshot:
            analysis = _analyze_padset_snapshot(
                padset_path=str(snapshot.path),
                layermap_path=layermap_path,
                top_cell=top_cell,
                klayout_executable=klayout_executable,
                expected_pad_count=expected_pad_count,
                source_drain_pad_count=source_drain_pad_count,
                pad_size_um=pad_size_um,
                pitch_um=pitch_um,
                device_width_um=device_width_um,
                device_height_um=device_height_um,
                landing_search_half_depth_um=landing_search_half_depth_um,
                tolerance_um=tolerance_um,
                timeout_seconds=timeout_seconds,
                source_padset_path=str(snapshot.source_path),
                snapshot_sha256=snapshot.sha256,
                snapshot_size_bytes=snapshot.size_bytes,
                image_path=image_path,
                image_width=image_width,
                image_height=image_height,
            )
            if not analysis.get("ok"):
                return analysis
    except AnalysisError as exc:
        return exc.to_result()
    return {
        "ok": True,
        "padset": analysis["padset"],
        "pad_count": analysis["pad_count"],
        "dut_slot_count": analysis["dut_slot_count"],
        "layout_read_count": analysis["layout_read_count"],
        "unresolved_landings": analysis["m1_connectivity"]["unresolved_landings"],
        "overlay": analysis["overlay"],
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
