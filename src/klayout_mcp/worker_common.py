"""Shared KLayout worker geometry and hierarchy helpers."""

import pya

from .worker_protocol import worker_error as _error

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
