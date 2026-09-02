"""Deterministic geometry-style extraction inside KLayout's Python runtime."""

from __future__ import annotations

import os

import pya

from .worker_common import _optional_box_um, _select_top, _shape_kind
from .worker_protocol import worker_error as _error


def _rounded_um(value_dbu, dbu):
    return round(int(value_dbu) * dbu, 12)


def _ranked_histogram(counts, dbu, limit):
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"value_um": _rounded_um(value, dbu), "count": count}
        for value, count in ranked[:limit]
    ]


def _polygon_is_manhattan(polygon):
    for edge in polygon.each_edge():
        if edge.p1.x != edge.p2.x and edge.p1.y != edge.p2.y:
            return False
    return True


def _shape_polygon(shape, transform):
    if shape.is_box():
        return pya.Polygon(shape.box).transformed(transform)
    if shape.is_polygon():
        return shape.polygon.transformed(transform)
    if shape.is_path():
        return shape.path.polygon().transformed(transform)
    return None


def _layer_style(layout, top, layer_index, roles, histogram_limit):
    info = layout.get_info(layer_index)
    shape_counts = {
        "box": 0,
        "path": 0,
        "polygon": 0,
        "text": 0,
        "edge": 0,
        "point": 0,
        "other": 0,
    }
    orientation_counts = {"horizontal": 0, "vertical": 0, "square": 0}
    width_counts = {}
    height_counts = {}
    short_side_counts = {}
    long_side_counts = {}
    geometry_shape_count = 0
    non_manhattan_shape_count = 0

    iterator = layout.begin_shapes(top.cell_index(), layer_index)
    while not iterator.at_end():
        shape = iterator.shape()
        kind = _shape_kind(shape)
        shape_counts[kind] += 1
        polygon = _shape_polygon(shape, iterator.itrans())
        if polygon is not None:
            geometry_shape_count += 1
            if not _polygon_is_manhattan(polygon):
                non_manhattan_shape_count += 1
            if shape.is_box():
                bbox = polygon.bbox()
                width = bbox.width()
                height = bbox.height()
                width_counts[width] = width_counts.get(width, 0) + 1
                height_counts[height] = height_counts.get(height, 0) + 1
                short_side = min(width, height)
                long_side = max(width, height)
                short_side_counts[short_side] = short_side_counts.get(short_side, 0) + 1
                long_side_counts[long_side] = long_side_counts.get(long_side, 0) + 1
                if width == height:
                    orientation_counts["square"] += 1
                elif width > height:
                    orientation_counts["horizontal"] += 1
                else:
                    orientation_counts["vertical"] += 1
        iterator.next()

    region = pya.Region(top.begin_shapes_rec(layer_index)).merged()
    polygons = list(region.each())
    bbox = region.bbox()
    bbox_area_dbu2 = 0 if bbox.empty() else bbox.width() * bbox.height()
    area_dbu2 = region.area()
    return {
        "layer": info.layer,
        "datatype": info.datatype,
        "layer_token": "%d/%d" % (info.layer, info.datatype),
        "mapped_roles": list(roles),
        "recursive_shape_count": sum(shape_counts.values()),
        "geometry_shape_count": geometry_shape_count,
        "shape_counts": shape_counts,
        "orthogonal_geometry_verified": non_manhattan_shape_count == 0,
        "non_manhattan_shape_count": non_manhattan_shape_count,
        "box_orientation_counts": orientation_counts,
        "observed_box_dimensions": {
            "histogram_limit": histogram_limit,
            "widths_um": _ranked_histogram(width_counts, layout.dbu, histogram_limit),
            "heights_um": _ranked_histogram(height_counts, layout.dbu, histogram_limit),
            "short_sides_um": _ranked_histogram(short_side_counts, layout.dbu, histogram_limit),
            "long_sides_um": _ranked_histogram(long_side_counts, layout.dbu, histogram_limit),
            "values_are_observations_not_design_rules": True,
        },
        "merged_topology": {
            "component_count": len(polygons),
            "hole_count": sum(polygon.holes() for polygon in polygons),
            "bbox_um": _optional_box_um(bbox, layout.dbu),
            "area_um2": area_dbu2 * layout.dbu * layout.dbu,
            "bbox_fill_ratio": (
                0.0 if bbox_area_dbu2 == 0 else area_dbu2 / float(bbox_area_dbu2)
            ),
        },
    }


def extract_layout_style(request):
    """Extract reproducible drawing-style observations without semantic inference."""

    layout_path = os.path.abspath(str(request["layout_path"]))
    if not os.path.isfile(layout_path):
        return _error(
            "LAYOUT_NOT_FOUND",
            "Style source layout does not exist.",
            {"layout_path": layout_path},
            "Provide an existing GDS or OAS layout.",
        )
    try:
        layout = pya.Layout()
        layout.read(layout_path)
    except Exception as exc:
        return _error(
            "LAYOUT_READ_FAILED",
            "KLayout could not read the style source layout.",
            {"layout_path": layout_path, "error": str(exc)},
            "Check the stream format and file integrity.",
        )

    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        return top_error
    histogram_limit = int(request.get("histogram_limit", 24))
    role_by_pair = {}
    for role, spec in request.get("layermap", {}).items():
        role_by_pair.setdefault((int(spec["layer"]), int(spec["datatype"])), []).append(role)
    for roles in role_by_pair.values():
        roles.sort()

    layer_indices = sorted(
        layout.layer_indices(),
        key=lambda index: (
            layout.get_info(index).layer,
            layout.get_info(index).datatype,
            index,
        ),
    )
    layer_styles = []
    for layer_index in layer_indices:
        info = layout.get_info(layer_index)
        style = _layer_style(
            layout,
            top,
            layer_index,
            role_by_pair.get((info.layer, info.datatype), []),
            histogram_limit,
        )
        if style["recursive_shape_count"]:
            layer_styles.append(style)

    child_counts = {}
    rotation_counts = {"0": 0, "90": 0, "180": 0, "270": 0}
    mirrored_instance_count = 0
    for instance in top.each_inst():
        child = layout.cell(instance.cell_index).name
        child_counts[child] = child_counts.get(child, 0) + 1
        rotation = str(int(instance.trans.rot) * 90)
        rotation_counts[rotation] = rotation_counts.get(rotation, 0) + 1
        if instance.trans.is_mirror():
            mirrored_instance_count += 1

    reused_cells = [
        {"cell": name, "direct_top_instance_count": count}
        for name, count in sorted(child_counts.items(), key=lambda item: (-item[1], item[0]))
        if count > 1
    ]
    text_strings = []
    for layer_index in layer_indices:
        iterator = layout.begin_shapes(top.cell_index(), layer_index)
        while not iterator.at_end():
            shape = iterator.shape()
            if shape.is_text():
                text_strings.append(shape.text.string)
            iterator.next()
    text_strings.sort()

    return {
        "ok": True,
        "style_profile": {
            "schema_version": 1,
            "kind": "ExtractedLayoutStyleProfile",
            "layout": {
                "format": os.path.splitext(layout_path)[1].lower().lstrip("."),
                "klayout_version": pya.Application.instance().version(),
                "dbu_um": layout.dbu,
                "top_cell": top.name,
                "top_cells": sorted(cell.name for cell in layout.top_cells()),
                "top_bbox_um": _optional_box_um(top.bbox(), layout.dbu),
                "cell_count": layout.cells(),
            },
            "hierarchy_style": {
                "top_direct_instance_count": sum(child_counts.values()),
                "top_child_instance_counts": [
                    {"cell": name, "count": count}
                    for name, count in sorted(child_counts.items())
                ],
                "reused_top_child_cells": reused_cells,
                "rotation_counts": rotation_counts,
                "mirrored_instance_count": mirrored_instance_count,
                "flattening_performed": False,
            },
            "layer_styles": layer_styles,
            "label_style": {
                "recursive_text_count": len(text_strings),
                "unique_text_count": len(set(text_strings)),
                "sample_strings": sorted(set(text_strings))[:50],
                "sample_limit": 50,
            },
            "inference_boundaries": {
                "semantic_layer_roles_only_from_supplied_layermap": True,
                "role_inference_performed": False,
                "net_or_terminal_inference_performed": False,
                "design_rule_inference_performed": False,
                "electrical_performance_inference_performed": False,
                "style_values_are_observed_geometry_not_generation_constraints": True,
            },
            "production_ready": False,
        },
    }
