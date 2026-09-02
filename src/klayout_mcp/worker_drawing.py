"""KLayout worker handler for atomic general-purpose Manhattan drawings."""

from __future__ import annotations

import os
import tempfile

import pya

from .worker_protocol import worker_error


def _layer_pairs(layout):
    return sorted(
        (layout.get_info(index).layer, layout.get_info(index).datatype)
        for index in layout.layer_indices()
    )


def _box_um(box, dbu):
    if box is None or box.empty():
        return None
    return [box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu]


def _direct_region(cell, layer_index):
    region = pya.Region()
    for shape in cell.each_shape(layer_index):
        if shape.is_box():
            region.insert(shape.box)
        elif shape.is_polygon():
            region.insert(shape.polygon)
        elif shape.is_path():
            region.insert(shape.path.polygon())
    return region.merged()


def _direct_texts(cell, layer_index):
    records = []
    for shape in cell.each_shape(layer_index):
        if shape.is_text():
            text = shape.text
            records.append((text.string, text.trans.disp.x, text.trans.disp.y))
    return sorted(records)


def _direct_instances(layout, cell):
    records = []
    for instance in cell.each_inst():
        trans = instance.trans
        records.append(
            (
                layout.cell(instance.cell_index).name,
                int(trans.rot) * 90,
                bool(trans.is_mirror()),
                int(trans.disp.x),
                int(trans.disp.y),
            )
        )
    return sorted(records)


def draw_manhattan_layout(request):
    """Generate one new layout from an already normalized integer-DBU plan."""

    output_path = os.path.abspath(str(request["output_layout_path"]))
    if os.path.exists(output_path):
        return worker_error(
            "OUTPUT_ALREADY_EXISTS",
            "Drawing output already exists and will not be overwritten.",
            {"output_layout_path": output_path},
            "Choose a new output path.",
        )

    layout = pya.Layout()
    layout.dbu = float(request["dbu_um"])
    layers = {
        name: layout.layer(int(spec["layer"]), int(spec["datatype"]))
        for name, spec in sorted(request["layers"].items())
    }
    cells = {name: layout.create_cell(name) for name in request["cells"]}

    rotations = {0: 0, 90: 1, 180: 2, 270: 3}
    operation_counts = {name: 0 for name in ("add_box", "add_text", "add_instance", "boolean")}
    for operation in request["operations"]:
        op_type = operation["type"]
        operation_counts[op_type] += 1
        if op_type == "add_box":
            cells[operation["cell"]].shapes(layers[operation["layer"]]).insert(
                pya.Box(*[int(value) for value in operation["bbox_dbu"]])
            )
        elif op_type == "add_text":
            x, y = [int(value) for value in operation["origin_dbu"]]
            cells[operation["cell"]].shapes(layers[operation["layer"]]).insert(
                pya.Text(str(operation["text"]), pya.Trans(x, y))
            )
        elif op_type == "add_instance":
            x, y = [int(value) for value in operation["origin_dbu"]]
            transform = pya.Trans(
                rotations[int(operation["rotation_deg"])],
                bool(operation["mirror_x"]),
                x,
                y,
            )
            cells[operation["parent_cell"]].insert(
                pya.CellInstArray(cells[operation["child_cell"]].cell_index(), transform)
            )
        elif op_type == "boolean":
            cell = cells[operation["cell"]]
            first = _direct_region(cell, layers[operation["input_layers"][0]])
            second = _direct_region(cell, layers[operation["input_layers"][1]])
            boolean_op = operation["operation"]
            if boolean_op == "or":
                result = first | second
            elif boolean_op == "and":
                result = first & second
            elif boolean_op == "xor":
                result = first ^ second
            else:
                result = first - second
            output_shapes = cell.shapes(layers[operation["output_layer"]])
            if operation["clear_output"]:
                output_shapes.clear()
            output_shapes.insert(result.merged())

    expected_regions = {}
    expected_texts = {}
    expected_instances = {}
    for cell_name, cell in cells.items():
        expected_instances[cell_name] = _direct_instances(layout, cell)
        for layer_name, layer_index in layers.items():
            expected_regions[(cell_name, layer_name)] = _direct_region(cell, layer_index)
            expected_texts[(cell_name, layer_name)] = _direct_texts(cell, layer_index)

    handle, temporary_output = tempfile.mkstemp(
        prefix=".manhattan-drawing-",
        suffix=os.path.splitext(output_path)[1],
        dir=os.path.dirname(output_path),
    )
    os.close(handle)
    os.unlink(temporary_output)
    try:
        layout.write(temporary_output)
        verify_layout = pya.Layout()
        verify_layout.read(temporary_output)
        verify_top_cells = list(verify_layout.top_cells())
        if len(verify_top_cells) != 1 or verify_top_cells[0].name != request["top_cell"]:
            return worker_error(
                "DRAWING_TOP_CELL_ROUNDTRIP_FAILED",
                "Fresh-loaded drawing does not have the requested single top cell.",
                {"top_cells": [cell.name for cell in verify_top_cells]},
                "Fix the declared hierarchy before writing the layout.",
            )
        if abs(verify_layout.dbu - float(request["dbu_um"])) > 1e-15:
            return worker_error(
                "DRAWING_DBU_ROUNDTRIP_FAILED",
                "Fresh-loaded drawing DBU differs from the requested DBU.",
                {"requested_dbu_um": request["dbu_um"], "actual_dbu_um": verify_layout.dbu},
                "Use a supported exact layout DBU.",
            )

        cell_reports = []
        for cell_name in request["cells"]:
            verify_cell = verify_layout.cell(cell_name)
            if verify_cell is None:
                return worker_error(
                    "DRAWING_CELL_ROUNDTRIP_FAILED",
                    "A declared drawing cell is missing after fresh reload.",
                    {"cell": cell_name},
                    "Inspect the cell hierarchy and output format.",
                )
            if _direct_instances(verify_layout, verify_cell) != expected_instances[cell_name]:
                return worker_error(
                    "DRAWING_INSTANCE_ROUNDTRIP_FAILED",
                    "Cell instances changed during layout round-trip.",
                    {"cell": cell_name},
                    "Inspect instance transforms and hierarchy.",
                )
            layer_reports = []
            for layer_name, spec in request["layers"].items():
                verify_layer = verify_layout.find_layer(
                    int(spec["layer"]), int(spec["datatype"])
                )
                actual_region = pya.Region()
                actual_texts = []
                if verify_layer is not None:
                    actual_region = _direct_region(verify_cell, verify_layer)
                    actual_texts = _direct_texts(verify_cell, verify_layer)
                xor_region = (
                    expected_regions[(cell_name, layer_name)] ^ actual_region
                ).merged()
                if not xor_region.is_empty() or actual_texts != expected_texts[(cell_name, layer_name)]:
                    return worker_error(
                        "DRAWING_GEOMETRY_ROUNDTRIP_FAILED",
                        "Cell geometry or text changed during fresh reload.",
                        {
                            "cell": cell_name,
                            "layer": layer_name,
                            "xor_area_dbu2": xor_region.area(),
                            "text_match": actual_texts == expected_texts[(cell_name, layer_name)],
                        },
                        "Inspect DBU alignment, shapes, and output format.",
                    )
                if not actual_region.is_empty() or actual_texts:
                    layer_reports.append(
                        {
                            "name": layer_name,
                            "layer": int(spec["layer"]),
                            "datatype": int(spec["datatype"]),
                            "geometry_area_um2": actual_region.area()
                            * verify_layout.dbu
                            * verify_layout.dbu,
                            "text_count": len(actual_texts),
                        }
                    )
            cell_reports.append(
                {
                    "name": cell_name,
                    "bbox_um": _box_um(verify_cell.bbox(), verify_layout.dbu),
                    "direct_instance_count": len(expected_instances[cell_name]),
                    "used_layers": layer_reports,
                }
            )

        os.replace(temporary_output, output_path)
    except Exception as exc:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        if os.path.exists(output_path):
            os.unlink(output_path)
        return worker_error(
            "DRAWING_GENERATION_FAILED",
            "KLayout could not generate and verify the Manhattan drawing.",
            {"output_layout_path": output_path, "error_type": type(exc).__name__, "error": str(exc)},
            "Inspect the normalized drawing plan and KLayout version.",
        )
    finally:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)

    final_layout = pya.Layout()
    final_layout.read(output_path)
    final_top = final_layout.cell(str(request["top_cell"]))
    return {
        "ok": True,
        "production_ready": False,
        "output_layout_path": output_path,
        "format": os.path.splitext(output_path)[1].lower().lstrip("."),
        "top_cell": final_top.name,
        "top_cell_count": len(list(final_layout.top_cells())),
        "dbu_um": final_layout.dbu,
        "bbox_um": _box_um(final_top.bbox(), final_layout.dbu),
        "layers": [
            {"layer": layer, "datatype": datatype}
            for layer, datatype in _layer_pairs(final_layout)
        ],
        "cell_count": final_layout.cells(),
        "cells": cell_reports,
        "operation_counts": operation_counts,
        "fresh_reload_verified": True,
        "integer_dbu_geometry_verified": True,
        "orthogonal_manhattan_contract": True,
    }
