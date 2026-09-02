"""Open a real hierarchical reference and render the navigator ROI marker."""

import json
import sys
from pathlib import Path

import pya


project_root = Path(str(project_root))
result_path = Path(str(result_path))
image_path = Path(str(image_path))
source_path = Path(str(source_path))
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root / "klayout_plugin"))

from reference_navigator_panel import open_reference_view_in_klayout


layout = pya.Layout()
layout.dbu = 0.001
top = layout.create_cell("REFERENCE_TOP")
unit = layout.create_cell("REFERENCE_UNIT")
m1 = layout.layer(pya.LayerInfo(10, 0))
unit.shapes(m1).insert(pya.DBox(0.0, 0.0, 4.0, 2.0))
top.insert(pya.DCellInstArray(unit.cell_index(), pya.DTrans(10.0, 5.0)))
layout.write(str(source_path))

manifest = {
    "view_id": "view-real",
    "reference_id": "ref-real",
    "stored_layout_path": str(source_path),
    "top_cell": "REFERENCE_TOP",
    "view_bbox_um": [10.0, 5.0, 14.0, 7.0],
    "relevant_layers": ["10/0"],
    "usage_mode": "normal_style",
}
result = open_reference_view_in_klayout(manifest)
view = pya.Application.instance().main_window().current_view()
view.save_image(str(image_path), 640, 360)
result["active_cell"] = view.active_cellview().cell.name
result["image_exists"] = image_path.is_file() and image_path.stat().st_size > 0
result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
