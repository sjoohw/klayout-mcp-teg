"""Emit direct-cell and recursive-top JSON inventory under KLayout's pya runtime."""

from __future__ import annotations

import hashlib
import json
import os

import pya


def required_script_variable(name):
    value = globals().get(name)
    if not value:
        raise RuntimeError(f"Pass -rd {name}=<value>")
    return str(value)


def box_dict(box, dbu):
    if box is None or box.empty():
        return None
    return {
        "dbu": [box.left, box.bottom, box.right, box.top],
        "microns": [
            box.left * dbu,
            box.bottom * dbu,
            box.right * dbu,
            box.top * dbu,
        ],
    }


def shape_kind(shape):
    for predicate, name in (
        ("is_box", "box"),
        ("is_path", "path"),
        ("is_polygon", "polygon"),
        ("is_text", "text"),
        ("is_edge", "edge"),
        ("is_point", "point"),
    ):
        method = getattr(shape, predicate, None)
        if method is not None and method():
            return name
    return "other"


def recursive_layer_inventory(cell, layer_index, dbu):
    region = pya.Region(cell.begin_shapes_rec(layer_index))
    region.merge()
    polygons = list(region.each())
    canonical = []
    holes = 0
    for polygon in polygons:
        holes += polygon.holes()
        canonical.append(str(polygon))
    canonical.sort()
    fingerprint = hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()
    return {
        "bbox": box_dict(region.bbox(), dbu),
        "area_dbu2": region.area(),
        "area_um2": region.area() * dbu * dbu,
        "connected_components": len(polygons),
        "holes": holes,
        "merged_region_sha256": fingerprint,
    }


layout_path = os.path.abspath(required_script_variable("layout_path"))
report_path = globals().get("report_path")

layout = pya.Layout()
layout.read(layout_path)
layer_indices = list(layout.layer_indices())
layers = [
    {
        "layer_index": index,
        "layer": layout.get_info(index).layer,
        "datatype": layout.get_info(index).datatype,
    }
    for index in layer_indices
]

cells = []
totals = {}
for cell in layout.each_cell():
    per_layer = []
    for layer_index in layer_indices:
        counts = {}
        for shape in cell.each_shape(layer_index):
            kind = shape_kind(shape)
            counts[kind] = counts.get(kind, 0) + 1
            totals[kind] = totals.get(kind, 0) + 1
        if counts:
            info = layout.get_info(layer_index)
            per_layer.append(
                {"layer": info.layer, "datatype": info.datatype, "counts": counts}
            )
    cells.append(
        {
            "name": cell.name,
            "index": cell.cell_index(),
            "bbox": box_dict(cell.bbox(), layout.dbu),
            "instances": sum(1 for _ in cell.each_inst()),
            "layers": per_layer,
            "is_pcell_variant": bool(cell.is_pcell_variant()),
        }
    )

top_cells = []
for top in layout.top_cells():
    recursive_layers = []
    for layer_index in layer_indices:
        inventory = recursive_layer_inventory(top, layer_index, layout.dbu)
        if inventory["bbox"] is not None:
            info = layout.get_info(layer_index)
            recursive_layers.append(
                {"layer": info.layer, "datatype": info.datatype, **inventory}
            )
    top_cells.append(
        {"name": top.name, "bbox": box_dict(top.bbox(), layout.dbu), "layers": recursive_layers}
    )

report = {
    "path": layout_path,
    "klayout_version": pya.Application.instance().version(),
    "dbu": layout.dbu,
    "top_cells": top_cells,
    "cell_count": layout.cells(),
    "layers": layers,
    "direct_shape_totals": totals,
    "cells": cells,
}

payload = json.dumps(report, indent=2, sort_keys=True)
print(payload)
if report_path:
    with open(os.path.abspath(str(report_path)), "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
