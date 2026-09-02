import pya


layout = pya.Layout()
layout.dbu = 0.001
pad = layout.create_cell("PAD_MACRO_40X40")

boundary = layout.layer(20, 0)
access = layout.layer(10, 0)
pad.shapes(boundary).insert(pya.Box(0, 0, 40000, 40000))
pad.shapes(access).insert(pya.Box(10000, 10000, 30000, 30000))
layout.write(str(output_path))
