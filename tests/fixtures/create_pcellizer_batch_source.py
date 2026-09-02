"""Create a shared-leaf 2000x60 source and exact one-box PCellizer capture."""

import json
import sys
from pathlib import Path

import pya


project_root = Path(str(project_root))
source_path = Path(str(source_path))
capture_path = Path(str(capture_path))
sys.path.insert(0, str(project_root / "src"))

from klayout_mcp.pcellizer_klayout_capture import capture_parameter_selection


layout = pya.Layout()
layout.dbu = 0.001
top = layout.create_cell("TOP")
leaf = layout.create_cell("DUT_LEAF")
parameter_layer = layout.layer(10, 0)
outline_layer = layout.layer(100, 0)
leaf.shapes(parameter_layer).insert(pya.Box(0, 0, 100, 50))
top.shapes(outline_layer).insert(pya.Box(0, 0, 2_000_000, 60_000))
top.insert(pya.CellInstArray(leaf.cell_index(), pya.Trans(1_000, 5_000)))
top.insert(pya.CellInstArray(leaf.cell_index(), pya.Trans(2_000, 5_000)))
layout.write(str(source_path))

fresh = pya.Layout()
fresh.read(str(source_path))
fresh_top = fresh.cell("TOP")
fresh_layer = fresh.layer(10, 0)
iterator = fresh_top.begin_shapes_rec(fresh_layer)
objects = []
while not iterator.at_end():
    objects.append(pya.ObjectInstPath(iterator, 0))
    iterator.next()
objects.sort(key=lambda item: (item.trans() * item.shape.bbox()).left)


class Ruler:
    segments = 1
    points = [pya.DPoint(1.0, 5.025), pya.DPoint(1.1, 5.025)]


capture = capture_parameter_selection(
    layout=fresh,
    source_layout_path=str(source_path),
    selected_objects=[objects[0]],
    selected_annotations=[Ruler()],
    view_dirty=False,
    neighborhood_radius_dbu=10,
)
capture_path.write_text(json.dumps(capture, sort_keys=True), encoding="utf-8")
