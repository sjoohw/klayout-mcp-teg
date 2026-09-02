"""Exercise PCellizer capture against fresh-reloaded KLayout objects."""

import json
import sys
from pathlib import Path

import pya


project_root = Path(str(project_root))
source_path = Path(str(source_path))
result_path = Path(str(result_path))
package_root = Path(str(package_root))
recovered_path = Path(str(recovered_path))
sys.path.insert(0, str(project_root / "src"))

from klayout_mcp.pcellizer_klayout_capture import (
    _resolve_shapes_from_selected_layer,
    capture_parameter_selection,
    serialize_klayout_shape,
)
from klayout_mcp.errors import AnalysisError
from klayout_mcp.workflow_manifest import canonical_sha256
from klayout_mcp.pcellizer_snapshot import (
    create_pcellizer_snapshot_package,
    recover_pcellizer_snapshot_source,
)


if not source_path.exists():
    layout = pya.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    leaf = layout.create_cell("LEAF")
    layer_index = layout.layer(10, 0)
    leaf.shapes(layer_index).insert(pya.Box(0, 0, 1000, 1000))
    leaf.shapes(layer_index).insert(pya.Box(2000, 0, 3000, 1000))
    top.insert(pya.CellInstArray(leaf.cell_index(), pya.Trans(10000, 5000)))
    layout.write(str(source_path))

fresh = pya.Layout()
fresh.read(str(source_path))
fresh_top = fresh.cell("TOP")
fresh_layer_index = fresh.layer(10, 0)
iterator = fresh_top.begin_shapes_rec(fresh_layer_index)
selections = []
while not iterator.at_end():
    selections.append(pya.ObjectInstPath(iterator, 0))
    iterator.next()


class OneSegmentRuler:
    segments = 1
    points = [pya.DPoint(11.0, 5.5), pya.DPoint(12.0, 5.5)]


result = capture_parameter_selection(
    layout=fresh,
    source_layout_path=str(source_path),
    selected_objects=selections,
    selected_annotations=[OneSegmentRuler()],
    view_dirty=False,
    neighborhood_radius_dbu=100,
)


class LayerNode:
    source_cellview = -1
    layer_index = fresh_layer_index


class LayerIterator:
    def current(self):
        return LayerNode()


class FakeView:
    selected_layers = [LayerIterator()]


class FakeCellView:
    layout = fresh
    cell = fresh_top
    index = 0


auto_selections = _resolve_shapes_from_selected_layer(
    FakeView(), FakeCellView(), OneSegmentRuler()
)
auto_result = capture_parameter_selection(
    layout=fresh,
    source_layout_path=str(source_path),
    selected_objects=auto_selections,
    selected_annotations=[OneSegmentRuler()],
    view_dirty=False,
    neighborhood_radius_dbu=100,
    selection_mode="selected_layer_and_ruler_auto_resolved",
)
result["auto_resolution"] = {
    "selection_count": len(auto_selections),
    "selection_mode": auto_result["selection_mode"],
    "endpoint_manifests_match": (
        auto_result["endpoint_manifests"] == result["endpoint_manifests"]
    ),
}

scratch = pya.Layout()
scratch_cell = scratch.create_cell("SCRATCH")
scratch_layer = scratch.layer(20, 1)
scratch_shapes = scratch_cell.shapes(scratch_layer)
shape_variants = [
    scratch_shapes.insert(pya.Box(0, 0, 10, 20)),
    scratch_shapes.insert(
        pya.Polygon([pya.Point(0, 0), pya.Point(20, 0), pya.Point(10, 10)])
    ),
    scratch_shapes.insert(pya.Path([pya.Point(0, 0), pya.Point(20, 0)], 4)),
    scratch_shapes.insert(pya.Edge(pya.Point(0, 0), pya.Point(0, 20))),
]
result["serialized_shape_kinds"] = [
    serialize_klayout_shape(shape)["kind"] for shape in shape_variants
]
try:
    capture_parameter_selection(
        layout=fresh,
        source_layout_path="",
        selected_objects=selections,
        selected_annotations=[OneSegmentRuler()],
        view_dirty=False,
    )
except AnalysisError as error:
    result["unsaved_source_error"] = error.code

snapshot = create_pcellizer_snapshot_package(
    capture=auto_result,
    package_root=str(package_root),
    session_id="integration-capture",
)
recovery = recover_pcellizer_snapshot_source(
    package_dir=snapshot["package_dir"], output_path=str(recovered_path)
)
reloaded = pya.Layout()
reloaded.read(str(recovered_path))
result["snapshot_roundtrip"] = {
    "layout_sha256": recovery["layout_sha256"],
    "source_runtime_dependency_used": recovery["source_runtime_dependency_used"],
    "top_cells": sorted(cell.name for cell in reloaded.top_cells()),
    "cell_names": sorted(cell.name for cell in reloaded.each_cell()),
    "flattening_performed": snapshot["manifest"]["flattening_performed"],
}
result["parameter_capture_sha256"] = canonical_sha256(result)
result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
