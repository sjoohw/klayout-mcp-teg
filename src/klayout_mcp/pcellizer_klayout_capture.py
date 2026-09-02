"""KLayout GUI selection/ruler capture for the PCellizer P0 frontend."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pya

from .errors import AnalysisError
from .pcellizer_contract import (
    build_selection_manifest,
    build_source_layout_identity,
    normalize_occurrence_path,
)
from .pcellizer_fingerprint import (
    bind_ruler_to_selected_edges,
    build_neighborhood_fingerprint,
    build_shape_identity,
    micron_ruler_to_exact_dbu,
    normalize_geometry_record,
)
from .workflow_manifest import canonical_sha256


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details=details,
        next_action="Return to the source top cell, save it, and select exact shapes plus one ruler.",
    )


def _point(point) -> list[int]:
    return [int(point.x), int(point.y)]


def _box(box) -> list[int]:
    return [int(box.left), int(box.bottom), int(box.right), int(box.top)]


def serialize_klayout_shape(shape) -> dict[str, Any]:
    """Serialize one direct-cell KLayout shape into the exact pure contract."""

    if shape.is_box():
        return normalize_geometry_record({"kind": "box", "bbox_dbu": _box(shape.box)})
    if shape.is_path():
        path = shape.path
        return normalize_geometry_record(
            {
                "kind": "path",
                "points_dbu": [_point(point) for point in path.each_point()],
                "width_dbu": int(shape.path_width),
                "begin_extension_dbu": int(shape.path_bgnext),
                "end_extension_dbu": int(shape.path_endext),
                "round": bool(shape.round_path),
            }
        )
    if shape.is_polygon():
        polygon = shape.polygon
        return normalize_geometry_record(
            {
                "kind": "polygon",
                "hull_dbu": [_point(point) for point in polygon.each_point_hull()],
                "holes_dbu": [
                    [_point(point) for point in polygon.each_point_hole(index)]
                    for index in range(polygon.holes())
                ],
            }
        )
    if shape.is_edge():
        edge = shape.edge
        return normalize_geometry_record(
            {"kind": "edge", "edge_dbu": [_point(edge.p1), _point(edge.p2)]}
        )
    _fail(
        "UNSUPPORTED_PCELLIZER_SHAPE_KIND",
        "Initial PCellizer capture supports box, polygon, path and edge shapes only.",
        shape_type=str(shape),
    )


def _transform_record(transform) -> dict[str, Any]:
    return {
        "displacement_dbu": [int(transform.disp.x), int(transform.disp.y)],
        "angle_degrees": float(transform.angle),
        "mirror": bool(transform.is_mirror()),
        "magnification": float(transform.mag),
    }


def _instance_ordinal(parent_cell, target_instance) -> int:
    for ordinal, candidate in enumerate(parent_cell.each_inst()):
        if candidate == target_instance:
            return ordinal
    _fail(
        "PCELLIZER_INSTANCE_PATH_STALE",
        "Selected instance no longer exists in its parent cell.",
        parent_cell=parent_cell.name,
    )


def occurrence_path_from_selection(selection, layout) -> dict[str, Any]:
    """Convert ObjectInstPath into the same exact path used by H0 inventory."""

    top_cell = layout.cell(int(selection.top))
    if top_cell is None:
        _fail(
            "PCELLIZER_SELECTION_TOP_MISSING",
            "Selected object top cell is no longer present.",
            top_cell_index=int(selection.top),
        )
    segments = []
    parent = top_cell
    for element in selection.each_inst():
        cell_inst = element.cell_inst()
        child = layout.cell(int(cell_inst.cell_index))
        is_regular = bool(cell_inst.is_regular_array())
        member_count = int(cell_inst.size())
        columns = int(cell_inst.na) if is_regular else 1
        rows = int(cell_inst.nb) if is_regular else 1
        a_vector = cell_inst.a if is_regular else pya.Vector(0, 0)
        b_vector = cell_inst.b if is_regular else pya.Vector(0, 0)
        element_column = int(element.ia())
        element_row = int(element.ib())
        column = element_column if element_column >= 0 else 0
        row = element_row if element_row >= 0 else 0
        blockers = (
            []
            if is_regular or member_count == 1
            else ["non_regular_iterated_instance"]
        )
        segments.append(
            {
                "parent_cell": parent.name,
                "child_cell": child.name,
                "instance_ordinal": _instance_ordinal(parent, element.inst()),
                "transform": _transform_record(element.specific_cplx_trans()),
                "array": {
                    "columns": columns if is_regular else member_count,
                    "rows": rows,
                    "column": column,
                    "row": row,
                    "a_vector_dbu": [int(a_vector.x), int(a_vector.y)],
                    "b_vector_dbu": [int(b_vector.x), int(b_vector.y)],
                    "regular": is_regular or member_count == 1,
                },
                "authoring_blockers": blockers,
            }
        )
        parent = child
    return normalize_occurrence_path(top_cell=top_cell.name, segments=segments)


def _shape_ordinal_and_duplicate_count(cell, layer_index, target_shape, geometry):
    ordinal = None
    duplicate_count = 0
    for index, candidate in enumerate(cell.each_shape(layer_index)):
        candidate_geometry = serialize_klayout_shape(candidate)
        if candidate_geometry == geometry:
            duplicate_count += 1
        if candidate == target_shape:
            ordinal = index
    if ordinal is None:
        _fail(
            "PCELLIZER_SELECTED_SHAPE_STALE",
            "Selected shape no longer exists in its direct cell.",
            cell=cell.name,
            layer_index=int(layer_index),
        )
    return ordinal, duplicate_count


def _top_edges(selection) -> list[list[list[int]]]:
    transform = selection.trans()
    result = []
    shape = selection.shape
    if shape.is_box():
        box = shape.box
        points = [box.p1, pya.Point(box.right, box.bottom), box.p2, pya.Point(box.left, box.top)]
        edges = [pya.Edge(points[index], points[(index + 1) % 4]) for index in range(4)]
    elif shape.is_polygon():
        edges = list(shape.polygon.each_edge())
    elif shape.is_path():
        edges = list(shape.path.polygon().each_edge())
    elif shape.is_edge():
        edges = [shape.edge]
    else:
        _fail(
            "UNSUPPORTED_PCELLIZER_SHAPE_KIND",
            "Cannot derive selectable edges from this shape kind.",
        )
    for edge in edges:
        transformed = transform * edge
        if transformed.p1 != transformed.p2:
            result.append([_point(transformed.p1), _point(transformed.p2)])
    return result


def _neighborhood(cell, layer_index, selected_bbox, radius_dbu):
    search_box = selected_bbox.enlarged(int(radius_dbu))
    identities = []
    direct_shapes = list(cell.each_shape(layer_index))
    geometries = [serialize_klayout_shape(shape) for shape in direct_shapes]
    duplicate_counts = {
        canonical_sha256(geometry): sum(
            1 for candidate in geometries if candidate == geometry
        )
        for geometry in geometries
    }
    for ordinal, (shape, geometry) in enumerate(zip(direct_shapes, geometries)):
        if not shape.bbox().touches(search_box):
            continue
        identities.append(
            build_shape_identity(
                geometry=geometry,
                layer=int(cell.layout().get_info(layer_index).layer),
                datatype=int(cell.layout().get_info(layer_index).datatype),
                shape_ordinal=ordinal,
                duplicate_geometry_count=duplicate_counts[
                    canonical_sha256(geometry)
                ],
            )
        )
    return build_neighborhood_fingerprint(identities, radius_dbu=radius_dbu)


def _capture_shape(selection, layout, neighborhood_radius_dbu):
    if selection.is_cell_inst():
        _fail(
            "PCELLIZER_SHAPE_SELECTION_REQUIRED",
            "Select geometry shapes, not cell instances.",
        )
    layer_index = int(selection.layer)
    info = layout.get_info(layer_index)
    cell = layout.cell(int(selection.cell_index()))
    shape = selection.shape
    geometry = serialize_klayout_shape(shape)
    ordinal, duplicate_count = _shape_ordinal_and_duplicate_count(
        cell, layer_index, shape, geometry
    )
    identity = build_shape_identity(
        geometry=geometry,
        layer=int(info.layer),
        datatype=int(info.datatype),
        shape_ordinal=ordinal,
        duplicate_geometry_count=duplicate_count,
    )
    neighborhood = _neighborhood(
        cell, layer_index, shape.bbox(), neighborhood_radius_dbu
    )
    return {
        "cell": cell.name,
        "cell_index": int(cell.cell_index()),
        "layer_index": layer_index,
        "layer": int(info.layer),
        "datatype": int(info.datatype),
        "occurrence_path": occurrence_path_from_selection(selection, layout),
        "shape_identity": identity,
        "neighborhood": neighborhood,
        "top_edges_dbu": _top_edges(selection),
    }


def capture_parameter_selection(
    *,
    layout,
    source_layout_path: str,
    selected_objects: Iterable[Any],
    selected_annotations: Iterable[Any],
    view_dirty: bool,
    neighborhood_radius_dbu: int = 1,
    selection_mode: str = "explicit_shapes_and_ruler",
) -> dict[str, Any]:
    """Capture exact GUI state into endpoint SelectionManifests."""

    if view_dirty:
        _fail(
            "DIRTY_PCELLIZER_SOURCE_FORBIDDEN",
            "Save or revert in-memory layout edits before PCellizer capture.",
        )
    if not str(source_layout_path).strip():
        _fail(
            "UNSAVED_PCELLIZER_SOURCE",
            "Save the source layout to GDS or OASIS before PCellizer capture.",
        )
    if not Path(source_layout_path).is_file():
        _fail(
            "PCELLIZER_SOURCE_NOT_FOUND",
            "The saved source layout file is no longer available.",
            source_layout_path=str(source_layout_path),
        )
    selections = list(selected_objects)
    annotations = list(selected_annotations)
    if not selections:
        _fail(
            "PCELLIZER_SHAPE_SELECTION_REQUIRED",
            "Select at least one shape touched by the ruler endpoints.",
        )
    if len(annotations) != 1:
        _fail(
            "PCELLIZER_SINGLE_RULER_REQUIRED",
            "Select exactly one ruler annotation.",
            selected_ruler_count=len(annotations),
        )
    annotation = annotations[0]
    if int(annotation.segments) != 1:
        _fail(
            "PCELLIZER_SINGLE_SEGMENT_RULER_REQUIRED",
            "The initial PCellizer supports one ruler segment only.",
            segment_count=int(annotation.segments),
        )
    top_indices = {int(selection.top) for selection in selections}
    if len(top_indices) != 1:
        _fail(
            "PCELLIZER_MIXED_SELECTION_TOPS",
            "All selected shapes must share one source top cell.",
            top_cell_indices=sorted(top_indices),
        )
    top_cell = layout.cell(next(iter(top_indices)))
    if not top_cell.is_top():
        _fail(
            "PCELLIZER_SOURCE_TOP_REQUIRED",
            "Capture must be rooted at a real source top cell so occurrence paths stay complete.",
            selected_root_cell=top_cell.name,
        )
    source = build_source_layout_identity(
        source_layout_path, top_cell=top_cell.name, dbu_um=float(layout.dbu)
    )
    captures = [
        _capture_shape(selection, layout, neighborhood_radius_dbu)
        for selection in selections
    ]
    points_um = [[float(point.x), float(point.y)] for point in annotation.points]
    ruler_dbu = micron_ruler_to_exact_dbu(points_um, dbu_um=float(layout.dbu))
    binding = bind_ruler_to_selected_edges(ruler_dbu, captures)

    endpoint_manifests = []
    for endpoint_index, endpoint_binding in enumerate(binding["endpoint_bindings"]):
        capture = captures[endpoint_binding["selection_index"]]
        identity = capture["shape_identity"]
        endpoint_manifests.append(
            {
                "endpoint_index": endpoint_index,
                "selection_index": endpoint_binding["selection_index"],
                "manifest": build_selection_manifest(
                    source=source,
                    occurrence_path=capture["occurrence_path"],
                    layer=capture["layer"],
                    datatype=capture["datatype"],
                    shape_fingerprint_sha256=identity[
                        "shape_fingerprint_sha256"
                    ],
                    shape_ordinal=identity["shape_ordinal"],
                    duplicate_geometry_count=identity[
                        "duplicate_geometry_count"
                    ],
                    edge_dbu=endpoint_binding["edge_dbu"],
                    neighborhood_fingerprint_sha256=capture["neighborhood"][
                        "neighborhood_fingerprint_sha256"
                    ],
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "kind": "PCellizerParameterCapture",
        "source": source,
        "ruler": binding,
        "selected_shapes": captures,
        "endpoint_manifests": endpoint_manifests,
        "scope": "current_occurrence",
        "selection_mode": selection_mode,
        "edge_snap": "exact_dbu",
        "source_layout_modified": False,
        "flattening_performed": False,
        "production_ready": False,
    }
    payload["parameter_capture_sha256"] = canonical_sha256(payload)
    return payload


def _resolve_shapes_from_selected_layer(view, cellview, annotation):
    """Resolve endpoint shapes from one selected layer plus one ruler."""

    selected_layers = list(view.selected_layers)
    if len(selected_layers) != 1:
        _fail(
            "PCELLIZER_SINGLE_LAYER_REQUIRED",
            "With no explicit shapes, select exactly one parameterized layer.",
            selected_layer_count=len(selected_layers),
        )
    if not cellview.cell.is_top():
        _fail(
            "PCELLIZER_SOURCE_TOP_REQUIRED",
            "Layer+ruler auto-resolution currently requires viewing the source top cell.",
            current_cell=cellview.cell.name,
        )
    layer_node = selected_layers[0].current()
    if int(layer_node.source_cellview) not in (-1, int(cellview.index)):
        _fail(
            "PCELLIZER_LAYER_CELLVIEW_MISMATCH",
            "The selected layer belongs to a different cell view.",
        )
    layer_index = int(layer_node.layer_index)
    if layer_index < 0:
        _fail(
            "PCELLIZER_PHYSICAL_LAYER_REQUIRED",
            "The selected layer view does not resolve to one physical layout layer.",
        )
    ruler_dbu = micron_ruler_to_exact_dbu(
        [[float(point.x), float(point.y)] for point in annotation.points],
        dbu_um=float(cellview.layout.dbu),
    )
    selections = []
    for point in ruler_dbu:
        iterator = cellview.cell.begin_shapes_rec_touching(
            layer_index, pya.Box(point[0], point[1], point[0], point[1])
        )
        while not iterator.at_end():
            candidate = pya.ObjectInstPath(iterator, int(cellview.index))
            if not any(candidate == existing for existing in selections):
                selections.append(candidate)
            iterator.next()
    if not selections:
        _fail(
            "PCELLIZER_RULER_ENDPOINT_NOT_ON_LAYER",
            "No shape on the selected layer touches either ruler endpoint.",
            layer_index=layer_index,
        )
    return selections


def capture_parameter_from_view(view=None, *, neighborhood_radius_dbu: int = 1):
    """Capture the active KLayout view without modifying layout or selection."""

    active_view = view or pya.LayoutView.current()
    if active_view is None:
        _fail("PCELLIZER_VIEW_REQUIRED", "No active KLayout layout view is available.")
    cellview = active_view.active_cellview()
    if cellview is None or not cellview.is_valid():
        _fail("PCELLIZER_VIEW_REQUIRED", "The active KLayout cell view is invalid.")
    selected_objects = list(active_view.each_object_selected())
    selected_annotations = list(active_view.each_annotation_selected())
    selection_mode = "explicit_shapes_and_ruler"
    if not selected_objects:
        if len(selected_annotations) != 1:
            _fail(
                "PCELLIZER_SINGLE_RULER_REQUIRED",
                "Select one ruler before layer+ruler auto-resolution.",
                selected_ruler_count=len(selected_annotations),
            )
        selected_objects = _resolve_shapes_from_selected_layer(
            active_view, cellview, selected_annotations[0]
        )
        selection_mode = "selected_layer_and_ruler_auto_resolved"
    for selection in selected_objects:
        if not selection.is_valid(active_view):
            _fail(
                "PCELLIZER_SELECTION_STALE",
                "A selected KLayout object path is no longer valid.",
            )
    return capture_parameter_selection(
        layout=cellview.layout,
        source_layout_path=str(cellview.filename),
        selected_objects=selected_objects,
        selected_annotations=selected_annotations,
        view_dirty=bool(cellview.is_dirty()),
        neighborhood_radius_dbu=neighborhood_radius_dbu,
        selection_mode=selection_mode,
    )
