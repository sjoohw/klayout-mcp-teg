"""KLayout hidden-view boundary-overlay operation."""

from __future__ import annotations

import os

import pya

from .worker_protocol import worker_error


def _dpolygon(record):
    polygon = pya.DPolygon(
        [pya.DPoint(float(x), float(y)) for x, y in record["hull"]]
    )
    for hole in record.get("holes", []):
        polygon.insert_hole(
            [pya.DPoint(float(x), float(y)) for x, y in hole]
        )
    return polygon


def _add_marker(view, geometry, color, marker_counts, kind, filled=False):
    marker = pya.Marker()
    marker.set(geometry)
    marker.color = color
    marker.frame_color = color
    marker.line_width = 1
    marker.dither_pattern = 0 if filled else -1
    marker.halo = 1
    view.add_marker(marker)
    marker_counts[kind] = marker_counts.get(kind, 0) + 1


def render_boundary_overlay(request, existing_layout):
    """Render analyzed pad, slot, and terminal landing markers to a new PNG."""

    layout_path = os.path.abspath(str(request["layout_path"]))
    image_path = os.path.abspath(str(request["image_path"]))
    if not os.path.isfile(layout_path):
        return worker_error(
            "PADSET_NOT_FOUND",
            "Padset layout does not exist.",
            {"padset_path": layout_path},
            "Provide an existing GDS or OAS path.",
        )
    if os.path.splitext(image_path)[1].lower() != ".png":
        return worker_error(
            "INVALID_OVERLAY_PATH",
            "Boundary overlay output must use a .png extension.",
            {"image_path": image_path},
            "Provide a new PNG output path.",
        )
    if os.path.normcase(image_path) == os.path.normcase(layout_path):
        return worker_error(
            "INPUT_OVERWRITE_FORBIDDEN",
            "Boundary overlay output cannot overwrite the padset input.",
            {"padset_path": layout_path, "image_path": image_path},
            "Provide a separate PNG output path.",
        )
    if os.path.exists(image_path):
        return worker_error(
            "OUTPUT_EXISTS",
            "Boundary overlay output already exists.",
            {"image_path": image_path},
            "Provide a new PNG output path. Existing artifacts are not overwritten.",
        )
    parent = os.path.dirname(image_path)
    if not os.path.isdir(parent):
        return worker_error(
            "OUTPUT_DIRECTORY_NOT_FOUND",
            "Boundary overlay output directory does not exist.",
            {"output_directory": parent},
            "Create the output directory or provide an existing directory.",
        )

    width = int(request["image_width"])
    height = int(request["image_height"])
    if width < 200 or height < 200 or width > 4096 or height > 4096:
        return worker_error(
            "INVALID_IMAGE_SIZE",
            "Boundary overlay image dimensions must be between 200 and 4096 pixels.",
            {"image_width": width, "image_height": height},
            "Provide image dimensions from 200 to 4096 pixels.",
        )

    main_window = pya.Application.instance().main_window()
    if main_window is None:
        return worker_error(
            "LAYOUT_VIEW_UNAVAILABLE",
            "KLayout did not provide a hidden layout window.",
            next_action="Run the worker in hidden-view mode.",
        )
    view = main_window.current_view()
    if view is None:
        main_window.create_view()
        view = main_window.current_view()
    if view is None:
        return worker_error(
            "LAYOUT_VIEW_UNAVAILABLE",
            "KLayout did not create a layout view.",
            next_action="Check hidden-view KLayout support.",
        )
    cellview_index = view.show_layout(existing_layout, False)
    view.active_cellview_index = cellview_index
    cellview = view.cellview(cellview_index)
    if cellview is None:
        return worker_error(
            "LAYOUT_VIEW_UNAVAILABLE",
            "KLayout did not create a cell view.",
            next_action="Check hidden-view KLayout support.",
        )
    cellview.cell_name = str(request["top_cell"])
    if not cellview.is_valid():
        return worker_error(
            "TOP_CELL_NOT_FOUND",
            "Requested top cell does not exist in the rendered padset.",
            {"requested_top_cell": request["top_cell"]},
            "Re-run padset analysis before rendering the overlay.",
        )
    view.add_missing_layers()
    view.max_hier()

    colors = {
        "pad": 0x3B82F6,
        "slot": 0xF59E0B,
        "resolved": 0x22C55E,
        "unresolved": 0xEF4444,
        "label": 0x111827,
    }
    marker_counts = {}
    for pad in request["pads"]:
        x1, y1, x2, y2 = [float(value) for value in pad["bbox_um"]]
        _add_marker(
            view,
            pya.DBox(x1, y1, x2, y2),
            colors["pad"],
            marker_counts,
            "pads",
        )
        _add_marker(
            view,
            pya.DText("P%d" % int(pad["number"]), pya.DTrans(x1, y2)),
            colors["label"],
            marker_counts,
            "labels",
        )

    for slot in request["dut_slots"]:
        site = int(slot["site"])
        x1, y1, x2, y2 = [float(value) for value in slot["routing_boundary_um"]]
        _add_marker(
            view,
            pya.DBox(x1, y1, x2, y2),
            colors["slot"],
            marker_counts,
            "slots",
        )
        _add_marker(
            view,
            pya.DText("S%d" % site, pya.DTrans(x1, y1)),
            colors["label"],
            marker_counts,
            "labels",
        )
        for role in ("source", "drain", "gate", "body"):
            landing = slot["landings"][role]
            if landing["status"] == "resolved":
                for polygon in landing["polygons_um"]:
                    _add_marker(
                        view,
                        _dpolygon(polygon),
                        colors["resolved"],
                        marker_counts,
                        "resolved_landings",
                        filled=True,
                    )
            else:
                bx1, by1, bx2, by2 = [
                    float(value) for value in landing["search_band_um"]
                ]
                _add_marker(
                    view,
                    pya.DBox(bx1, by1, bx2, by2),
                    colors["unresolved"],
                    marker_counts,
                    "unresolved_landings",
                )

    view.zoom_fit()
    temporary_path = image_path + ".tmp.png"
    try:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
        view.save_image(temporary_path, width, height)
        if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
            return worker_error(
                "OVERLAY_RENDER_FAILED",
                "KLayout did not create a non-empty boundary overlay image.",
                {"image_path": image_path},
                "Inspect hidden-view rendering support.",
            )
        os.replace(temporary_path, image_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

    return {
        "ok": True,
        "image_path": image_path,
        "image_width": width,
        "image_height": height,
        "top_cell": str(request["top_cell"]),
        "marker_counts": marker_counts,
        "legend": colors,
        "input_layout_modified": False,
    }
