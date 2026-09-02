"""Domain-neutral and sample-DUT layout inventory worker handler."""

import os

import pya

from .worker_common import _optional_box_um, _point_um, _select_top, _shape_kind
from .worker_protocol import worker_error as _error

def inspect_layout(request):
    """Inventory a sample DUT without assigning unprovided process meaning."""

    generic_inventory = request.get("operation") == "inspect_layout"
    not_found_code = "LAYOUT_NOT_FOUND" if generic_inventory else "SAMPLE_LAYOUT_NOT_FOUND"
    read_failed_code = (
        "LAYOUT_READ_FAILED" if generic_inventory else "SAMPLE_LAYOUT_READ_FAILED"
    )
    layout_label = "Layout" if generic_inventory else "Sample layout"
    layout_path = os.path.abspath(str(request["layout_path"]))
    if not os.path.isfile(layout_path):
        return _error(
            not_found_code,
            f"{layout_label} does not exist.",
            {"layout_path": layout_path},
            "Provide an existing GDS or OAS path.",
        )

    layout = pya.Layout()
    try:
        layout.read(layout_path)
    except Exception as exc:
        return _error(
            read_failed_code,
            f"KLayout could not read the {layout_label.lower()}.",
            {"layout_path": layout_path, "error": str(exc)},
            "Check the layout format and file integrity.",
        )

    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        if top_error.get("code") == "TOP_CELL_AMBIGUOUS":
            top_error["next_action"] = (
                "Provide top_cell explicitly."
                if generic_inventory
                else "Provide the sample DUT top_cell explicitly."
            )
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
