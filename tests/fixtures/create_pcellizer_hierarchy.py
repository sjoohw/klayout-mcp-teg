"""Create a transform/array hierarchy fixture for PCellizer H0 tests."""

import os

import pya


output_path = os.path.abspath(str(globals()["output_path"]))
layout = pya.Layout()
layout.dbu = 0.001
layer = layout.layer(pya.LayerInfo(1, 0))

leaf = layout.create_cell("LEAF")
leaf.shapes(layer).insert(pya.Box(0, 0, 1000, 2000))

mid = layout.create_cell("MID")
mid.insert(
    pya.CellInstArray(
        leaf.cell_index(),
        pya.Trans(1, True, 3000, 4000),
    )
)

top = layout.create_cell("TOP")
top.insert(
    pya.CellInstArray(
        mid.cell_index(),
        pya.Trans(1, False, 10000, 20000),
        pya.Vector(5000, 0),
        pya.Vector(0, 7000),
        3,
        2,
    )
)
top.insert(
    pya.CellInstArray(
        leaf.cell_index(),
        pya.ICplxTrans(1.5, 45.0, False, 50000, 10000),
    )
)
layout.write(output_path)
