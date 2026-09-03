import pya


layout = pya.Layout()
layout.dbu = 0.001
active = layout.layer(2, 0)


def donut(name, hole_left):
    cell = layout.create_cell(name)
    polygon = pya.Polygon(
        [
            pya.Point(-500, -500),
            pya.Point(500, -500),
            pya.Point(500, 500),
            pya.Point(-500, 500),
        ]
    )
    polygon.insert_hole(
        [
            pya.Point(hole_left, -100),
            pya.Point(hole_left + 200, -100),
            pya.Point(hole_left + 200, 100),
            pya.Point(hole_left, 100),
        ]
    )
    cell.shapes(active).insert(polygon)


def two_components(name, split_left, split_right):
    cell = layout.create_cell(name)
    cell.shapes(active).insert(pya.Box(-500, -100, split_left, 100))
    cell.shapes(active).insert(pya.Box(split_right, -100, 500, 100))


donut("DONUT_CENTER", -100)
donut("DONUT_SHIFTED", 100)
two_components("TERMINALS_BALANCED", -100, 100)
two_components("TERMINALS_UNBALANCED", -200, 0)
connected = layout.create_cell("TERMINALS_CONNECTED")
connected.shapes(active).insert(pya.Box(-500, -100, 500, 100))
layout.write(str(output_path))
