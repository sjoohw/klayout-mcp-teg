"""KLayout PCell declaration and library registration for DUT transistor arrays."""

from __future__ import annotations

import inspect
from typing import Any, Mapping

try:
    import pya
except ImportError:
    pya = None  # type: ignore

from .dut_geometry import (
    DutGeometryResult,
    DutParameters,
    _default_device_window,
    _default_routing_boundary,
    build_dut_geometry,
    build_terminal_contract,
)
from .design_contract import validate_orthogonal_m1_shapes
from .errors import AnalysisError
from .geometry import Box, Point
from .selection import _balanced_target_points, _unit_centers, select_routed_units


PCELL_NAME = "DutTransistorArray"
LIBRARY_NAME = "TEG_DUT_LIB"


def _standalone_geometry_core_source() -> str:
    """Serialize the canonical pure geometry implementation into the PCell script."""

    definitions = (
        AnalysisError,
        Point,
        Box,
        _unit_centers,
        _balanced_target_points,
        select_routed_units,
        _default_device_window,
        _default_routing_boundary,
        validate_orthogonal_m1_shapes,
        DutParameters,
        build_terminal_contract,
        DutGeometryResult,
        build_dut_geometry,
    )
    return "\n\n".join(inspect.getsource(item).strip() for item in definitions)


def generate_pcell_python_source(layermap: Mapping[str, Any]) -> str:
    """Generate standalone, copy-pasteable Python PCell script for KLayout pymacros."""

    required_roles = ("m1", "active", "poly", "contact")
    missing_roles = [
        role
        for role in required_roles
        if role not in layermap or not hasattr(layermap[role], "layer")
    ]
    if missing_roles:
        raise AnalysisError(
            code="PCELL_LAYERMAP_INCOMPLETE",
            message="PCell export requires explicit M1, Active, Poly, and Contact layers.",
            details={"missing_layer_roles": missing_roles},
            next_action=(
                "Add explicit layer/datatype pairs for m1, active, poly, and contact."
            ),
        )

    layer_values = {
        role: (int(layermap[role].layer), int(layermap[role].datatype))
        for role in required_roles
    }
    roles_by_layer: dict[tuple[int, int], list[str]] = {}
    for role, value in layer_values.items():
        roles_by_layer.setdefault(value, []).append(role)
    collisions = [
        {"layer": layer, "datatype": datatype, "roles": sorted(roles)}
        for (layer, datatype), roles in sorted(roles_by_layer.items())
        if len(roles) > 1
    ]
    if collisions:
        raise AnalysisError(
            code="PCELL_LAYERMAP_COLLISION",
            message="PCell geometry roles must use distinct layer/datatype pairs.",
            details={"collisions": collisions},
            next_action="Assign a distinct explicit layer/datatype pair to each role.",
        )

    layer_m1, dt_m1 = layer_values["m1"]
    layer_active, dt_active = layer_values["active"]
    layer_poly, dt_poly = layer_values["poly"]
    layer_contact, dt_contact = layer_values["contact"]

    geometry_core_source = _standalone_geometry_core_source()

    return f'''# $autorun
# NON-PRODUCTION CONCEPTUAL PCELL. DO NOT USE AS A FABRICATION MASK.
# Process geometry and electrical connectivity are not verified.
# M1 routing is horizontal/vertical only; diagonal routing is forbidden.
# W/L below are device-specific transistor parameters, not generic short/long axes.
# KLayout Python PCell Library for TEG DUT Transistor Arrays
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import pya

PRODUCTION_READY = False
DUT_PCELL_CONTRACT_VERSION = 2
DIMENSION_SEMANTICS = "device_specific_w_l"
ROUTING_STYLE = "orthogonal_only"
DIAGONAL_ROUTING_ALLOWED = False
PARASITIC_RESISTANCE_OPTIMIZED = False

{geometry_core_source}

class DutTransistorArrayPCell(pya.PCellDeclarationHelper):
    """Parameterized DUT Transistor Array with 4-terminal landing contract."""

    def __init__(self):
        super(DutTransistorArrayPCell, self).__init__()
        self.param("w_um", self.TypeDouble, "Transistor Width (um)", default=1.0)
        self.param("l_um", self.TypeDouble, "Gate Length (um)", default=0.1)
        self.param("array_rows", self.TypeInt, "Array Rows", default=4)
        self.param("array_cols", self.TypeInt, "Array Columns", default=8)
        self.param("pitch_x_um", self.TypeDouble, "Pitch X (um)", default=2.0)
        self.param("pitch_y_um", self.TypeDouble, "Pitch Y (um)", default=2.0)
        self.param("routed_device_count", self.TypeInt, "Routed Tr Count", default=10)
        self.param("m1_width_um", self.TypeDouble, "M1 Route Width (um)", default=0.4)
        self.param("m1_overlap_um", self.TypeDouble, "Landing Overlap (um)", default=0.2)

        self.param("l_m1", self.TypeLayer, "M1 Layer", default=pya.LayerInfo({layer_m1}, {dt_m1}))
        self.param("l_active", self.TypeLayer, "Active Layer", default=pya.LayerInfo({layer_active}, {dt_active}))
        self.param("l_poly", self.TypeLayer, "Poly Layer", default=pya.LayerInfo({layer_poly}, {dt_poly}))
        self.param("l_contact", self.TypeLayer, "Contact Layer", default=pya.LayerInfo({layer_contact}, {dt_contact}))

    def display_text_impl(self):
        return f"DutArray(W={{self.w_um}}, L={{self.l_um}}, Rows={{self.array_rows}}, Cols={{self.array_cols}})"

    def coerce_parameters_impl(self):
        try:
            DutParameters(
                w_um=self.w_um,
                l_um=self.l_um,
                array_rows=self.array_rows,
                array_cols=self.array_cols,
                pitch_x_um=self.pitch_x_um,
                pitch_y_um=self.pitch_y_um,
                routed_device_count=self.routed_device_count,
                m1_width_um=self.m1_width_um,
                m1_overlap_um=self.m1_overlap_um,
            ).validate()
        except AnalysisError as exc:
            raise ValueError(f"{{exc.code}}: {{exc.message}}") from exc

    def produce_impl(self):
        geometry = build_dut_geometry(
            DutParameters(
                w_um=self.w_um,
                l_um=self.l_um,
                array_rows=self.array_rows,
                array_cols=self.array_cols,
                pitch_x_um=self.pitch_x_um,
                pitch_y_um=self.pitch_y_um,
                routed_device_count=self.routed_device_count,
                m1_width_um=self.m1_width_um,
                m1_overlap_um=self.m1_overlap_um,
            ),
            dbu_um=self.layout.dbu,
        )

        for box_um in geometry.active_boxes_um:
            self.cell.shapes(self.l_active_layer).insert(pya.DBox(*box_um))
        for box_um in geometry.poly_boxes_um:
            self.cell.shapes(self.l_poly_layer).insert(pya.DBox(*box_um))
        for box_um in geometry.contact_boxes_um:
            self.cell.shapes(self.l_contact_layer).insert(pya.DBox(*box_um))
        for shape in geometry.m1_shapes_um:
            self.cell.shapes(self.l_m1_layer).insert(pya.DBox(*shape["bbox_um"]))


class TegPcellLibrary(pya.Library):
    """KLayout Library for TEG DUT PCells."""

    def __init__(self):
        super(TegPcellLibrary, self).__init__()
        self.description = "Parameterized TEG DUT Transistor Array Library"
        self.layout().register_pcell("{PCELL_NAME}", DutTransistorArrayPCell())
        self.register("{LIBRARY_NAME}")


# Auto-register library when script is loaded
TegPcellLibrary()
'''


def register_teg_library(layers: Mapping[str, tuple[int, int]]) -> bool:
    """Register the live library with explicit non-colliding layer/datatype pairs."""

    if pya is None:
        return False

    required_roles = ("m1", "active", "poly", "contact")
    invalid_roles: list[str] = []
    layer_values: dict[str, tuple[int, int]] = {}
    for role in required_roles:
        value = layers.get(role)
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
            or int(value[0]) < 0
            or int(value[1]) < 0
        ):
            invalid_roles.append(role)
            continue
        layer_values[role] = (int(value[0]), int(value[1]))
    if invalid_roles:
        raise AnalysisError(
            code="PCELL_LAYERMAP_INCOMPLETE",
            message="Live PCell registration requires explicit layer/datatype pairs.",
            details={"invalid_or_missing_layer_roles": invalid_roles},
            next_action="Provide m1, active, poly, and contact as [layer, datatype].",
        )
    if len(set(layer_values.values())) != len(layer_values):
        raise AnalysisError(
            code="PCELL_LAYERMAP_COLLISION",
            message="Live PCell geometry roles must use distinct layer/datatype pairs.",
            details={"layers": layer_values},
            next_action="Assign a distinct explicit layer/datatype pair to every role.",
        )
    layer_infos = {
        role: pya.LayerInfo(layer, datatype)
        for role, (layer, datatype) in layer_values.items()
    }

    class DutTransistorArrayPCell(pya.PCellDeclarationHelper):
        def __init__(self):
            super().__init__()
            self.param("w_um", self.TypeDouble, "Transistor Width (um)", default=1.0)
            self.param("l_um", self.TypeDouble, "Gate Length (um)", default=0.1)
            self.param("array_rows", self.TypeInt, "Array Rows", default=4)
            self.param("array_cols", self.TypeInt, "Array Columns", default=8)
            self.param("pitch_x_um", self.TypeDouble, "Pitch X (um)", default=2.0)
            self.param("pitch_y_um", self.TypeDouble, "Pitch Y (um)", default=2.0)
            self.param("routed_device_count", self.TypeInt, "Routed Tr Count", default=10)
            self.param("m1_width_um", self.TypeDouble, "M1 Route Width (um)", default=0.4)
            self.param("m1_overlap_um", self.TypeDouble, "Landing Overlap (um)", default=0.2)
            self.param("l_m1", self.TypeLayer, "M1 Layer", default=layer_infos["m1"])
            self.param(
                "l_active", self.TypeLayer, "Active Layer", default=layer_infos["active"]
            )
            self.param("l_poly", self.TypeLayer, "Poly Layer", default=layer_infos["poly"])
            self.param(
                "l_contact", self.TypeLayer, "Contact Layer", default=layer_infos["contact"]
            )

        def display_text_impl(self):
            return f"DutArray(W={self.w_um}, L={self.l_um})"

        def coerce_parameters_impl(self):
            try:
                DutParameters(
                    w_um=self.w_um,
                    l_um=self.l_um,
                    array_rows=self.array_rows,
                    array_cols=self.array_cols,
                    pitch_x_um=self.pitch_x_um,
                    pitch_y_um=self.pitch_y_um,
                    routed_device_count=self.routed_device_count,
                    m1_width_um=self.m1_width_um,
                    m1_overlap_um=self.m1_overlap_um,
                ).validate()
            except AnalysisError as exc:
                raise ValueError(f"{exc.code}: {exc.message}") from exc

        def produce_impl(self):
            geometry = build_dut_geometry(
                DutParameters(
                    w_um=self.w_um,
                    l_um=self.l_um,
                    array_rows=self.array_rows,
                    array_cols=self.array_cols,
                    pitch_x_um=self.pitch_x_um,
                    pitch_y_um=self.pitch_y_um,
                    routed_device_count=self.routed_device_count,
                    m1_width_um=self.m1_width_um,
                    m1_overlap_um=self.m1_overlap_um,
                ),
                dbu_um=self.layout.dbu,
            )

            for box_um in geometry.active_boxes_um:
                self.cell.shapes(self.l_active_layer).insert(pya.DBox(*box_um))
            for box_um in geometry.poly_boxes_um:
                self.cell.shapes(self.l_poly_layer).insert(pya.DBox(*box_um))
            for box_um in geometry.contact_boxes_um:
                self.cell.shapes(self.l_contact_layer).insert(pya.DBox(*box_um))
            for shape in geometry.m1_shapes_um:
                self.cell.shapes(self.l_m1_layer).insert(pya.DBox(*shape["bbox_um"]))

    class LiveTegPcellLibrary(pya.Library):
        def __init__(self):
            super().__init__()
            self.description = "Parameterized TEG DUT Transistor Array Library"
            self.layout().register_pcell(PCELL_NAME, DutTransistorArrayPCell())
            self.register(LIBRARY_NAME)

    LiveTegPcellLibrary()
    return True
