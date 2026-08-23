"""Create a hierarchical sample DUT for inventory integration tests."""

import os

import pya


output_path = os.path.abspath(str(globals()["output_path"]))
second_top = str(globals().get("second_top", "0")) == "1"
unmapped_layer = str(globals().get("unmapped_layer", "0")) == "1"

layout = pya.Layout()
layout.dbu = 0.001
active = layout.layer(pya.LayerInfo(1, 0))
poly = layout.layer(pya.LayerInfo(2, 0))
contact = layout.layer(pya.LayerInfo(3, 0))
m1 = layout.layer(pya.LayerInfo(10, 2))
label = layout.layer(pya.LayerInfo(100, 0))

unit = layout.create_cell("TR_UNIT")
unit.shapes(active).insert(pya.DBox(-0.6, -0.3, 0.6, 0.3))
unit.shapes(poly).insert(pya.DBox(-0.08, -0.5, 0.08, 0.5))
unit.shapes(contact).insert(pya.DBox(-0.45, -0.1, -0.25, 0.1))
unit.shapes(contact).insert(pya.DBox(0.25, -0.1, 0.45, 0.1))

top = layout.create_cell("SAMPLE_DUT")
for x, y in ((-2.0, -1.0), (2.0, -1.0), (-2.0, 1.0), (2.0, 1.0)):
    top.insert(pya.DCellInstArray(unit.cell_index(), pya.DTrans(x, y)))

top.shapes(m1).insert(pya.DPath([pya.DPoint(-2.0, 0.0), pya.DPoint(-20.2, 0.0)], 0.4))
top.shapes(m1).insert(pya.DPath([pya.DPoint(2.0, 0.0), pya.DPoint(20.2, 0.0)], 0.4))
top.shapes(m1).insert(pya.DPath([pya.DPoint(0.0, 1.0), pya.DPoint(0.0, 20.2)], 0.4))
top.shapes(m1).insert(pya.DPath([pya.DPoint(0.0, -1.0), pya.DPoint(0.0, -20.2)], 0.4))
for string, x, y in (("S", -20.0, 0.0), ("D", 20.0, 0.0), ("G", 0.0, 20.0), ("B", 0.0, -20.0)):
    top.shapes(label).insert(pya.DText(string, pya.DTrans(x, y)))

if unmapped_layer:
    unknown = layout.layer(pya.LayerInfo(99, 7))
    top.shapes(unknown).insert(pya.DBox(-0.5, -0.5, 0.5, 0.5))

if second_top:
    extra = layout.create_cell("EXTRA_TOP")
    extra.shapes(m1).insert(pya.DBox(100.0, 100.0, 101.0, 101.0))

layout.write(output_path)
