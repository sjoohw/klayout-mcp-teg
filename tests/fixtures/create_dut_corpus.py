import pya


layout = pya.Layout()
layout.dbu = 0.001
active = layout.layer(2, 0)
gate = layout.layer(6, 0)
top = layout.create_cell("CORPUS")
for index, gate_length_dbu in enumerate((50, 100, 150)):
    cell = layout.create_cell(f"DUT_{gate_length_dbu}")
    cell.shapes(active).insert(pya.Box(-500, -500, 500, 500))
    half = gate_length_dbu // 2
    cell.shapes(gate).insert(pya.Box(-half, -600, gate_length_dbu - half, 600))
    top.insert(pya.CellInstArray(cell.cell_index(), pya.Trans(index * 2000, 0)))
if "variant_marker" in globals():
    marker = layout.create_cell("REPRODUCTION_BUILD_MARKER")
    marker_layer = layout.layer(99, 0)
    marker.shapes(marker_layer).insert(pya.Box(0, 0, int(variant_marker), 1))
layout.write(str(output_path))
