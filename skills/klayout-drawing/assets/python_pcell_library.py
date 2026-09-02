"""Process-neutral KLayout Python PCell template."""

import pya


def linear_taper_polygon(length_um, width_in_um, width_out_um):
    return pya.DPolygon(
        [
            pya.DPoint(0.0, -width_in_um / 2.0),
            pya.DPoint(length_um, -width_out_um / 2.0),
            pya.DPoint(length_um, width_out_um / 2.0),
            pya.DPoint(0.0, width_in_um / 2.0),
        ]
    )


class LinearTaperPCell(pya.PCellDeclarationHelper):
    def __init__(self):
        super().__init__()
        self.param(
            "drawing_layer",
            self.TypeLayer,
            "Drawing layer",
            default=pya.LayerInfo(1, 0),
        )
        self.param(
            "length_um",
            self.TypeDouble,
            "Length",
            default=20.0,
            unit="um",
            min_value=0.001,
        )
        self.param(
            "width_in_um",
            self.TypeDouble,
            "Input width",
            default=1.0,
            unit="um",
            min_value=0.001,
        )
        self.param(
            "width_out_um",
            self.TypeDouble,
            "Output width",
            default=4.0,
            unit="um",
            min_value=0.001,
        )

    def coerce_parameters_impl(self):
        minimum = float(self.layout.dbu)
        self.length_um = max(float(self.length_um), minimum)
        self.width_in_um = max(float(self.width_in_um), minimum)
        self.width_out_um = max(float(self.width_out_um), minimum)

    def display_text_impl(self):
        return (
            f"LinearTaper(L={self.length_um:.3f},"
            f"Win={self.width_in_um:.3f},Wout={self.width_out_um:.3f})"
        )

    def produce_impl(self):
        polygon = linear_taper_polygon(
            float(self.length_um),
            float(self.width_in_um),
            float(self.width_out_um),
        )
        self.cell.shapes(self.drawing_layer_layer).insert(polygon)


class KLayoutDrawingLibrary(pya.Library):
    def __init__(self):
        super().__init__()
        self.description = "Process-neutral deterministic drawing PCells"
        self.layout().register_pcell("LinearTaper", LinearTaperPCell())
        self.register("KLayoutDrawing")


KLayoutDrawingLibrary()
