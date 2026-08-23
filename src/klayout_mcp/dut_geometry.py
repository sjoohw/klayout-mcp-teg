"""Deterministic geometry generator and parameter schema for DUT transistor arrays."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass, field
import math
from typing import Any, Sequence

from .errors import AnalysisError
from .geometry import Box, Point
from .selection import select_routed_units


DUT_PCELL_CONTRACT_VERSION = 1


def _default_device_window() -> Box:
    return Box(-17.5, -20.0, 17.5, 20.0)


def _default_routing_boundary() -> Box:
    return Box(-20.0, -20.0, 20.0, 20.0)


@dataclass(frozen=True, slots=True)
class DutParameters:
    """Validated parameter set for a single DUT transistor array instance."""

    w_um: float = 1.0
    l_um: float = 0.1
    array_rows: int = 4
    array_cols: int = 8
    pitch_x_um: float = 2.0
    pitch_y_um: float = 2.0
    routed_device_count: int = 10
    m1_width_um: float = 0.4
    m1_overlap_um: float = 0.2
    device_window_um: Box | Sequence[float] = field(default_factory=_default_device_window)
    routing_boundary_um: Box | Sequence[float] = field(default_factory=_default_routing_boundary)

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_window_um", Box.from_sequence(self.device_window_um))
        object.__setattr__(
            self,
            "routing_boundary_um",
            Box.from_sequence(self.routing_boundary_um),
        )

    def validate(self) -> None:
        numeric_values = {
            "w_um": self.w_um,
            "l_um": self.l_um,
            "pitch_x_um": self.pitch_x_um,
            "pitch_y_um": self.pitch_y_um,
            "m1_width_um": self.m1_width_um,
            "m1_overlap_um": self.m1_overlap_um,
        }
        invalid_numeric = {
            name: value
            for name, value in numeric_values.items()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        }
        if invalid_numeric:
            raise AnalysisError(
                code="INVALID_DUT_PARAMETER",
                message="DUT dimensions and routing values must be finite numbers.",
                details={"invalid_parameters": invalid_numeric},
                next_action="Replace boolean, text, NaN, or infinite values with finite micron values.",
            )

        integer_values = {
            "array_rows": self.array_rows,
            "array_cols": self.array_cols,
            "routed_device_count": self.routed_device_count,
        }
        invalid_integers = {
            name: value
            for name, value in integer_values.items()
            if isinstance(value, bool) or not isinstance(value, int)
        }
        if invalid_integers:
            raise AnalysisError(
                code="INVALID_DUT_INTEGER_PARAMETER",
                message="Array dimensions and routed_device_count must be integers.",
                details={"invalid_parameters": invalid_integers},
                next_action="Provide whole-number array_rows, array_cols, and routed_device_count values.",
            )

        if self.w_um <= 0 or self.l_um <= 0:
            raise AnalysisError(
                code="INVALID_DUT_DIMENSIONS",
                message="Transistor width (w_um) and length (l_um) must be positive.",
                details={"w_um": self.w_um, "l_um": self.l_um},
                next_action="Provide positive w_um and l_um values.",
            )
        if self.array_rows < 1 or self.array_cols < 1:
            raise AnalysisError(
                code="INVALID_ARRAY_SIZE",
                message="Array rows and columns must be at least 1.",
                details={"rows": self.array_rows, "cols": self.array_cols},
                next_action="Provide positive integer array dimensions.",
            )
        total_units = self.array_rows * self.array_cols
        if not 1 <= self.routed_device_count <= total_units:
            raise AnalysisError(
                code="INVALID_ROUTED_COUNT",
                message=(
                    f"routed_device_count ({self.routed_device_count}) must be between 1 "
                    f"and total units ({total_units})."
                ),
                details={
                    "routed_device_count": self.routed_device_count,
                    "total_units": total_units,
                },
                next_action=f"Set routed_device_count to an integer between 1 and {total_units}.",
            )
        if self.pitch_x_um <= 0 or self.pitch_y_um <= 0:
            raise AnalysisError(
                code="INVALID_PITCH",
                message="Transistor pitches must be positive.",
                details={"pitch_x_um": self.pitch_x_um, "pitch_y_um": self.pitch_y_um},
                next_action="Provide positive pitch_x_um and pitch_y_um values in microns.",
            )
        if self.m1_width_um <= 0 or self.m1_overlap_um <= 0:
            raise AnalysisError(
                code="INVALID_ROUTING_PARAMETERS",
                message="m1_width_um and m1_overlap_um must be positive.",
                details={"m1_width_um": self.m1_width_um, "m1_overlap_um": self.m1_overlap_um},
                next_action="Provide positive M1 width and landing overlap values; point contact is not allowed.",
            )

        device_window = self.device_window_um
        routing_boundary = self.routing_boundary_um
        if (
            device_window.x1 < routing_boundary.x1
            or device_window.y1 < routing_boundary.y1
            or device_window.x2 > routing_boundary.x2
            or device_window.y2 > routing_boundary.y2
        ):
            raise AnalysisError(
                code="DEVICE_WINDOW_OUTSIDE_ROUTING_BOUNDARY",
                message="The device window must be contained by the routing boundary.",
                details={
                    "device_window_um": device_window.to_list(),
                    "routing_boundary_um": routing_boundary.to_list(),
                },
                next_action="Increase the routing boundary or reduce the device window.",
            )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["device_window_um"] = self.device_window_um.to_list()
        result["routing_boundary_um"] = self.routing_boundary_um.to_list()
        return result


def build_terminal_contract(params: DutParameters) -> dict[str, dict[str, Any]]:
    """Return the four DUT-local M1 landing contracts without inferring layer numbers."""

    params.validate()
    boundary = params.routing_boundary_um
    half_width = params.m1_width_um / 2.0
    overlap = params.m1_overlap_um
    common = {
        "layer_role": "m1",
        "route_width_um": params.m1_width_um,
        "minimum_overlap_um": overlap,
    }
    return {
        "source": {
            **common,
            "name": "S",
            "net_role": "source",
            "boundary_side": "left",
            "direction_vector": [-1, 0],
            "anchor_um": [boundary.x1, 0.0],
            "landing_bbox_um": [boundary.x1 - overlap, -half_width, boundary.x1, half_width],
        },
        "drain": {
            **common,
            "name": "D",
            "net_role": "drain",
            "boundary_side": "right",
            "direction_vector": [1, 0],
            "anchor_um": [boundary.x2, 0.0],
            "landing_bbox_um": [boundary.x2, -half_width, boundary.x2 + overlap, half_width],
        },
        "gate": {
            **common,
            "name": "G",
            "net_role": "gate",
            "boundary_side": "top",
            "direction_vector": [0, 1],
            "anchor_um": [0.0, boundary.y2],
            "landing_bbox_um": [-half_width, boundary.y2, half_width, boundary.y2 + overlap],
        },
        "body": {
            **common,
            "name": "B",
            "net_role": "body",
            "boundary_side": "bottom",
            "direction_vector": [0, -1],
            "anchor_um": [0.0, boundary.y1],
            "landing_bbox_um": [-half_width, boundary.y1 - overlap, half_width, boundary.y1],
        },
    }


def describe_dut_pcell_contract() -> dict[str, Any]:
    """Describe the implemented abstract DUT contract and unresolved production inputs."""

    defaults = DutParameters()
    return {
        "ok": True,
        "contract_version": DUT_PCELL_CONTRACT_VERSION,
        "pcell_name": "DutTransistorArray",
        "coordinate_system": {
            "unit": "um",
            "origin": "DUT slot center",
            "x_positive": "right",
            "y_positive": "top",
        },
        "parameter_schema": [
            {"name": "w_um", "type": "float", "unit": "um", "default": defaults.w_um, "exclusive_minimum": 0.0},
            {"name": "l_um", "type": "float", "unit": "um", "default": defaults.l_um, "exclusive_minimum": 0.0},
            {"name": "array_rows", "type": "integer", "default": defaults.array_rows, "minimum": 1},
            {"name": "array_cols", "type": "integer", "default": defaults.array_cols, "minimum": 1},
            {"name": "pitch_x_um", "type": "float", "unit": "um", "default": defaults.pitch_x_um, "exclusive_minimum": 0.0},
            {"name": "pitch_y_um", "type": "float", "unit": "um", "default": defaults.pitch_y_um, "exclusive_minimum": 0.0},
            {"name": "routed_device_count", "type": "integer", "default": defaults.routed_device_count, "minimum": 1},
            {"name": "m1_width_um", "type": "float", "unit": "um", "default": defaults.m1_width_um, "exclusive_minimum": 0.0},
            {"name": "m1_overlap_um", "type": "float", "unit": "um", "default": defaults.m1_overlap_um, "exclusive_minimum": 0.0},
            {"name": "device_window_um", "type": "box", "unit": "um", "default": defaults.device_window_um.to_list()},
            {"name": "routing_boundary_um", "type": "box", "unit": "um", "default": defaults.routing_boundary_um.to_list()},
        ],
        "terminals": build_terminal_contract(defaults),
        "production_ready": False,
        "geometry_status": "conceptual_scaffold",
        "required_production_inputs": [
            "padset GDS/OAS",
            "layermap with explicit layer/datatype pairs",
            "sample DUT GDS/OAS and parameter explanation",
        ],
        "unresolved_from_current_workspace": [
            "device type and process-specific transistor geometry",
            "active/poly/contact layer identities",
            "local S/D/G/B bus topology and design-rule dimensions",
            "sweep parameter meaning and 21-site values",
        ],
        "next_action": (
            "Provide a sample DUT GDS/OAS and its parameter explanation before generating "
            "a production PCell or static DUT layout."
        ),
    }


@dataclass(slots=True)
class DutGeometryResult:
    """Structured geometry and terminal metadata generated for a DUT variant."""

    parameters: DutParameters
    total_units: int
    routed_indices: list[int]
    device_bbox_um: list[float]
    unit_centers_um: list[list[float]]
    active_boxes_um: list[list[float]]
    poly_boxes_um: list[list[float]]
    contact_boxes_um: list[list[float]]
    m1_shapes_um: list[dict[str, Any]]
    terminals: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "contract_version": DUT_PCELL_CONTRACT_VERSION,
            "production_ready": False,
            "geometry_status": "conceptual_scaffold",
            "electrical_connectivity_verified": False,
            "process_geometry_verified": False,
            "warning": (
                "Process layers and verified internal S/D/G/B connectivity are unresolved; "
                "do not use this scaffold as a production layout."
            ),
            "parameters": self.parameters.to_dict(),
            "total_units": self.total_units,
            "routed_count": len(self.routed_indices),
            "routed_indices": self.routed_indices,
            "device_bbox_um": self.device_bbox_um,
            "unit_centers_um": self.unit_centers_um,
            "shape_counts": {
                "active": len(self.active_boxes_um),
                "poly": len(self.poly_boxes_um),
                "contact": len(self.contact_boxes_um),
                "m1": len(self.m1_shapes_um),
            },
            "active_boxes_um": self.active_boxes_um,
            "poly_boxes_um": self.poly_boxes_um,
            "contact_boxes_um": self.contact_boxes_um,
            "m1_shapes_um": self.m1_shapes_um,
            "terminals": self.terminals,
        }


def build_dut_geometry(params: DutParameters | None = None) -> DutGeometryResult:
    """Deterministic, pure-geometry generation for a parameterized DUT transistor array.

    Local coordinate origin (0, 0) is at the DUT slot center.
    Terminal landing stubs extend to:
      - Source: Left boundary (x = routing_boundary.x1)
      - Drain: Right boundary (x = routing_boundary.x2)
      - Gate: Top boundary (y = routing_boundary.y2)
      - Body: Bottom boundary (y = routing_boundary.y1)
    """

    cfg = params or DutParameters()
    cfg.validate()

    # 1. Calculate array unit centers centered at local (0, 0)
    cols, rows = cfg.array_cols, cfg.array_rows
    total_width = (cols - 1) * cfg.pitch_x_um
    total_height = (rows - 1) * cfg.pitch_y_um
    start_x = -total_width / 2.0
    start_y = -total_height / 2.0

    unit_centers: list[Point] = []
    unit_centers_raw: list[list[float]] = []
    for r in range(rows):
        for c in range(cols):
            cx = start_x + c * cfg.pitch_x_um
            cy = start_y + r * cfg.pitch_y_um
            unit_centers.append(Point(cx, cy))
            unit_centers_raw.append([cx, cy])

    # 2. Check that unit array bounding box fits within device_window
    half_w = cfg.w_um / 2.0
    half_l = cfg.l_um / 2.0
    min_x = min(p.x - max(half_w, half_l, 0.5) for p in unit_centers)
    max_x = max(p.x + max(half_w, half_l, 0.5) for p in unit_centers)
    min_y = min(p.y - max(half_w, half_l, 0.5) for p in unit_centers)
    max_y = max(p.y + max(half_w, half_l, 0.5) for p in unit_centers)
    array_bbox = Box(min_x, min_y, max_x, max_y)

    dev_win = cfg.device_window_um
    if (
        array_bbox.x1 < dev_win.x1 - 1e-4
        or array_bbox.x2 > dev_win.x2 + 1e-4
        or array_bbox.y1 < dev_win.y1 - 1e-4
        or array_bbox.y2 > dev_win.y2 + 1e-4
    ):
        raise AnalysisError(
            code="DEVICE_EXCEEDS_WINDOW",
            message="Transistor array geometry exceeds the specified device window.",
            details={
                "array_bbox_um": array_bbox.to_list(),
                "device_window_um": dev_win.to_list(),
            },
            next_action="Reduce array_rows/array_cols/pitch or increase device_window.",
        )

    # 3. Deterministic selection of routed units
    selection_res = select_routed_units(
        unit_centers_um=unit_centers_raw,
        device_window_um=dev_win.to_list(),
        routed_device_count=cfg.routed_device_count,
        edge_inset_um=5.0,
    )
    routed_indices: list[int] = list(selection_res["selected_unit_indices"])
    routed_indices_set = set(routed_indices)


    # 4. Generate device layers (Active, Poly, Contacts)
    active_boxes: list[list[float]] = []
    poly_boxes: list[list[float]] = []
    contact_boxes: list[list[float]] = []
    m1_shapes: list[dict[str, Any]] = []

    contact_size = 0.22
    contact_half = contact_size / 2.0
    active_w = max(cfg.w_um, 0.4)
    active_h = max(cfg.l_um + 0.8, 1.0)
    poly_w = cfg.l_um
    poly_h = active_w + 0.4

    for idx, center in enumerate(unit_centers, start=1):
        cx, cy = center.x, center.y
        # Active diffusion box
        act_box = [cx - active_h / 2.0, cy - active_w / 2.0, cx + active_h / 2.0, cy + active_w / 2.0]
        active_boxes.append(act_box)

        # Poly Gate vertical stripe
        ply_box = [cx - poly_w / 2.0, cy - poly_h / 2.0, cx + poly_w / 2.0, cy + poly_h / 2.0]
        poly_boxes.append(ply_box)

        # Contacts and local M1 taps for Source (left) and Drain (right)
        s_cx = cx - (active_h / 2.0 - contact_half - 0.05)
        d_cx = cx + (active_h / 2.0 - contact_half - 0.05)
        s_contact = [s_cx - contact_half, cy - contact_half, s_cx + contact_half, cy + contact_half]
        d_contact = [d_cx - contact_half, cy - contact_half, d_cx + contact_half, cy + contact_half]
        contact_boxes.extend([s_contact, d_contact])

        # Local M1 taps for selected routed transistors
        if idx in routed_indices_set:
            m1_tap_w = min(cfg.m1_width_um, contact_size + 0.08)
            tap_half = m1_tap_w / 2.0
            # Local S/D landing pads
            m1_shapes.append({
                "net": "source",
                "unit": idx,
                "type": "box",
                "bbox_um": [s_cx - tap_half, cy - tap_half, s_cx + tap_half, cy + tap_half],
            })
            m1_shapes.append({
                "net": "drain",
                "unit": idx,
                "type": "box",
                "bbox_um": [d_cx - tap_half, cy - tap_half, d_cx + tap_half, cy + tap_half],
            })


    # 5. Build S/D/G/B M1 routing trunks extending to boundaries
    rout_bound = cfg.routing_boundary_um
    m1_w = cfg.m1_width_um
    half_m1 = m1_w / 2.0

    # Source trunk (left): vertical collector + horizontal landing stub to left boundary
    s_trunk_x = min(p.x for p in unit_centers) - 1.0
    m1_shapes.append({
        "net": "source",
        "type": "box",
        "name": "source_collector",
        "bbox_um": [s_trunk_x - half_m1, array_bbox.y1, s_trunk_x + half_m1, array_bbox.y2],
    })
    m1_shapes.append({
        "net": "source",
        "type": "box",
        "name": "source_landing_stub",
        "bbox_um": [rout_bound.x1 - cfg.m1_overlap_um, -half_m1, s_trunk_x + half_m1, half_m1],
    })

    # Drain trunk (right): vertical collector + horizontal landing stub to right boundary
    d_trunk_x = max(p.x for p in unit_centers) + 1.0
    m1_shapes.append({
        "net": "drain",
        "type": "box",
        "name": "drain_collector",
        "bbox_um": [d_trunk_x - half_m1, array_bbox.y1, d_trunk_x + half_m1, array_bbox.y2],
    })
    m1_shapes.append({
        "net": "drain",
        "type": "box",
        "name": "drain_landing_stub",
        "bbox_um": [d_trunk_x - half_m1, -half_m1, rout_bound.x2 + cfg.m1_overlap_um, half_m1],
    })

    # Gate trunk (top): horizontal collector + vertical landing stub to top boundary
    g_trunk_y = min(array_bbox.y2 + 0.8, rout_bound.y2 - half_m1)
    m1_shapes.append({
        "net": "gate",
        "type": "box",
        "name": "gate_collector",
        "bbox_um": [array_bbox.x1, g_trunk_y - half_m1, array_bbox.x2, g_trunk_y + half_m1],
    })
    m1_shapes.append({
        "net": "gate",
        "type": "box",
        "name": "gate_landing_stub",
        "bbox_um": [-half_m1, g_trunk_y - half_m1, half_m1, rout_bound.y2 + cfg.m1_overlap_um],
    })

    # Body trunk (bottom): horizontal collector + vertical landing stub to bottom boundary
    b_trunk_y = max(array_bbox.y1 - 0.8, rout_bound.y1 + half_m1)
    m1_shapes.append({
        "net": "body",
        "type": "box",
        "name": "body_collector",
        "bbox_um": [array_bbox.x1, b_trunk_y - half_m1, array_bbox.x2, b_trunk_y + half_m1],
    })
    m1_shapes.append({
        "net": "body",
        "type": "box",
        "name": "body_landing_stub",
        "bbox_um": [-half_m1, rout_bound.y1 - cfg.m1_overlap_um, half_m1, b_trunk_y + half_m1],
    })

    # 6. Structured terminal metadata contract
    terminals = build_terminal_contract(cfg)

    return DutGeometryResult(
        parameters=cfg,
        total_units=len(unit_centers),
        routed_indices=routed_indices,
        device_bbox_um=array_bbox.to_list(),
        unit_centers_um=unit_centers_raw,
        active_boxes_um=active_boxes,
        poly_boxes_um=poly_boxes,
        contact_boxes_um=contact_boxes,
        m1_shapes_um=m1_shapes,
        terminals=terminals,
    )
