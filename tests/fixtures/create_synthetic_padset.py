"""Create a hierarchical synthetic padset under KLayout for integration tests."""

import os

import pya


output_path = os.path.abspath(str(globals()["output_path"]))
pad_shape = str(globals().get("pad_shape", "box"))
attach_routes = str(globals().get("attach_routes", "0")) == "1"
bridge_short = str(globals().get("bridge_short", "0")) == "1"
landing_routes = str(globals().get("landing_routes", "0")) == "1"
layout = pya.Layout()
layout.dbu = 0.002
m1 = layout.layer(pya.LayerInfo(10, 2))
pad = layout.create_cell("PAD")
if pad_shape == "mesh":
    for x in (0.0, 9.5, 19.0, 28.5, 38.0):
        pad.shapes(m1).insert(pya.DBox(x, 10.0, min(x + 2.0, 40.0), 50.0))
    for y in (10.0, 19.5, 29.0, 38.5, 48.0):
        pad.shapes(m1).insert(pya.DBox(0.0, y, 40.0, min(y + 2.0, 50.0)))
elif pad_shape == "polygon":
    pad.shapes(m1).insert(
        pya.DPolygon(
            [
                pya.DPoint(0.2, 10.0),
                pya.DPoint(39.8, 10.0),
                pya.DPoint(40.0, 10.2),
                pya.DPoint(40.0, 49.8),
                pya.DPoint(39.8, 50.0),
                pya.DPoint(0.2, 50.0),
                pya.DPoint(0.0, 49.8),
                pya.DPoint(0.0, 10.2),
            ]
        )
    )
else:
    pad.shapes(m1).insert(pya.DBox(0.0, 10.0, 40.0, 50.0))
top = layout.create_cell("PADSET")
for index in range(25):
    top.insert(pya.DCellInstArray(pad.cell_index(), pya.DTrans(20.0 + 80.0 * index, 0.0)))
if attach_routes:
    top.shapes(m1).insert(
        pya.DPath([pya.DPoint(1800.0, 49.0), pya.DPoint(1800.0, 58.0)], 2.0)
    )
    top.shapes(m1).insert(
        pya.DPolygon(
            [
                pya.DPoint(1879.0, 2.0),
                pya.DPoint(1881.0, 2.0),
                pya.DPoint(1882.0, 5.0),
                pya.DPoint(1881.0, 11.0),
                pya.DPoint(1879.0, 11.0),
                pya.DPoint(1878.0, 5.0),
            ]
        )
    )
if bridge_short:
    top.shapes(m1).insert(pya.DBox(1819.0, 29.0, 1861.0, 31.0))
if landing_routes:
    top.shapes(m1).insert(
        pya.DPath(
            [
                pya.DPoint(1800.0, 49.0),
                pya.DPoint(1800.0, 54.0),
                pya.DPoint(80.0, 54.0),
                pya.DPoint(80.0, 49.0),
            ],
            2.0,
        )
    )
    top.shapes(m1).insert(
        pya.DPath(
            [
                pya.DPoint(1960.0, 11.0),
                pya.DPoint(1960.0, 6.0),
                pya.DPoint(80.0, 6.0),
                pya.DPoint(80.0, 11.0),
            ],
            2.0,
        )
    )
layout.write(output_path)
