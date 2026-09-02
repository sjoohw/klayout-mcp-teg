import pya


layout = pya.Layout()
layout.dbu = 0.001
via = layout.create_cell("PAD_VIA_STACK")
pad = layout.create_cell("PAD_MACRO_40X40")
top = layout.create_cell("PAD_SOURCE")

m1 = layout.layer(10, 0)
via_layer = layout.layer(11, 0)
passivation = layout.layer(12, 0)
via.shapes(via_layer).insert(pya.Box(19000, 19000, 21000, 21000))
pad.shapes(m1).insert(pya.Box(0, 0, 40000, 40000))
pad.shapes(passivation).insert(pya.Box(1000, 1000, 39000, 39000))
pad.insert(pya.CellInstArray(via.cell_index(), pya.Trans()))
top.insert(pya.CellInstArray(pad.cell_index(), pya.Trans()))
layout.write(str(output_path))
