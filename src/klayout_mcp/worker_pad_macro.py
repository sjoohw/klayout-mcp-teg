"""KLayout-side immutable pad-macro geometry inspection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

import pya

from .file_publication import (
    OutputAlreadyExistsError,
    publication_staging_prefix,
    publish_new_file,
)
from .worker_protocol import worker_error


def _points(polygon):
    return [[point.x, point.y] for point in polygon.each_point_hull()]


def _recursive_cell_count(layout, root_cell):
    pending = [root_cell.cell_index()]
    seen = set()
    while pending:
        index = pending.pop()
        if index in seen:
            continue
        seen.add(index)
        pending.extend(layout.cell(index).each_child_cell())
    return len(seen)


def _geometry_fingerprint(layout, cell):
    fingerprint_payload = []
    for info in sorted(layout.layer_infos(), key=lambda item: (item.layer, item.datatype)):
        index = layout.find_layer(info.layer, info.datatype)
        flattened = pya.Region(cell.begin_shapes_rec(index)).merged()
        polygons = [
            {"hull": _points(polygon), "area_dbu2": polygon.area()}
            for polygon in flattened.each()
        ]
        if polygons:
            fingerprint_payload.append(
                {"layer": info.layer, "datatype": info.datatype, "polygons": polygons}
            )
    return hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def inspect_pad_macro(request):
    layout_path = os.path.abspath(str(request.get("layout_path", "")))
    if not os.path.isfile(layout_path):
        return worker_error(
            "PAD_MACRO_NOT_FOUND",
            "Pad macro GDS/OAS does not exist.",
            {"field": "layout_path", "value": layout_path, "stage": "pad_macro_intake"},
            "Provide an existing immutable pad macro stream file.",
        )
    layout = pya.Layout()
    layout.read(layout_path)
    top_name = request.get("top_cell")
    if top_name:
        cell = layout.cell(str(top_name))
        if cell is None:
            return worker_error(
                "PAD_MACRO_CELL_NOT_FOUND",
                "The requested pad macro cell is not present in the stream.",
                {"field": "top_cell", "value": top_name, "stage": "pad_macro_intake"},
                "Choose one of the cells reported by layout inspection.",
            )
    else:
        tops = list(layout.top_cells())
        if len(tops) != 1:
            return worker_error(
                "PAD_MACRO_TOP_CELL_AMBIGUOUS",
                "Pad macro stream must have one top cell or an explicit top_cell selection.",
                {
                    "field": "top_cell",
                    "received": None,
                    "candidates": sorted(candidate.name for candidate in tops),
                    "stage": "pad_macro_intake",
                },
                "Select the exact pad macro cell name.",
            )
        cell = tops[0]
    bbox = cell.bbox()
    dbu = float(layout.dbu)
    if bbox.empty():
        return worker_error(
            "PAD_MACRO_EMPTY",
            "The selected pad macro cell has no recursive geometry.",
            {"field": "top_cell", "value": cell.name, "stage": "pad_macro_intake"},
            "Select a cell containing the complete pad geometry hierarchy.",
        )
    access = request.get("access_layer", {})
    try:
        layer_number = int(access["layer"])
        datatype = int(access["datatype"])
    except (KeyError, TypeError, ValueError):
        return worker_error(
            "PAD_MACRO_ACCESS_LAYER_INVALID",
            "access_layer requires integer layer and datatype.",
            {"field": "access_layer", "received": access, "stage": "pad_macro_intake"},
            "Provide the exact probe/routing access metal layer and datatype.",
        )
    layer_index = layout.find_layer(layer_number, datatype)
    if layer_index is None:
        return worker_error(
            "PAD_MACRO_ACCESS_LAYER_MISSING",
            "The selected pad macro has no declared access-metal layer.",
            {
                "field": "access_layer",
                "received": access,
                "top_cell": cell.name,
                "stage": "pad_macro_intake",
            },
            "Correct the access layer mapping or provide the complete pad macro stream.",
        )
    region = pya.Region(cell.begin_shapes_rec(layer_index)).merged()
    tolerance_dbu = max(0, int(round(float(request.get("edge_tolerance_um", 0.001)) / dbu)))
    landings = []
    for polygon_index, polygon in enumerate(region.each()):
        shape_bbox = polygon.bbox()
        candidates = (
            ("left", bbox.left - shape_bbox.left, max(bbox.bottom, shape_bbox.bottom), min(bbox.top, shape_bbox.top)),
            ("right", shape_bbox.right - bbox.right, max(bbox.bottom, shape_bbox.bottom), min(bbox.top, shape_bbox.top)),
            ("bottom", bbox.bottom - shape_bbox.bottom, max(bbox.left, shape_bbox.left), min(bbox.right, shape_bbox.right)),
            ("top", shape_bbox.top - bbox.top, max(bbox.left, shape_bbox.left), min(bbox.right, shape_bbox.right)),
        )
        for edge, signed_distance, start, stop in candidates:
            # A negative signed distance means the access shape is inset from
            # the macro boundary.  Treating every negative value as eligible
            # falsely advertised all four edges for an internal-only metal.
            if abs(signed_distance) <= tolerance_dbu and stop > start:
                horizontal = edge in {"bottom", "top"}
                coordinate = (
                    bbox.bottom if edge == "bottom" else bbox.top if edge == "top" else bbox.left if edge == "left" else bbox.right
                )
                landings.append(
                    {
                        "landing_id": f"access-{polygon_index}-{edge}",
                        "edge": edge,
                        "segment_um": (
                            [[start * dbu, coordinate * dbu], [stop * dbu, coordinate * dbu]]
                            if horizontal
                            else [[coordinate * dbu, start * dbu], [coordinate * dbu, stop * dbu]]
                        ),
                        "shape_bbox_um": [
                            shape_bbox.left * dbu,
                            shape_bbox.bottom * dbu,
                            shape_bbox.right * dbu,
                            shape_bbox.top * dbu,
                        ],
                    }
                )
    recursive_fingerprint = _geometry_fingerprint(layout, cell)
    return {
        "ok": True,
        "top_cell": cell.name,
        "dbu_um": dbu,
        "bbox_um": [bbox.left * dbu, bbox.bottom * dbu, bbox.right * dbu, bbox.top * dbu],
        "width_um": bbox.width() * dbu,
        "height_um": bbox.height() * dbu,
        "access_layer": {"layer": layer_number, "datatype": datatype},
        "eligible_edge_landings": landings,
        "recursive_geometry_fingerprint_sha256": recursive_fingerprint,
        "hierarchy_cell_count": _recursive_cell_count(layout, cell),
        "geometry_preservation_mode": "source_stream_and_recursive_hierarchy_immutable",
    }


def compose_pad_macro_overlay(request):
    """Place immutable pad-cell instances and add only separate DUT/routing cells."""

    source_path = os.path.abspath(str(request.get("source_layout_path", "")))
    output_path = os.path.abspath(str(request.get("output_path", "")))
    if not os.path.isfile(source_path):
        return worker_error(
            "PAD_MACRO_PACKAGE_SOURCE_MISSING",
            "The preserved pad macro source stream is missing.",
            {"field": "source_layout_path", "value": source_path, "stage": "pad_macro_compose"},
            "Restore the exact content-addressed pad macro package.",
        )
    if os.path.exists(output_path):
        return worker_error(
            "OUTPUT_ALREADY_EXISTS",
            "Pad macro overlay composition requires a new output path.",
            {"field": "output_path", "value": output_path, "stage": "pad_macro_compose"},
            "Choose a new GDS/OAS output path.",
        )
    parent = os.path.dirname(output_path)
    if not os.path.isdir(parent):
        return worker_error(
            "OUTPUT_DIRECTORY_NOT_FOUND",
            "Pad macro overlay output directory does not exist.",
            {"field": "output_path", "value": output_path, "stage": "pad_macro_compose"},
            "Create the output directory before generation.",
        )
    layout = pya.Layout()
    layout.read(source_path)
    source_cell = layout.cell(str(request.get("source_cell", "")))
    if source_cell is None:
        return worker_error(
            "PAD_MACRO_CELL_NOT_FOUND",
            "The preserved pad source cell is not present in the package stream.",
            {"field": "source_cell", "value": request.get("source_cell"), "stage": "pad_macro_compose"},
            "Restore or re-register the exact pad macro package.",
        )
    expected_fingerprint = str(request.get("recursive_source_cell_fingerprint_sha256", ""))
    before_fingerprint = _geometry_fingerprint(layout, source_cell)
    if before_fingerprint != expected_fingerprint:
        return worker_error(
            "PAD_MACRO_SOURCE_FINGERPRINT_MISMATCH",
            "The preserved pad cell geometry differs from its immutable artifact.",
            {"expected": expected_fingerprint, "received": before_fingerprint, "stage": "pad_macro_compose"},
            "Restore the exact content-addressed pad macro package.",
        )
    top_name = str(request.get("output_top_cell", "TEG_PAD_MACRO_OVERLAY"))
    if layout.cell(top_name) is not None:
        return worker_error(
            "PAD_MACRO_OUTPUT_CELL_CONFLICT",
            "The requested output top cell already exists in the pad source stream.",
            {"field": "output_top_cell", "value": top_name, "stage": "pad_macro_compose"},
            "Choose a new output top-cell name without renaming source cells.",
        )
    top = layout.create_cell(top_name)
    dut_cell = layout.create_cell(top_name + "__DUT")
    routing_cell = layout.create_cell(top_name + "__ROUTING")
    rotations = {0: 0, 90: 1, 180: 2, 270: 3}
    for instance in request.get("instances", []):
        transform = pya.Trans(
            rotations[int(instance["rotation_deg"])],
            bool(instance["mirror_x"]),
            int(round(float(instance["x_um"]) / layout.dbu)),
            int(round(float(instance["y_um"]) / layout.dbu)),
        )
        top.insert(pya.CellInstArray(source_cell.cell_index(), transform))
    operation_counts = {"dut": 0, "routing": 0}
    for operation in request.get("operations", []):
        category = operation.get("category")
        if category not in operation_counts or operation.get("type") != "add_box":
            return worker_error(
                "PAD_MACRO_COMPOSE_OPERATION_FORBIDDEN",
                "Overlay composition accepts only DUT or routing add_box operations.",
                {"field": "operations", "received": operation, "stage": "pad_macro_compose"},
                "Remove pad-edit operations and provide only separate DUT/routing geometry.",
            )
        layer = operation.get("layer", {})
        layer_index = layout.layer(int(layer["layer"]), int(layer["datatype"]))
        bbox = [int(round(float(value) / layout.dbu)) for value in operation["bbox_um"]]
        target = dut_cell if category == "dut" else routing_cell
        target.shapes(layer_index).insert(pya.Box(*bbox))
        operation_counts[category] += 1
    top.insert(pya.CellInstArray(dut_cell.cell_index(), pya.Trans()))
    top.insert(pya.CellInstArray(routing_cell.cell_index(), pya.Trans()))
    after_fingerprint = _geometry_fingerprint(layout, source_cell)
    if after_fingerprint != before_fingerprint:
        return worker_error(
            "PAD_MACRO_SOURCE_MUTATED",
            "Pad source geometry changed during overlay composition.",
            {"expected": before_fingerprint, "received": after_fingerprint, "stage": "pad_macro_compose"},
            "Reject this output and inspect the composer implementation.",
        )
    suffix = os.path.splitext(output_path)[1]
    descriptor, temporary = tempfile.mkstemp(
        prefix=publication_staging_prefix("pad-overlay"), suffix=suffix, dir=parent
    )
    os.close(descriptor)
    os.unlink(temporary)
    try:
        layout.write(temporary)
        verify = pya.Layout()
        try:
            verify.read(temporary)
            verify_source = verify.cell(source_cell.name)
            verify_top = verify.cell(top_name)
            fresh_fingerprint = (
                None
                if verify_source is None
                else _geometry_fingerprint(verify, verify_source)
            )
        except Exception as exc:
            return worker_error(
                "PAD_MACRO_FRESH_RELOAD_VERIFICATION_FAILED",
                "The staged pad overlay could not be fresh-reloaded.",
                {
                    "error_type": type(exc).__name__,
                    "stage": "pad_macro_compose",
                    "final_output_published": False,
                },
                "Inspect stream serialization compatibility before publishing a new output.",
            )
        if verify_top is None or fresh_fingerprint != before_fingerprint:
            return worker_error(
                "PAD_MACRO_FRESH_RELOAD_VERIFICATION_FAILED",
                "Fresh reload did not preserve the source pad cell geometry.",
                {
                    "expected": before_fingerprint,
                    "received": fresh_fingerprint,
                    "stage": "pad_macro_compose",
                    "final_output_published": False,
                },
                "Reject the staged output and inspect stream serialization compatibility.",
            )
        try:
            publish_new_file(temporary, output_path)
        except OutputAlreadyExistsError:
            return worker_error(
                "OUTPUT_ALREADY_EXISTS",
                "Another writer published the pad macro overlay output first.",
                {"field": "output_path", "value": output_path, "stage": "pad_macro_compose"},
                "Use the winning immutable artifact or choose a new output path.",
            )
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "ok": True,
        "output_path": output_path,
        "output_top_cell": top_name,
        "pad_instance_count": len(request.get("instances", [])),
        "operation_counts": operation_counts,
        "source_pad_fingerprint_before": before_fingerprint,
        "source_pad_fingerprint_after_fresh_reload": fresh_fingerprint,
        "source_pad_geometry_preserved": True,
        "pad_geometry_added_or_modified": False,
        "fresh_reload_verified": True,
    }
