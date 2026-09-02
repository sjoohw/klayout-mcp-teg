"""KLayout worker handler for the fixed 25-Pad analysis profile."""

import os

import pya

from .errors import AnalysisError
from .padset import PadDetectionConfig, analyze_pad_boxes
from .profiles import DEFAULT_TEG_PROFILE
from .worker_common import (
    _box_um,
    _component_records,
    _find_layer,
    _landing_record,
    _polygon_um,
    _select_top,
)
from .worker_overlay import render_boundary_overlay as _render_boundary_overlay
from .worker_protocol import worker_error as _error

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


def analyze_padset(request):
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
