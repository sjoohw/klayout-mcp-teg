"""Worker executed by KLayout's bundled Python runtime."""

import json
import os
import sys
import tempfile
import pya


PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from klayout_mcp.errors import AnalysisError
from klayout_mcp.drc_guardrails import analyze_m1_connectivity
from klayout_mcp.dut_geometry import DutParameters, build_dut_geometry
from klayout_mcp.geometry import Box
from klayout_mcp.padset import PadDetectionConfig, analyze_pad_boxes
from klayout_mcp.profiles import DEFAULT_TEG_PROFILE
from klayout_mcp.worker_overlay import render_boundary_overlay as _render_boundary_overlay
from klayout_mcp.worker_protocol import worker_error as _error


def _required_variable(name):
    value = globals().get(name)
    if not value:
        raise RuntimeError("Pass -rd %s=<path>" % name)
    return os.path.abspath(str(value))


def _select_top(layout, requested_name):
    if requested_name:
        cell = layout.cell(str(requested_name))
        if cell is None:
            return None, _error(
                "TOP_CELL_NOT_FOUND",
                "Requested top cell does not exist.",
                {"requested_top_cell": requested_name,
                 "top_cells": [item.name for item in layout.top_cells()]},
                "Use one of the reported top cell names.",
            )
        return cell, None

    top_cells = list(layout.top_cells())
    if len(top_cells) != 1:
        return None, _error(
            "TOP_CELL_AMBIGUOUS",
            "Layout does not have exactly one top cell.",
            {"top_cells": [item.name for item in top_cells]},
            "Provide top_cell explicitly. Automatic selection is disabled.",
        )
    return top_cells[0], None


def _find_layer(layout, layer_number, datatype):
    for index in layout.layer_indices():
        info = layout.get_info(index)
        if info.layer == layer_number and info.datatype == datatype:
            return index
    return None


def _box_um(box, dbu):
    return [
        box.left * dbu,
        box.bottom * dbu,
        box.right * dbu,
        box.top * dbu,
    ]


def _optional_box_um(box, dbu):
    if box is None or box.empty():
        return None
    return _box_um(box, dbu)


def _shape_kind(shape):
    if shape.is_box():
        return "box"
    if shape.is_path():
        return "path"
    if shape.is_polygon():
        return "polygon"
    if shape.is_text():
        return "text"
    if shape.is_edge():
        return "edge"
    if hasattr(shape, "is_point") and shape.is_point():
        return "point"
    return "other"


def _point_um(point, dbu):
    return [point.x * dbu, point.y * dbu]


def _polygon_um(polygon, dbu):
    return {
        "hull": [_point_um(point, dbu) for point in polygon.each_point_hull()],
        "holes": [
            [_point_um(point, dbu) for point in polygon.each_point_hole(index)]
            for index in range(polygon.holes())
        ],
    }


def _component_records(region, dbu):
    polygons = list(region.merged().each())
    polygons.sort(
        key=lambda polygon: (
            polygon.bbox().left,
            polygon.bbox().bottom,
            polygon.bbox().right,
            polygon.bbox().top,
        )
    )
    records = [
        {"id": index, "bbox_um": _box_um(polygon.bbox(), dbu)}
        for index, polygon in enumerate(polygons, start=1)
    ]
    return polygons, records


def _landing_record(component, band, component_id, dbu):
    clipped = (pya.Region(component) & pya.Region(band)).merged()
    polygons = list(clipped.each_merged())
    polygons.sort(
        key=lambda polygon: (
            polygon.bbox().left,
            polygon.bbox().bottom,
            polygon.bbox().right,
            polygon.bbox().top,
        )
    )
    if not polygons:
        return {
            "status": "unresolved",
            "component_id": component_id,
            "search_band_um": _box_um(band, dbu),
            "polygons_um": [],
            "bbox_um": None,
            "area_um2": 0.0,
        }
    return {
        "status": "resolved",
        "component_id": component_id,
        "search_band_um": _box_um(band, dbu),
        "polygons_um": [_polygon_um(polygon, dbu) for polygon in polygons],
        "bbox_um": _box_um(clipped.bbox(), dbu),
        "area_um2": clipped.area() * dbu * dbu,
    }


def _normalized_pad_candidates(region, component_polygons, request, dbu):
    pad_width_um = float(request["expected_pad_width_um"])
    pad_height_um = float(request["expected_pad_height_um"])
    tolerance_um = float(request["pad_tolerance_um"])
    minimum_half_um = min(pad_width_um, pad_height_um) / 2.0
    opening_um = minimum_half_um - max(tolerance_um, dbu)
    if opening_um <= 0:
        return None, _error(
            "INVALID_PAD_OPENING",
            "Pad dimensions are too small for Region opening.",
            {
                "pad_width_um": pad_width_um,
                "pad_height_um": pad_height_um,
                "tolerance_um": tolerance_um,
                "dbu_um": dbu,
            },
            "Increase pad dimensions or reduce the tolerance.",
        )

    opening_dbu = int(round(opening_um / dbu))
    opened = region.merged().sized(-opening_dbu).sized(opening_dbu).merged()
    candidates_by_key = {}

    def add_candidate(polygon, detection_method):
        box = polygon.bbox()
        width_um = box.width() * dbu
        height_um = box.height() * dbu
        if abs(width_um - pad_width_um) > tolerance_um:
            return None
        if abs(height_um - pad_height_um) > tolerance_um:
            return None
        core_region = pya.Region(polygon)
        component_ids = [
            index
            for index, component in enumerate(component_polygons, start=1)
            if not (core_region & pya.Region(component)).is_empty()
        ]
        if len(component_ids) != 1:
            return _error(
                "PAD_COMPONENT_AMBIGUOUS",
                "A normalized pad core does not map to exactly one M1 component.",
                {"bbox_um": _box_um(box, dbu), "component_ids": component_ids},
                "Inspect overlapping M1 geometry or the pad normalization settings.",
            )
        key = (box.left, box.bottom, box.right, box.top, component_ids[0])
        existing = candidates_by_key.get(key)
        if existing is None or detection_method == "opening":
            candidates_by_key[key] = {
                "bbox_um": _box_um(box, dbu),
                "component_id": component_ids[0],
                "detection_method": detection_method,
            }
        return None

    for polygon in opened.each():
        error = add_candidate(polygon, "opening")
        if error:
            return None, error

    # A large opening erases mesh/slotted pads. Fill holes component by
    # component and accept only an exact square outer hull. Attached routes
    # enlarge the hull and therefore remain handled by the opening path.
    for component in component_polygons:
        hulls = pya.Region(component).hulls()
        for polygon in hulls.each():
            error = add_candidate(polygon, "component_hull")
            if error:
                return None, error

    candidates = list(candidates_by_key.values())
    candidates.sort(key=lambda item: tuple(item["bbox_um"]))
    return candidates, None


def _integrated_analyze_padset(request):
    """Inspect, derive slots, extract landings, and optionally render after one read."""

    layout_path = os.path.abspath(str(request["layout_path"]))
    if not os.path.isfile(layout_path):
        return _error(
            "PADSET_NOT_FOUND",
            "Padset layout does not exist.",
            {"padset_path": layout_path},
            "Provide an existing GDS or OAS path.",
        )

    layout = pya.Layout()
    try:
        layout.read(layout_path)
    except Exception as exc:
        return _error(
            "PADSET_READ_FAILED",
            "KLayout could not read the padset.",
            {"padset_path": layout_path, "error": str(exc)},
            "Check the layout format and file integrity.",
        )

    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        return top_error

    m1 = request["m1"]
    layer_index = _find_layer(layout, int(m1["layer"]), int(m1["datatype"]))
    if layer_index is None:
        available = []
        for index in layout.layer_indices():
            info = layout.get_info(index)
            available.append([info.layer, info.datatype])
        return _error(
            "M1_LAYER_NOT_FOUND",
            "The layermap M1 layer is absent from the padset.",
            {"m1": m1, "available_layers": available},
            "Check the supplied layermap against the padset.",
        )

    raw_boxes_um = []
    shape_counts = {"box": 0, "path": 0, "polygon": 0, "other": 0}
    iterator = layout.begin_shapes(top.cell_index(), layer_index)
    while not iterator.at_end():
        shape = iterator.shape()
        if shape.is_box():
            shape_counts["box"] += 1
            raw_boxes_um.append(
                _box_um(shape.box.transformed(iterator.itrans()), layout.dbu)
            )
        elif shape.is_path():
            shape_counts["path"] += 1
        elif shape.is_polygon():
            shape_counts["polygon"] += 1
        else:
            shape_counts["other"] += 1
        iterator.next()

    all_m1 = pya.Region(layout.begin_shapes(top.cell_index(), layer_index)).merged()
    component_polygons, component_records = _component_records(all_m1, layout.dbu)
    pad_candidates, candidate_error = _normalized_pad_candidates(
        all_m1, component_polygons, request, layout.dbu
    )
    if candidate_error:
        return candidate_error

    extraction = {
        "raw_box_count": len(raw_boxes_um),
        "shape_counts": shape_counts,
        "normalized_pad_candidate_count": len(pad_candidates),
        "component_count": len(component_records),
    }
    config = PadDetectionConfig(
        expected_pad_count=int(request["expected_pad_count"]),
        source_drain_pad_count=int(request["source_drain_pad_count"]),
        expected_pad_width_um=float(request["expected_pad_width_um"]),
        expected_pad_height_um=float(request["expected_pad_height_um"]),
        expected_pitch_um=float(request["expected_pitch_um"]),
        size_tolerance_um=float(request["pad_tolerance_um"]),
        alignment_tolerance_um=float(request["pad_tolerance_um"]),
        pitch_tolerance_um=float(request["pad_tolerance_um"]),
        device_width_um=float(request["device_width_um"]),
        device_height_um=float(request["device_height_um"]),
    )
    try:
        analysis = analyze_pad_boxes(
            [candidate["bbox_um"] for candidate in pad_candidates], config
        )
    except AnalysisError as exc:
        exc.details["m1_extraction"] = extraction
        return exc.to_result()

    tolerance_um = float(request["pad_tolerance_um"])
    pad_component_ids = {}
    unmatched_pads = []
    for pad in analysis["pads"]:
        matches = [
            candidate
            for candidate in pad_candidates
            if all(
                abs(float(actual) - float(expected)) <= tolerance_um
                for actual, expected in zip(candidate["bbox_um"], pad["bbox_um"])
            )
        ]
        if len(matches) != 1:
            unmatched_pads.append(int(pad["number"]))
            continue
        component_id = int(matches[0]["component_id"])
        pad["m1_component_id"] = component_id
        pad_component_ids[int(pad["number"])] = component_id
    if unmatched_pads:
        return _error(
            "PAD_COMPONENT_MAPPING_FAILED",
            "Detected pads could not be mapped to unique M1 components.",
            {"pad_numbers": unmatched_pads},
            "Inspect normalized pad candidates and M1 overlaps.",
        )

    pads_by_component = {}
    for pad_number, component_id in pad_component_ids.items():
        pads_by_component.setdefault(component_id, []).append(pad_number)
    short_groups = [numbers for numbers in pads_by_component.values() if len(numbers) > 1]
    if short_groups:
        return _error(
            "PAD_SHORT_DETECTED",
            "Multiple pads belong to the same M1 connected component.",
            {"shorted_pad_groups": short_groups},
            "Inspect the padset M1 geometry. Automatic landing assignment is stopped.",
        )

    half_depth_um = float(request["landing_search_half_depth_um"])
    half_depth_dbu = int(round(half_depth_um / layout.dbu))
    if half_depth_dbu < 1:
        return _error(
            "INVALID_LANDING_SEARCH_DEPTH",
            "Landing search half-depth must span at least one padset DBU.",
            {
                "landing_search_half_depth_um": half_depth_um,
                "padset_dbu_um": layout.dbu,
            },
            "Increase landing_search_half_depth_um.",
        )

    unresolved_landings = []
    for slot in analysis["dut_slots"]:
        target_component_ids = {
            "source": pad_component_ids[int(slot["source_pad"])],
            "drain": pad_component_ids[int(slot["drain_pad"])],
            "gate": pad_component_ids[int(slot["gate_pad"])],
            "body": pad_component_ids[int(slot["body_pad"])],
        }
        slot["target_component_ids"] = target_component_ids
        x1, y1, x2, y2 = [
            int(round(float(value) / layout.dbu))
            for value in slot["routing_boundary_um"]
        ]
        bands = {
            "source": pya.Box(x1 - half_depth_dbu, y1, x1 + half_depth_dbu, y2),
            "drain": pya.Box(x2 - half_depth_dbu, y1, x2 + half_depth_dbu, y2),
            "gate": pya.Box(x1, y2 - half_depth_dbu, x2, y2 + half_depth_dbu),
            "body": pya.Box(x1, y1 - half_depth_dbu, x2, y1 + half_depth_dbu),
        }
        landings = {}
        unresolved_roles = []
        for role in ("source", "drain", "gate", "body"):
            component_id = target_component_ids[role]
            landing = _landing_record(
                component_polygons[component_id - 1],
                bands[role],
                component_id,
                layout.dbu,
            )
            landings[role] = landing
            if landing["status"] != "resolved":
                unresolved_roles.append(role)
        slot["landing_status"] = "resolved" if not unresolved_roles else "unresolved"
        slot["landings"] = landings
        if unresolved_roles:
            unresolved_landings.append(
                {"site": slot["site"], "roles": unresolved_roles}
            )

    analysis["warnings"] = [
        warning
        for warning in analysis.get("warnings", [])
        if "Gate and Body landings are unresolved" not in warning
    ]
    if unresolved_landings:
        analysis["warnings"].append(
            "Some target M1 components do not cross their site boundary search bands."
        )
    analysis["layout"] = {
        "path": layout_path,
        "dbu_um": layout.dbu,
        "top_cell": top.name,
        "top_cells": [item.name for item in layout.top_cells()],
        "m1": {"layer": int(m1["layer"]), "datatype": int(m1["datatype"])},
        "klayout_version": pya.Application.instance().version(),
    }
    analysis["m1_extraction"] = extraction
    analysis["m1_connectivity"] = {
        "pad_component_ids": {
            str(number): component_id
            for number, component_id in sorted(pad_component_ids.items())
        },
        "common_pad_components": {
            "odd_gate_pad_23": pad_component_ids[23],
            "even_gate_pad_24": pad_component_ids[24],
            "body_pad_25": pad_component_ids[DEFAULT_TEG_PROFILE.body_pad],
        },
        "shorted_pad_groups": [],
        "landing_search_half_depth_um": half_depth_dbu * layout.dbu,
        "unresolved_landings": unresolved_landings,
    }

    if request.get("render_overlay"):
        overlay_request = dict(request)
        overlay_request["top_cell"] = top.name
        overlay_request["pads"] = analysis["pads"]
        overlay_request["dut_slots"] = analysis["dut_slots"]
        overlay = _render_boundary_overlay(overlay_request, existing_layout=layout)
        if not overlay.get("ok"):
            return overlay
        analysis["overlay"] = overlay
    analysis["layout_read_count"] = 1
    return analysis


def _inspect_sample_layout(request):
    """Inventory a sample DUT without assigning unprovided process meaning."""

    layout_path = os.path.abspath(str(request["layout_path"]))
    if not os.path.isfile(layout_path):
        return _error(
            "SAMPLE_LAYOUT_NOT_FOUND",
            "Sample layout does not exist.",
            {"sample_layout_path": layout_path},
            "Provide an existing sample DUT GDS or OAS path.",
        )

    layout = pya.Layout()
    try:
        layout.read(layout_path)
    except Exception as exc:
        return _error(
            "SAMPLE_LAYOUT_READ_FAILED",
            "KLayout could not read the sample DUT layout.",
            {"sample_layout_path": layout_path, "error": str(exc)},
            "Check the sample format and file integrity.",
        )

    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        if top_error.get("code") == "TOP_CELL_AMBIGUOUS":
            top_error["next_action"] = "Provide top_cell for the sample DUT explicitly."
        return top_error

    role_by_layer = {}
    for role, spec in request["layermap"].items():
        key = (int(spec["layer"]), int(spec["datatype"]))
        role_by_layer.setdefault(key, []).append(str(role))
    for roles in role_by_layer.values():
        roles.sort()

    layer_records = []
    mapped_roles_seen = set()
    unmapped_used_layers = []
    shape_totals = {}
    text_records = []
    text_limit = int(request.get("text_limit", 200))
    layer_indices = list(layout.layer_indices())
    layer_indices.sort(
        key=lambda index: (
            layout.get_info(index).layer,
            layout.get_info(index).datatype,
            index,
        )
    )
    for layer_index in layer_indices:
        info = layout.get_info(layer_index)
        roles = list(role_by_layer.get((info.layer, info.datatype), []))
        counts = {
            "box": 0,
            "path": 0,
            "polygon": 0,
            "text": 0,
            "edge": 0,
            "point": 0,
            "other": 0,
        }
        iterator = layout.begin_shapes(top.cell_index(), layer_index)
        while not iterator.at_end():
            shape = iterator.shape()
            kind = _shape_kind(shape)
            counts[kind] += 1
            shape_totals[kind] = shape_totals.get(kind, 0) + 1
            if kind == "text" and len(text_records) < text_limit:
                transformed = shape.text.transformed(iterator.itrans())
                text_records.append(
                    {
                        "string": transformed.string,
                        "origin_um": _point_um(transformed.trans.disp, layout.dbu),
                        "layer": info.layer,
                        "datatype": info.datatype,
                        "mapped_roles": roles,
                    }
                )
            iterator.next()

        total_count = sum(counts.values())
        region = pya.Region(
            layout.begin_shapes(top.cell_index(), layer_index)
        ).merged()
        polygon_count = sum(1 for _ in region.each_merged())
        layer_records.append(
            {
                "layer_index": layer_index,
                "layer": info.layer,
                "datatype": info.datatype,
                "mapped_roles": roles,
                "used": total_count > 0,
                "recursive_shape_count": total_count,
                "shape_counts": counts,
                "polygon_count": polygon_count,
                "geometry_bbox_um": _optional_box_um(region.bbox(), layout.dbu),
                "geometry_area_um2": region.area() * layout.dbu * layout.dbu,
            }
        )
        if total_count > 0:
            if roles:
                mapped_roles_seen.update(roles)
            else:
                unmapped_used_layers.append(
                    {"layer": info.layer, "datatype": info.datatype}
                )

    cells = []
    for cell in layout.each_cell():
        direct_layers = []
        for layer_index in layer_indices:
            direct_counts = {}
            for shape in cell.each_shape(layer_index):
                kind = _shape_kind(shape)
                direct_counts[kind] = direct_counts.get(kind, 0) + 1
            if direct_counts:
                info = layout.get_info(layer_index)
                direct_layers.append(
                    {
                        "layer": info.layer,
                        "datatype": info.datatype,
                        "shape_counts": direct_counts,
                    }
                )
        child_names = sorted(layout.cell(index).name for index in cell.each_child_cell())
        cells.append(
            {
                "name": cell.name,
                "index": cell.cell_index(),
                "bbox_um": _optional_box_um(cell.bbox(), layout.dbu),
                "direct_instance_count": sum(1 for _ in cell.each_inst()),
                "child_cells": child_names,
                "direct_layers": direct_layers,
                "is_pcell_variant": bool(cell.is_pcell_variant()),
            }
        )
    cells.sort(key=lambda item: (item["name"], item["index"]))
    text_records.sort(
        key=lambda item: (
            item["layer"],
            item["datatype"],
            item["origin_um"][1],
            item["origin_um"][0],
            item["string"],
        )
    )

    mapped_roles = sorted(request["layermap"])
    return {
        "ok": True,
        "layout": {
            "path": layout_path,
            "format": os.path.splitext(layout_path)[1].lower().lstrip("."),
            "dbu_um": layout.dbu,
            "klayout_version": pya.Application.instance().version(),
            "top_cell": top.name,
            "top_cells": [cell.name for cell in layout.top_cells()],
            "top_bbox_um": _optional_box_um(top.bbox(), layout.dbu),
            "cell_count": layout.cells(),
        },
        "layers": layer_records,
        "shape_totals": shape_totals,
        "cells": cells,
        "texts": {
            "count": shape_totals.get("text", 0),
            "records": text_records,
            "truncated": shape_totals.get("text", 0) > len(text_records),
            "limit": text_limit,
        },
        "layermap_coverage": {
            "mapped_roles": mapped_roles,
            "mapped_roles_present": sorted(mapped_roles_seen),
            "mapped_roles_absent": sorted(set(mapped_roles) - mapped_roles_seen),
            "unmapped_used_layers": unmapped_used_layers,
            "role_inference_performed": False,
        },
        "layout_read_count": 1,
        "input_layout_modified": False,
    }


def _assemble_teg(request):
    padset_path = os.path.abspath(str(request["padset_path"]))
    output_gds_path = os.path.abspath(str(request["output_gds_path"]))
    teg_name = str(request.get("teg_name", "TEG_DUT_ARRAY_V1"))
    export_static = bool(request.get("export_static", True))
    dut_sweep = request.get("dut_sweep", [])
    layermap = request.get("layermap", {})

    if not request.get("conceptual_export_confirmed"):
        return _error(
            "CONCEPTUAL_EXPORT_REQUIRES_OPT_IN",
            "Synthetic DUT assembly requires explicit non-production acknowledgement.",
            {"production_ready": False},
            "Use the host assemble_teg tool with confirm_conceptual_export=true.",
        )
    if os.path.exists(output_gds_path):
        return _error(
            "OUTPUT_ALREADY_EXISTS",
            "Assembly output already exists and will not be overwritten.",
            {"output_gds_path": output_gds_path},
            "Choose a new output path.",
        )
    output_dir = os.path.dirname(output_gds_path) or os.getcwd()
    if not os.path.isdir(output_dir):
        return _error(
            "OUTPUT_DIRECTORY_NOT_FOUND",
            "Assembly output directory does not exist.",
            {"output_directory": output_dir},
            "Create the output directory before retrying.",
        )

    required_roles = {"m1", "active", "poly", "contact", "text"}
    missing_roles = sorted(required_roles.difference(layermap))
    if missing_roles:
        return _error(
            "ASSEMBLY_LAYERMAP_INCOMPLETE",
            "Every generated layer role must be explicit in the layermap.",
            {"missing_layer_roles": missing_roles},
            "Add explicit m1, active, poly, contact, and text layer/datatype pairs.",
        )

    layout = pya.Layout()
    try:
        layout.read(padset_path)
    except Exception as exc:
        return _error(
            "PADSET_READ_FAILED",
            "KLayout could not read the template padset.",
            {"padset_path": padset_path, "error": str(exc)},
        )

    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        return top_error

    dbu = layout.dbu

    # Resolve layers
    def get_layer_idx(role_key):
        role_info = layermap.get(role_key)
        l = int(role_info["layer"])
        dt = int(role_info["datatype"])
        return layout.layer(l, dt)

    l_m1 = get_layer_idx("m1")
    l_active = get_layer_idx("active")
    l_poly = get_layer_idx("poly")
    l_contact = get_layer_idx("contact")
    l_text = get_layer_idx("text")

    # 1. Inspect padset to get deterministic slot origins
    req_analyze = dict(request)
    req_analyze["layout_path"] = padset_path
    analysis = _integrated_analyze_padset(req_analyze)
    if not analysis.get("ok"):
        return analysis

    dut_slots = analysis["dut_slots"]
    if len(dut_sweep) != len(dut_slots):
        return _error(
            "SWEEP_COUNT_MISMATCH",
            f"Provided {len(dut_sweep)} sweep configs for {len(dut_slots)} slots.",
            {"sweep_count": len(dut_sweep), "slot_count": len(dut_slots)},
            "Provide exactly one validated parameter config for every DUT slot.",
        )

    # 2. Build canonical geometry once per unique parameter/window variant and
    # instantiate reusable cells at the deterministic slot origins.
    variant_cells = {}
    variant_expectations = {}
    created_cells = []
    variant_records = []
    site_variants = []
    for site_idx, (slot, sweep_item) in enumerate(zip(dut_slots, dut_sweep), start=1):
        site_num = int(slot["site"])
        params_dict = sweep_item.get("parameters", {})
        origin = slot["origin_um"]
        local_device_window = Box(
            float(slot["device_window_um"][0]) - float(origin[0]),
            float(slot["device_window_um"][1]) - float(origin[1]),
            float(slot["device_window_um"][2]) - float(origin[0]),
            float(slot["device_window_um"][3]) - float(origin[1]),
        )
        local_routing_boundary = Box(
            float(slot["routing_boundary_um"][0]) - float(origin[0]),
            float(slot["routing_boundary_um"][1]) - float(origin[1]),
            float(slot["routing_boundary_um"][2]) - float(origin[0]),
            float(slot["routing_boundary_um"][3]) - float(origin[1]),
        )
        canonical_values = {
            name: params_dict[name]
            for name in (
                "w_um",
                "l_um",
                "array_rows",
                "array_cols",
                "pitch_x_um",
                "pitch_y_um",
                "routed_device_count",
                "m1_width_um",
                "m1_overlap_um",
            )
            if name in params_dict
        }
        canonical_values["device_window_um"] = local_device_window
        canonical_values["routing_boundary_um"] = local_routing_boundary
        try:
            params = DutParameters(**canonical_values)
            geometry = build_dut_geometry(params)
        except AnalysisError as exc:
            return _error(
                "ASSEMBLY_GEOMETRY_INVALID",
                f"Site {site_num} canonical geometry generation failed: {exc.message}",
                {
                    "site": site_num,
                    "cause_code": exc.code,
                    "parameters": params_dict,
                },
                exc.next_action,
            )

        parameter_key = json.dumps(
            params.to_dict(), sort_keys=True, separators=(",", ":")
        )
        if parameter_key not in variant_cells:
            variant_id = f"VARIANT_{len(variant_cells) + 1:03d}"
            cell_name = f"DUT_{variant_id}"
            dut_cell = layout.create_cell(cell_name)
            for box in geometry.active_boxes_um:
                dut_cell.shapes(l_active).insert(pya.DBox(*box))
            for box in geometry.poly_boxes_um:
                dut_cell.shapes(l_poly).insert(pya.DBox(*box))
            for box in geometry.contact_boxes_um:
                dut_cell.shapes(l_contact).insert(pya.DBox(*box))
            for shape in geometry.m1_shapes_um:
                dut_cell.shapes(l_m1).insert(pya.DBox(*shape["bbox_um"]))

            connectivity = analyze_m1_connectivity(geometry.m1_shapes_um)
            variant_cells[parameter_key] = (variant_id, dut_cell)
            variant_expectations[cell_name] = {
                "active": list(geometry.active_boxes_um),
                "poly": list(geometry.poly_boxes_um),
                "contact": list(geometry.contact_boxes_um),
                "m1": [
                    list(shape["bbox_um"]) for shape in geometry.m1_shapes_um
                ],
            }
            created_cells.append(cell_name)
            variant_records.append(
                {
                    "variant_id": variant_id,
                    "cell_name": cell_name,
                    "parameters": params.to_dict(),
                    "routed_indices": list(geometry.routed_indices),
                    "shape_counts": geometry.to_dict()["shape_counts"],
                    "m1_connectivity": connectivity,
                }
            )
        else:
            variant_id, dut_cell = variant_cells[parameter_key]

        # Insert instance into top cell
        trans = pya.DTrans(pya.DPoint(origin[0], origin[1]))
        top.insert(pya.DCellInstArray(dut_cell.cell_index(), trans))
        site_variants.append(
            {
                "site": site_num,
                "variant_id": variant_id,
                "cell_name": dut_cell.name,
                "origin_um": [float(origin[0]), float(origin[1])],
            }
        )

    # 3. Add TEG Name Text (90-degree rotated at left edge)
    pads = analysis.get("pads", [])
    if pads:
        leftmost_pad = min(pads, key=lambda p: p["bbox_um"][0])
        label_x = leftmost_pad["bbox_um"][0] - 20.0
        label_y = leftmost_pad["center_um"][1]
        text_obj = pya.DText(
            teg_name,
            pya.DTrans(pya.DTrans.R90, label_x, label_y),
        )
        top.shapes(l_text).insert(text_obj)

    # 4. Export static layout if requested
    if export_static:
        top.flatten(-1, True)

    # 5. Write to a temporary sibling, verify, then promote without replacing
    # any pre-existing user artifact.
    temp_handle, temporary_output = tempfile.mkstemp(
        prefix=".klayout-assembly-",
        suffix=".gds",
        dir=output_dir,
    )
    os.close(temp_handle)
    os.unlink(temporary_output)
    try:
        layout.write(temporary_output)
    except Exception as exc:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        return _error(
            "GDS_WRITE_FAILED",
            f"Failed to write assembled GDS: {exc}",
            {"output_path": output_gds_path, "error": str(exc)},
        )

    # 6. Round-trip verification
    verify_layout = pya.Layout()
    verify_layout.read(temporary_output)
    verify_top = verify_layout.top_cell()
    verify_layers = sorted(
        {
            (verify_layout.get_info(index).layer, verify_layout.get_info(index).datatype)
            for index in verify_layout.layer_indices()
        }
    )
    expected_layers = sorted(
        {(int(layermap[role]["layer"]), int(layermap[role]["datatype"])) for role in required_roles}
    )
    missing_output_layers = sorted(set(expected_layers).difference(verify_layers))
    if missing_output_layers:
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Fresh reload is missing required generated layers.",
            {"missing_layers": missing_output_layers, "output_layers": verify_layers},
            "Inspect layer generation before exporting again.",
        )
    direct_instance_count = sum(1 for _ in verify_top.each_inst())
    if export_static and direct_instance_count != 0:
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Static export still contains top-level instances after fresh reload.",
            {"direct_instance_count": direct_instance_count},
            "Inspect flattening and static export logic.",
        )

    pcell_variant_names = [
        cell.name for cell in verify_layout.each_cell() if cell.is_pcell_variant()
    ]
    if pcell_variant_names:
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Fresh reload unexpectedly depends on PCell variants.",
            {"pcell_variant_names": sorted(pcell_variant_names)},
            "Export only ordinary geometry cells before delivery.",
        )

    variant_roundtrip = []
    if not export_static:
        verify_role_layers = {
            role: _find_layer(
                verify_layout,
                int(layermap[role]["layer"]),
                int(layermap[role]["datatype"]),
            )
            for role in ("active", "poly", "contact", "m1")
        }
        for cell_name, expected_by_role in sorted(variant_expectations.items()):
            verify_cell = verify_layout.cell(cell_name)
            if verify_cell is None:
                os.unlink(temporary_output)
                return _error(
                    "ASSEMBLY_ROUNDTRIP_INVALID",
                    "Fresh reload is missing a reusable DUT variant cell.",
                    {"missing_variant_cell": cell_name},
                    "Inspect hierarchy serialization before export.",
                )
            mismatched_roles = []
            for role, expected_boxes in expected_by_role.items():
                layer_index = verify_role_layers[role]
                actual_region = pya.Region(verify_cell.begin_shapes_rec(layer_index))
                expected_region = pya.Region()
                for box in expected_boxes:
                    expected_region.insert(
                        pya.DBox(*box).to_itype(verify_layout.dbu)
                    )
                if not (actual_region ^ expected_region).is_empty():
                    mismatched_roles.append(role)
            if mismatched_roles:
                os.unlink(temporary_output)
                return _error(
                    "ASSEMBLY_ROUNDTRIP_INVALID",
                    "Fresh reload changed canonical DUT variant geometry.",
                    {
                        "variant_cell": cell_name,
                        "mismatched_layer_roles": mismatched_roles,
                    },
                    "Inspect DBU conversion and GDS serialization before export.",
                )
            variant_roundtrip.append(
                {"cell_name": cell_name, "geometry_xor_clean": True}
            )

    verify_text_layer = _find_layer(
        verify_layout,
        int(layermap["text"]["layer"]),
        int(layermap["text"]["datatype"]),
    )
    verified_labels = []
    if verify_text_layer is not None:
        for shape in verify_top.each_shape(verify_text_layer):
            if shape.is_text() and shape.text.string == teg_name:
                verified_labels.append(shape.text)
    expected_text_rotation = 1
    if (
        len(verified_labels) != 1
        or int(verified_labels[0].trans.rot) != expected_text_rotation
        or bool(verified_labels[0].trans.is_mirror())
    ):
        actual_transforms = [
            {
                "rotation_quadrants": int(label.trans.rot),
                "mirrored": bool(label.trans.is_mirror()),
            }
            for label in verified_labels
        ]
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Fresh reload did not preserve exactly one unmirrored 90-degree TEG label.",
            {
                "teg_name": teg_name,
                "matching_label_count": len(verified_labels),
                "actual_transforms": actual_transforms,
            },
            "Inspect text construction and GDS serialization before exporting again.",
        )

    reservation = None
    try:
        reservation = os.open(
            output_gds_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
        os.close(reservation)
        reservation = None
        os.replace(temporary_output, output_gds_path)
    except FileExistsError:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        return _error(
            "OUTPUT_ALREADY_EXISTS",
            "Assembly output appeared during generation and was not overwritten.",
            {"output_gds_path": output_gds_path},
            "Choose a new output path.",
        )
    except Exception as exc:
        if reservation is not None:
            os.close(reservation)
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        if os.path.isfile(output_gds_path) and os.path.getsize(output_gds_path) == 0:
            os.unlink(output_gds_path)
        return _error(
            "ASSEMBLY_PROMOTION_FAILED",
            "Verified assembly could not be promoted to the requested output path.",
            {"output_gds_path": output_gds_path, "error": str(exc)},
            "Check output permissions and retry with a new path.",
        )

    unresolved_landings = analysis.get("m1_connectivity", {}).get(
        "unresolved_landings", []
    )

    return {
        "ok": True,
        "production_ready": False,
        "geometry_status": "conceptual_scaffold",
        "process_geometry_verified": False,
        "electrical_connectivity_verified": False,
        "known_terminal_state": "canonical_conceptual_geometry_with_reported_internal_opens",
        "output_gds_path": output_gds_path,
        "teg_name": teg_name,
        "export_static": export_static,
        "total_sites": len(dut_slots),
        "assembled_sites": len(dut_slots),
        "variant_count": len(variant_cells),
        "top_cell": verify_top.name,
        "dbu_um": verify_layout.dbu,
        "bbox_um": _box_um(verify_top.bbox(), verify_layout.dbu),
        "cell_count": verify_layout.cells(),
        "direct_instance_count": direct_instance_count,
        "layers": [
            {"layer": layer, "datatype": datatype}
            for layer, datatype in verify_layers
        ],
        "teg_label": {
            "string": teg_name,
            "rotation_degrees": 90,
            "mirrored": False,
            "roundtrip_verified": True,
        },
        "roundtrip_verified": True,
        "pcell_dependency_count": 0,
        "variant_roundtrip": variant_roundtrip,
        "input_layout_modified": False,
        "unresolved_padset_landings": unresolved_landings,
        "created_dut_cells": created_cells,
        "site_variants": site_variants,
        "variants": variant_records,
        "warning": (
            "Canonical synthetic geometry and reported internal opens are for visual testing only."
        ),
    }


request_file = _required_variable("request_path")
response_file = _required_variable("response_path")
try:
    with open(request_file, "r", encoding="utf-8") as handle:
        worker_request = json.load(handle)
    if worker_request.get("operation") == "analyze_padset_integrated":
        worker_result = _integrated_analyze_padset(worker_request)
    elif worker_request.get("operation") == "inspect_sample_layout":
        worker_result = _inspect_sample_layout(worker_request)
    elif worker_request.get("operation") == "assemble_teg":
        worker_result = _assemble_teg(worker_request)
    else:
        worker_result = _error(
            "UNKNOWN_OPERATION",
            "KLayout worker operation is not supported.",
            {"operation": worker_request.get("operation")},
        )

except Exception as exc:
    worker_result = _error(
        "KLAYOUT_WORKER_FAILED",
        "KLayout worker failed.",
        {"error_type": type(exc).__name__, "error": str(exc)},
        "Inspect the worker error and KLayout version compatibility.",
    )

with open(response_file, "w", encoding="utf-8") as handle:
    json.dump(worker_result, handle, indent=2, sort_keys=True)
    handle.write("\n")
