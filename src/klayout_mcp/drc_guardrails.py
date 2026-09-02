"""Design rule checking and connectivity guardrails for DUT geometries and M1 routing."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Sequence

from .errors import AnalysisError
from .geometry import Box, Point


@dataclass(frozen=True, slots=True)
class DesignRuleConfig:
    """Configurable Key Design Rules for M1 and device geometries."""

    min_m1_width_um: float = 0.28
    min_m1_space_um: float = 0.28
    min_landing_overlap_um: float = 0.1
    min_poly_width_um: float = 0.08
    min_contact_size_um: float = 0.18
    comparison_tolerance_um: float = 1e-9

    def validate(self) -> None:
        rules = {
            "min_m1_width_um": self.min_m1_width_um,
            "min_m1_space_um": self.min_m1_space_um,
            "min_landing_overlap_um": self.min_landing_overlap_um,
            "min_poly_width_um": self.min_poly_width_um,
            "min_contact_size_um": self.min_contact_size_um,
        }
        invalid = {
            key: value
            for key, value in rules.items()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        }
        tolerance = self.comparison_tolerance_um
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(float(tolerance))
            or tolerance < 0
        ):
            invalid["comparison_tolerance_um"] = tolerance
        if invalid:
            raise AnalysisError(
                code="INVALID_DESIGN_RULES",
                message="Design rules must be finite; thresholds must be positive.",
                details=invalid,
                next_action=(
                    "Provide finite positive rule dimensions and a finite non-negative "
                    "comparison tolerance in microns."
                ),
            )


def check_box_design_rules(
    m1_shapes: Sequence[dict[str, Any]],
    min_width_um: float,
    min_space_um: float,
    rule_name: str = "M1",
    tolerance_um: float = 1e-9,
) -> list[dict[str, Any]]:
    """Check width and spacing constraints among shapes, respecting electrical nets."""

    violations: list[dict[str, Any]] = []
    parsed_shapes: list[tuple[Box, str, int]] = []

    for idx, item in enumerate(m1_shapes, start=1):
        raw = item.get("bbox_um") if isinstance(item, dict) else item
        net = item.get("net", f"net_{idx}") if isinstance(item, dict) else f"net_{idx}"
        box = Box.from_sequence(raw)
        parsed_shapes.append((box, net, idx))

        # 1. Min width / height check
        if box.width < min_width_um - tolerance_um:
            violations.append({
                "rule": f"{rule_name}_MIN_WIDTH",
                "box_index": idx,
                "box_um": box.to_list(),
                "actual_width_um": box.width,
                "min_required_um": min_width_um,
                "violation_type": "width",
            })
        if box.height < min_width_um - tolerance_um:
            violations.append({
                "rule": f"{rule_name}_MIN_WIDTH",
                "box_index": idx,
                "box_um": box.to_list(),
                "actual_height_um": box.height,
                "min_required_um": min_width_um,
                "violation_type": "height",
            })

    # 2. Pairwise space check between different nets
    count = len(parsed_shapes)
    for i in range(count):
        b1, net1, idx1 = parsed_shapes[i]
        for j in range(i + 1, count):
            b2, net2, idx2 = parsed_shapes[j]

            # Same net shapes are intentionally joined/routed together
            if net1 == net2:
                continue

            # Calculate gap in X and Y
            dx = max(0.0, max(b1.x1 - b2.x2, b2.x1 - b1.x2))
            dy = max(0.0, max(b1.y1 - b2.y2, b2.y1 - b1.y2))

            # If different net boxes overlap or touch, it is a short
            if dx <= tolerance_um and dy <= tolerance_um:
                violations.append({
                    "rule": f"{rule_name}_SHORT_DETECTED",
                    "nets": [net1, net2],
                    "boxes": [idx1, idx2],
                    "box1_um": b1.to_list(),
                    "box2_um": b2.to_list(),
                })
                continue

            # Pure orthogonal clearance
            actual_space = math.hypot(dx, dy)
            if dx <= tolerance_um and dy > tolerance_um and actual_space < min_space_um - tolerance_um:
                violations.append({
                    "rule": f"{rule_name}_MIN_SPACE",
                    "nets": [net1, net2],
                    "boxes": [idx1, idx2],
                    "box1_um": b1.to_list(),
                    "box2_um": b2.to_list(),
                    "actual_space_um": actual_space,
                    "min_required_um": min_space_um,
                    "violation_axis": "Y",
                })
            elif dy <= tolerance_um and dx > tolerance_um and actual_space < min_space_um - tolerance_um:
                violations.append({
                    "rule": f"{rule_name}_MIN_SPACE",
                    "nets": [net1, net2],
                    "boxes": [idx1, idx2],
                    "box1_um": b1.to_list(),
                    "box2_um": b2.to_list(),
                    "actual_space_um": actual_space,
                    "min_required_um": min_space_um,
                    "violation_axis": "X",
                })
            elif dx > tolerance_um and dy > tolerance_um and actual_space < min_space_um - tolerance_um:
                violations.append({
                    "rule": f"{rule_name}_MIN_SPACE",
                    "nets": [net1, net2],
                    "boxes": [idx1, idx2],
                    "box1_um": b1.to_list(),
                    "box2_um": b2.to_list(),
                    "actual_space_um": actual_space,
                    "min_required_um": min_space_um,
                    "violation_axis": "DIAGONAL",
                })

    return violations


def analyze_m1_connectivity(
    m1_shapes: Sequence[dict[str, Any]],
    *,
    tolerance_um: float = 1e-9,
) -> dict[str, Any]:
    """Return box-based per-net connected components without claiming LVS."""

    parsed_by_net: dict[str, list[tuple[int, Box]]] = {}
    for index, item in enumerate(m1_shapes, start=1):
        if not isinstance(item, dict):
            raise AnalysisError(
                code="INVALID_M1_SHAPE",
                message="M1 connectivity shapes must include bbox_um and net fields.",
                details={"shape_index": index, "shape": item},
                next_action="Provide each M1 shape as {net: name, bbox_um: [x1,y1,x2,y2]}.",
            )
        net = item.get("net")
        if not isinstance(net, str) or not net.strip():
            raise AnalysisError(
                code="INVALID_M1_NET",
                message="Every M1 connectivity shape must have a non-empty net name.",
                details={"shape_index": index, "net": net},
                next_action="Assign an explicit source, drain, gate, or body net role.",
            )
        parsed_by_net.setdefault(net, []).append(
            (index, Box.from_sequence(item.get("bbox_um")))
        )

    component_counts: dict[str, int] = {}
    components: dict[str, list[list[int]]] = {}
    for net, shapes in sorted(parsed_by_net.items()):
        unvisited = set(range(len(shapes)))
        net_components: list[list[int]] = []
        while unvisited:
            seed = min(unvisited)
            unvisited.remove(seed)
            stack = [seed]
            member_indexes: list[int] = []
            while stack:
                current = stack.pop()
                shape_index, current_box = shapes[current]
                member_indexes.append(shape_index)
                neighbors = []
                for candidate in sorted(unvisited):
                    _, candidate_box = shapes[candidate]
                    dx = max(
                        0.0,
                        max(
                            current_box.x1 - candidate_box.x2,
                            candidate_box.x1 - current_box.x2,
                        ),
                    )
                    dy = max(
                        0.0,
                        max(
                            current_box.y1 - candidate_box.y2,
                            candidate_box.y1 - current_box.y2,
                        ),
                    )
                    overlap_x = min(current_box.x2, candidate_box.x2) - max(
                        current_box.x1, candidate_box.x1
                    )
                    overlap_y = min(current_box.y2, candidate_box.y2) - max(
                        current_box.y1, candidate_box.y1
                    )
                    # A corner-only touch is not a conductive join.  Ignore tiny
                    # positive artifacts below the caller's explicit geometry
                    # tolerance when deciding whether a shared edge has length.
                    has_positive_edge_overlap = (
                        overlap_x > tolerance_um or overlap_y > tolerance_um
                    )
                    if (
                        dx <= tolerance_um
                        and dy <= tolerance_um
                        and has_positive_edge_overlap
                    ):
                        neighbors.append(candidate)
                for candidate in neighbors:
                    unvisited.remove(candidate)
                    stack.append(candidate)
            net_components.append(sorted(member_indexes))
        components[net] = net_components
        component_counts[net] = len(net_components)

    open_nets = [
        {"net": net, "component_count": count, "components": components[net]}
        for net, count in component_counts.items()
        if count > 1
    ]
    return {
        "checked": bool(m1_shapes),
        "method": "axis_aligned_box_touch_components",
        "electrically_connected": bool(m1_shapes) and not open_nets,
        "net_component_counts": component_counts,
        "open_nets": open_nets,
        "limitations": (
            "This is a geometry guardrail for labeled orthogonal M1 boxes, not LVS."
        ),
    }


def verify_dut_design_rules(
    dut_geometry: dict[str, Any],
    rules: DesignRuleConfig | None = None,
) -> dict[str, Any]:
    """Verify Key Design Rules for a generated DUT geometry dictionary."""

    cfg = rules or DesignRuleConfig()
    cfg.validate()

    all_violations: list[dict[str, Any]] = []

    # 1. Check M1 shape widths and spacing
    m1_shapes = dut_geometry.get("m1_shapes_um", [])
    if m1_shapes:
        m1_violations = check_box_design_rules(
            m1_shapes,
            min_width_um=cfg.min_m1_width_um,
            min_space_um=cfg.min_m1_space_um,
            rule_name="M1",
            tolerance_um=cfg.comparison_tolerance_um,
        )
        all_violations.extend(m1_violations)

    # 2. Check Poly Gate width
    poly_boxes = dut_geometry.get("poly_boxes_um", [])
    for idx, raw in enumerate(poly_boxes, start=1):
        box = Box.from_sequence(raw)
        if box.width < cfg.min_poly_width_um - cfg.comparison_tolerance_um:
            all_violations.append({
                "rule": "POLY_MIN_WIDTH",
                "box_index": idx,
                "box_um": box.to_list(),
                "actual_width_um": box.width,
                "min_required_um": cfg.min_poly_width_um,
            })

    # 3. Check Contact size
    contact_boxes = dut_geometry.get("contact_boxes_um", [])
    for idx, raw in enumerate(contact_boxes, start=1):
        box = Box.from_sequence(raw)
        if min(box.width, box.height) < cfg.min_contact_size_um - cfg.comparison_tolerance_um:
            all_violations.append({
                "rule": "CONTACT_MIN_SIZE",
                "box_index": idx,
                "box_um": box.to_list(),
                "actual_size_um": [box.width, box.height],
                "min_required_um": cfg.min_contact_size_um,
            })

    # 4. Check Directional Terminal Overlaps
    terminals = dut_geometry.get("terminals", {})
    for term_name, term_info in terminals.items():
        landing_box_raw = term_info.get("landing_bbox_um")
        boundary_side = term_info.get("boundary_side", term_info.get("direction"))
        if landing_box_raw:
            lbox = Box.from_sequence(landing_box_raw)
            if boundary_side not in {"left", "right", "top", "bottom"}:
                all_violations.append({
                    "rule": "TERMINAL_CONTRACT_INVALID",
                    "terminal": term_name,
                    "boundary_side": boundary_side,
                    "message": "Terminal boundary_side must be left, right, top, or bottom.",
                })
                continue
            overlap_dim = (
                lbox.width
                if boundary_side in ("left", "right")
                else lbox.height
            )
            if overlap_dim < cfg.min_landing_overlap_um - cfg.comparison_tolerance_um:
                all_violations.append({
                    "rule": "TERMINAL_MIN_OVERLAP",
                    "terminal": term_name,
                    "boundary_side": boundary_side,
                    "landing_bbox_um": lbox.to_list(),
                    "actual_overlap_um": overlap_dim,
                    "min_required_um": cfg.min_landing_overlap_um,
                })


    if all_violations:
        raise AnalysisError(
            code="DESIGN_RULE_VIOLATION",
            message=f"Found {len(all_violations)} design rule violation(s) in DUT geometry.",
            details={"violation_count": len(all_violations), "violations": all_violations},
            next_action="Adjust DUT parameters (e.g. increase width, pitch, or overlap) to satisfy DRC.",
        )

    connectivity = analyze_m1_connectivity(
        m1_shapes,
        tolerance_um=cfg.comparison_tolerance_um,
    )
    return {
        "ok": True,
        "drc_clean": True,
        "design_rules_clean": True,
        "violation_count": 0,
        "electrical_connectivity_verified": connectivity["electrically_connected"],
        "connectivity": connectivity,
        "rules_checked": {
            "min_m1_width_um": cfg.min_m1_width_um,
            "min_m1_space_um": cfg.min_m1_space_um,
            "min_landing_overlap_um": cfg.min_landing_overlap_um,
            "min_poly_width_um": cfg.min_poly_width_um,
            "min_contact_size_um": cfg.min_contact_size_um,
            "comparison_tolerance_um": cfg.comparison_tolerance_um,
        },
    }
