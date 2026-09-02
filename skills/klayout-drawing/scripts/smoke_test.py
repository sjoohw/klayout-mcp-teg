"""Create and fresh-reload a small GDS from the bundled PCell template."""

import importlib.util
import os

import pya


def required_script_variable(name):
    value = globals().get(name)
    if not value:
        raise RuntimeError(f"Pass -rd {name}=<value>")
    return str(value)


asset_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "python_pcell_library.py")
)
spec = importlib.util.spec_from_file_location("klayout_drawing_template", asset_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

output_path = os.path.abspath(required_script_variable("output_path"))
layout = pya.Layout()
layout.dbu = 0.001
top = layout.create_cell("TOP")
variant = layout.create_cell(
    "LinearTaper",
    "KLayoutDrawing",
    {
        "drawing_layer": pya.LayerInfo(1, 0),
        "length_um": 20.0,
        "width_in_um": 1.0,
        "width_out_um": 4.0,
    },
)
if variant is None or not variant.is_pcell_variant():
    raise RuntimeError("Failed to create LinearTaper PCell variant")

top.insert(pya.CellInstArray(variant.cell_index(), pya.Trans()))

# Only the separate interchange test output is flattened. Editable PCell sources must remain intact.
top.flatten(True)
layout.write(output_path)

round_trip = pya.Layout()
round_trip.read(output_path)
round_top = round_trip.cell("TOP")
if round_top is None:
    raise RuntimeError("Round-trip layout has no TOP cell")

expected_bbox = pya.Box(0, -2000, 20000, 2000)
if round_top.bbox() != expected_bbox:
    raise RuntimeError(f"Unexpected TOP bbox: {round_top.bbox()} expected {expected_bbox}")

print(f"smoke-test=ok output={output_path} bbox={round_top.bbox()}")
