"""Validated declarative plans for general-purpose Manhattan layout drawing."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import AnalysisError
from .dbu_grid import DbuGridError, micron_to_dbu


SUPPORTED_OUTPUT_SUFFIXES = {".gds", ".oas"}
SUPPORTED_OPERATION_TYPES = {"add_box", "add_text", "add_instance", "boolean"}
SUPPORTED_BOOLEAN_OPERATIONS = {"or", "and", "xor", "a_not_b"}
SUPPORTED_ROTATIONS = {0, 90, 180, 270}


def _finite_number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise AnalysisError(
            code="INVALID_DRAWING_NUMBER",
            message=f"{field} must be a finite number.",
            details={"field": field, "value": value},
            next_action="Provide a finite micron value.",
        )
    return float(value)


def _exact_dbu(value: object, *, dbu_um: float, field: str) -> int:
    numeric = _finite_number(value, field=field)
    try:
        return micron_to_dbu(numeric, dbu_um)
    except DbuGridError as exc:
        if not math.isfinite(float(dbu_um)) or dbu_um <= 0:
            raise AnalysisError(
                code="INVALID_DRAWING_DBU",
                message="Drawing DBU must be a finite positive micron value.",
                details={"dbu_um": dbu_um},
                next_action="Provide a positive DBU such as 0.001 um.",
            ) from exc
        raise AnalysisError(
            code="DRAWING_COORDINATE_OFF_DBU_GRID",
            message=f"{field} is not representable on the requested DBU grid.",
            details={
                "field": field,
                "value_um": numeric,
                "dbu_um": dbu_um,
                "numeric_drift_tolerance_dbu": 1e-9,
            },
            next_action="Snap the coordinate to an integer multiple of dbu_um.",
        ) from exc


def _name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisError(
            code="INVALID_DRAWING_NAME",
            message=f"{field} must be a non-empty string.",
            details={"field": field, "value": value},
            next_action="Provide a stable visible name.",
        )
    return value.strip()


def _layer_name(value: object, *, layers: Mapping[str, Any], field: str) -> str:
    name = _name(value, field=field)
    if name not in layers:
        raise AnalysisError(
            code="DRAWING_LAYER_NOT_FOUND",
            message=f"Drawing operation references unknown layer name {name!r}.",
            details={"field": field, "layer": name, "available_layers": sorted(layers)},
            next_action="Use a layer name declared in the layers list.",
        )
    return name


def _normalize_layers(raw_layers: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    if not raw_layers:
        raise AnalysisError(
            code="DRAWING_LAYERS_REQUIRED",
            message="At least one explicit layer/datatype pair is required.",
            details={},
            next_action="Declare layers as {name, layer, datatype} records.",
        )
    layers: dict[str, dict[str, int]] = {}
    pairs: dict[tuple[int, int], str] = {}
    for index, raw in enumerate(raw_layers):
        if not isinstance(raw, Mapping):
            raise AnalysisError(
                code="INVALID_DRAWING_LAYER",
                message="Every drawing layer must be an object.",
                details={"index": index, "layer": raw},
                next_action="Use {name, layer, datatype} records.",
            )
        name = _name(raw.get("name"), field=f"layers[{index}].name")
        layer = raw.get("layer")
        datatype = raw.get("datatype", 0)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (layer, datatype)
        ):
            raise AnalysisError(
                code="INVALID_DRAWING_LAYER",
                message="Layer and datatype must be non-negative integers.",
                details={"index": index, "layer": dict(raw)},
                next_action="Provide an explicit non-negative layer/datatype pair.",
            )
        pair = (int(layer), int(datatype))
        if name in layers or pair in pairs:
            raise AnalysisError(
                code="DUPLICATE_DRAWING_LAYER",
                message="Drawing layer names and layer/datatype pairs must be unique.",
                details={"name": name, "pair": list(pair), "existing_name": pairs.get(pair)},
                next_action="Use one unique name for each unique layer/datatype pair.",
            )
        layers[name] = {"layer": pair[0], "datatype": pair[1]}
        pairs[pair] = name
    return layers


def _validate_hierarchy(
    *, cells: Sequence[str], top_cell: str, operations: Sequence[Mapping[str, Any]]
) -> None:
    graph = {name: set() for name in cells}
    children = set()
    for operation in operations:
        if operation["type"] == "add_instance":
            graph[operation["parent_cell"]].add(operation["child_cell"])
            children.add(operation["child_cell"])
    if top_cell in children:
        raise AnalysisError(
            code="DRAWING_TOP_CELL_IS_CHILD",
            message="The declared top cell may not be instantiated by another cell.",
            details={"top_cell": top_cell},
            next_action="Remove the instance of the top cell or choose the actual root cell.",
        )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(cell: str) -> None:
        if cell in visiting:
            raise AnalysisError(
                code="DRAWING_HIERARCHY_CYCLE",
                message="Cell instances form a recursive hierarchy cycle.",
                details={"cell": cell},
                next_action="Remove recursive cell references.",
            )
        if cell in visited:
            return
        visiting.add(cell)
        for child in sorted(graph[cell]):
            visit(child)
        visiting.remove(cell)
        visited.add(cell)

    visit(top_cell)
    unreachable = sorted(set(cells) - visited)
    if unreachable:
        raise AnalysisError(
            code="DRAWING_UNREACHABLE_CELLS",
            message="Every declared cell must be reachable from the single top cell.",
            details={"top_cell": top_cell, "unreachable_cells": unreachable},
            next_action="Instantiate each reusable cell below the top or remove it.",
        )


def build_manhattan_drawing_plan(
    *,
    output_layout_path: str,
    dbu_um: float,
    top_cell: str,
    cells: Sequence[str],
    layers: Sequence[Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and normalize one atomic Manhattan drawing request."""

    dbu = _finite_number(dbu_um, field="dbu_um")
    if dbu <= 0:
        raise AnalysisError(
            code="INVALID_DRAWING_DBU",
            message="Drawing DBU must be positive.",
            details={"dbu_um": dbu_um},
            next_action="Provide a positive DBU such as 0.001 um.",
        )
    output = Path(output_layout_path).expanduser().resolve()
    if output.suffix.casefold() not in SUPPORTED_OUTPUT_SUFFIXES:
        raise AnalysisError(
            code="UNSUPPORTED_DRAWING_OUTPUT_FORMAT",
            message="Manhattan drawing output must be GDS or OASIS.",
            details={"output_layout_path": str(output)},
            next_action="Choose a new output path ending in .gds or .oas.",
        )
    if output.exists():
        raise AnalysisError(
            code="OUTPUT_ALREADY_EXISTS",
            message="Drawing output already exists and will not be overwritten.",
            details={"output_layout_path": str(output)},
            next_action="Choose a new output path.",
        )
    if not output.parent.is_dir():
        raise AnalysisError(
            code="OUTPUT_DIRECTORY_NOT_FOUND",
            message="Drawing output directory does not exist.",
            details={"output_directory": str(output.parent)},
            next_action="Create the output directory and retry.",
        )

    layer_map = _normalize_layers(layers)
    if not cells:
        raise AnalysisError(
            code="DRAWING_CELLS_REQUIRED",
            message="At least one cell is required.",
            details={},
            next_action="Declare the top cell and any reusable child cells.",
        )
    normalized_cells = [_name(value, field=f"cells[{index}]") for index, value in enumerate(cells)]
    if len(set(normalized_cells)) != len(normalized_cells):
        raise AnalysisError(
            code="DUPLICATE_DRAWING_CELL",
            message="Drawing cell names must be unique.",
            details={"cells": normalized_cells},
            next_action="Keep each cell name once.",
        )
    top = _name(top_cell, field="top_cell")
    if top not in normalized_cells:
        raise AnalysisError(
            code="DRAWING_TOP_CELL_NOT_DECLARED",
            message="The top cell must appear in the cells list.",
            details={"top_cell": top, "cells": normalized_cells},
            next_action="Add the top cell to cells.",
        )
    cell_set = set(normalized_cells)

    normalized_operations: list[dict[str, Any]] = []
    for index, raw in enumerate(operations):
        if not isinstance(raw, Mapping):
            raise AnalysisError(
                code="INVALID_DRAWING_OPERATION",
                message="Every drawing operation must be an object.",
                details={"index": index, "operation": raw},
                next_action="Use a supported typed operation object.",
            )
        op_type = raw.get("type")
        if op_type not in SUPPORTED_OPERATION_TYPES:
            raise AnalysisError(
                code="UNSUPPORTED_DRAWING_OPERATION",
                message="Drawing operation type is not supported.",
                details={"index": index, "type": op_type, "supported": sorted(SUPPORTED_OPERATION_TYPES)},
                next_action="Use add_box, add_text, add_instance, or boolean.",
            )
        operation: dict[str, Any] = {"type": op_type}
        if op_type == "add_instance":
            parent = _name(raw.get("parent_cell"), field=f"operations[{index}].parent_cell")
            child = _name(raw.get("child_cell"), field=f"operations[{index}].child_cell")
            if parent not in cell_set or child not in cell_set:
                raise AnalysisError(
                    code="DRAWING_CELL_NOT_FOUND",
                    message="Instance parent and child cells must be declared.",
                    details={"index": index, "parent_cell": parent, "child_cell": child},
                    next_action="Declare both cells in the cells list.",
                )
            origin = raw.get("origin_um", [0.0, 0.0])
            if not isinstance(origin, Sequence) or isinstance(origin, (str, bytes)) or len(origin) != 2:
                raise AnalysisError(
                    code="INVALID_DRAWING_ORIGIN",
                    message="Instance origin_um must contain [x, y].",
                    details={"index": index, "origin_um": origin},
                    next_action="Provide two micron coordinates.",
                )
            rotation = raw.get("rotation_deg", 0)
            mirror_x = raw.get("mirror_x", False)
            if rotation not in SUPPORTED_ROTATIONS or not isinstance(mirror_x, bool):
                raise AnalysisError(
                    code="INVALID_DRAWING_TRANSFORM",
                    message="Instances support orthogonal rotation and optional x mirroring only.",
                    details={"index": index, "rotation_deg": rotation, "mirror_x": mirror_x},
                    next_action="Use rotation_deg 0, 90, 180, or 270 and a boolean mirror_x.",
                )
            operation.update(
                {
                    "parent_cell": parent,
                    "child_cell": child,
                    "origin_dbu": [
                        _exact_dbu(origin[0], dbu_um=dbu, field=f"operations[{index}].origin_um[0]"),
                        _exact_dbu(origin[1], dbu_um=dbu, field=f"operations[{index}].origin_um[1]"),
                    ],
                    "rotation_deg": int(rotation),
                    "mirror_x": mirror_x,
                }
            )
        else:
            cell = _name(raw.get("cell"), field=f"operations[{index}].cell")
            if cell not in cell_set:
                raise AnalysisError(
                    code="DRAWING_CELL_NOT_FOUND",
                    message="Drawing operation references an undeclared cell.",
                    details={"index": index, "cell": cell},
                    next_action="Declare the cell before using it.",
                )
            operation["cell"] = cell
            if op_type == "add_box":
                layer = _layer_name(raw.get("layer"), layers=layer_map, field=f"operations[{index}].layer")
                bbox = raw.get("bbox_um")
                if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)) or len(bbox) != 4:
                    raise AnalysisError(
                        code="INVALID_DRAWING_BOX",
                        message="Box bbox_um must contain [x1, y1, x2, y2].",
                        details={"index": index, "bbox_um": bbox},
                        next_action="Provide four micron coordinates with positive area.",
                    )
                box = [
                    _exact_dbu(value, dbu_um=dbu, field=f"operations[{index}].bbox_um[{offset}]")
                    for offset, value in enumerate(bbox)
                ]
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise AnalysisError(
                        code="INVALID_DRAWING_BOX",
                        message="Box coordinates must define positive area.",
                        details={"index": index, "bbox_um": list(bbox)},
                        next_action="Use x1 < x2 and y1 < y2.",
                    )
                operation.update({"layer": layer, "bbox_dbu": box})
            elif op_type == "add_text":
                layer = _layer_name(raw.get("layer"), layers=layer_map, field=f"operations[{index}].layer")
                text = raw.get("text")
                origin = raw.get("origin_um")
                if not isinstance(text, str) or not text or not isinstance(origin, Sequence) or isinstance(origin, (str, bytes)) or len(origin) != 2:
                    raise AnalysisError(
                        code="INVALID_DRAWING_TEXT",
                        message="Text requires a non-empty string and [x, y] origin_um.",
                        details={"index": index, "text": text, "origin_um": origin},
                        next_action="Provide text, layer, and two micron origin coordinates.",
                    )
                operation.update(
                    {
                        "layer": layer,
                        "text": text,
                        "origin_dbu": [
                            _exact_dbu(origin[0], dbu_um=dbu, field=f"operations[{index}].origin_um[0]"),
                            _exact_dbu(origin[1], dbu_um=dbu, field=f"operations[{index}].origin_um[1]"),
                        ],
                    }
                )
            elif op_type == "boolean":
                boolean_op = raw.get("operation")
                input_layers = raw.get("input_layers")
                output_layer = _layer_name(raw.get("output_layer"), layers=layer_map, field=f"operations[{index}].output_layer")
                if boolean_op not in SUPPORTED_BOOLEAN_OPERATIONS or not isinstance(input_layers, Sequence) or isinstance(input_layers, (str, bytes)) or len(input_layers) != 2:
                    raise AnalysisError(
                        code="INVALID_DRAWING_BOOLEAN",
                        message="Boolean requires a supported operation and exactly two input layers.",
                        details={"index": index, "operation": boolean_op, "input_layers": input_layers},
                        next_action="Use or, and, xor, or a_not_b with two declared layers.",
                    )
                inputs = [
                    _layer_name(value, layers=layer_map, field=f"operations[{index}].input_layers")
                    for value in input_layers
                ]
                clear_output = raw.get("clear_output", False)
                if not isinstance(clear_output, bool):
                    raise AnalysisError(
                        code="INVALID_DRAWING_BOOLEAN",
                        message="clear_output must be boolean.",
                        details={"index": index, "clear_output": clear_output},
                        next_action="Use true or false.",
                    )
                operation.update(
                    {
                        "operation": boolean_op,
                        "input_layers": inputs,
                        "output_layer": output_layer,
                        "clear_output": clear_output,
                    }
                )
        normalized_operations.append(operation)

    _validate_hierarchy(cells=normalized_cells, top_cell=top, operations=normalized_operations)
    return {
        "operation": "draw_manhattan_layout",
        "output_layout_path": str(output),
        "dbu_um": dbu,
        "top_cell": top,
        "cells": normalized_cells,
        "layers": layer_map,
        "operations": normalized_operations,
    }
