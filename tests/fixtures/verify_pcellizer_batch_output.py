"""Fresh-reload semantic checks for one generated batch output."""

import json
from pathlib import Path

import pya


layout = pya.Layout()
layout.read(str(layout_path))
top = layout.cell("TOP")
layer = layout.layer(10, 0)
iterator = top.begin_shapes_rec(layer)
boxes = []
while not iterator.at_end():
    boxes.append(iterator.itrans() * iterator.shape().box)
    iterator.next()
boxes.sort(key=lambda box: box.left)
result = {
    "dbu_um": float(layout.dbu),
    "top_bbox_dbu": [top.bbox().left, top.bbox().bottom, top.bbox().right, top.bbox().top],
    "direct_instance_count": sum(1 for _ in top.each_inst()),
    "occurrence_boxes_dbu": [
        [box.left, box.bottom, box.right, box.top] for box in boxes
    ],
    "top_cells": sorted(cell.name for cell in layout.top_cells()),
}
Path(str(result_path)).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
