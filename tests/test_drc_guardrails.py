from __future__ import annotations

import pytest

from klayout_mcp.drc_guardrails import (
    DesignRuleConfig,
    analyze_m1_connectivity,
    check_box_design_rules,
    verify_dut_design_rules,
)
from klayout_mcp.dut_geometry import DutParameters, build_dut_geometry
from klayout_mcp.errors import AnalysisError


def test_default_dut_passes_drc() -> None:
    geom = build_dut_geometry().to_dict()
    result = verify_dut_design_rules(geom)

    assert result["ok"] is True
    assert result["drc_clean"] is True
    assert result["design_rules_clean"] is True
    assert result["violation_count"] == 0
    assert result["electrical_connectivity_verified"] is False
    assert {item["net"] for item in result["connectivity"]["open_nets"]} == {
        "source",
        "drain",
    }


def test_m1_min_width_violation() -> None:
    # m1_width_um = 0.2 um < min 0.28 um
    params = DutParameters(m1_width_um=0.2)
    geom = build_dut_geometry(params).to_dict()

    with pytest.raises(AnalysisError) as exc:
        verify_dut_design_rules(geom, DesignRuleConfig(min_m1_width_um=0.28))

    assert exc.value.code == "DESIGN_RULE_VIOLATION"
    details = exc.value.details
    assert details["violation_count"] > 0
    assert any(v["rule"] == "M1_MIN_WIDTH" for v in details["violations"])


def test_poly_min_width_violation() -> None:
    # l_um = 0.05 um < min 0.08 um
    params = DutParameters(l_um=0.05)
    geom = build_dut_geometry(params).to_dict()

    with pytest.raises(AnalysisError) as exc:
        verify_dut_design_rules(geom, DesignRuleConfig(min_poly_width_um=0.08))

    assert exc.value.code == "DESIGN_RULE_VIOLATION"
    details = exc.value.details
    assert any(v["rule"] == "POLY_MIN_WIDTH" for v in details["violations"])


def test_terminal_min_overlap_violation() -> None:
    # m1_overlap_um = 0.05 um < min 0.1 um
    params = DutParameters(m1_overlap_um=0.05)
    geom = build_dut_geometry(params).to_dict()

    with pytest.raises(AnalysisError) as exc:
        verify_dut_design_rules(geom, DesignRuleConfig(min_landing_overlap_um=0.1))

    assert exc.value.code == "DESIGN_RULE_VIOLATION"
    details = exc.value.details
    assert any(v["rule"] == "TERMINAL_MIN_OVERLAP" for v in details["violations"])


def test_invalid_rules_raise_error() -> None:
    with pytest.raises(AnalysisError) as exc:
        DesignRuleConfig(min_m1_width_um=-0.1).validate()
    assert exc.value.code == "INVALID_DESIGN_RULES"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1e-3])
def test_invalid_rule_tolerance_is_rejected(invalid: float) -> None:
    with pytest.raises(AnalysisError) as caught:
        DesignRuleConfig(comparison_tolerance_um=invalid).validate()

    assert caught.value.code == "INVALID_DESIGN_RULES"


def test_diagonal_m1_spacing_violation_is_detected() -> None:
    violations = check_box_design_rules(
        [
            {"net": "source", "bbox_um": [0.0, 0.0, 1.0, 1.0]},
            {"net": "drain", "bbox_um": [1.1, 1.1, 2.1, 2.1]},
        ],
        min_width_um=0.1,
        min_space_um=0.28,
    )

    spacing = [item for item in violations if item["rule"] == "M1_MIN_SPACE"]
    assert len(spacing) == 1
    assert spacing[0]["violation_axis"] == "DIAGONAL"
    assert spacing[0]["actual_space_um"] == pytest.approx(2**0.5 * 0.1)


@pytest.mark.parametrize(
    ("terminal", "boundary_side", "landing_bbox_um"),
    [
        ("gate", "top", [-0.2, 20.0, 0.2, 20.05]),
        ("body", "bottom", [-0.2, -20.05, 0.2, -20.0]),
    ],
)
def test_vertical_terminal_overlap_uses_height_axis(
    terminal: str,
    boundary_side: str,
    landing_bbox_um: list[float],
) -> None:
    geometry = {
        "terminals": {
            terminal: {
                "boundary_side": boundary_side,
                "landing_bbox_um": landing_bbox_um,
            }
        }
    }

    with pytest.raises(AnalysisError) as caught:
        verify_dut_design_rules(
            geometry,
            DesignRuleConfig(min_landing_overlap_um=0.1),
        )

    assert caught.value.code == "DESIGN_RULE_VIOLATION"
    violation = caught.value.details["violations"][0]
    assert violation["rule"] == "TERMINAL_MIN_OVERLAP"
    assert violation["boundary_side"] == boundary_side
    assert violation["actual_overlap_um"] == pytest.approx(0.05)


def test_rule_boundary_and_explicit_tolerance_are_distinct() -> None:
    exact = check_box_design_rules(
        [{"net": "source", "bbox_um": [0.0, 0.0, 0.28, 1.0]}],
        min_width_um=0.28,
        min_space_um=0.28,
        tolerance_um=1e-9,
    )
    below = check_box_design_rules(
        [{"net": "source", "bbox_um": [0.0, 0.0, 0.279999, 1.0]}],
        min_width_um=0.28,
        min_space_um=0.28,
        tolerance_um=1e-9,
    )

    assert exact == []
    assert any(item["rule"] == "M1_MIN_WIDTH" for item in below)


def test_m1_connectivity_reports_open_and_connected_nets() -> None:
    result = analyze_m1_connectivity(
        [
            {"net": "source", "bbox_um": [0.0, 0.0, 1.0, 1.0]},
            {"net": "source", "bbox_um": [1.0, 0.0, 2.0, 1.0]},
            {"net": "drain", "bbox_um": [5.0, 0.0, 6.0, 1.0]},
            {"net": "drain", "bbox_um": [7.0, 0.0, 8.0, 1.0]},
        ]
    )

    assert result["electrically_connected"] is False
    assert result["net_component_counts"] == {"drain": 2, "source": 1}
    assert [item["net"] for item in result["open_nets"]] == ["drain"]


def test_m1_connectivity_rejects_corner_only_contact() -> None:
    result = analyze_m1_connectivity(
        [
            {"net": "gate", "bbox_um": [0.0, 0.0, 1.0, 1.0]},
            {"net": "gate", "bbox_um": [1.0, 1.0, 2.0, 2.0]},
        ],
        tolerance_um=0.0,
    )

    assert result["electrically_connected"] is False
    assert result["net_component_counts"] == {"gate": 2}


def test_m1_connectivity_rejects_sub_tolerance_corner_artifact() -> None:
    result = analyze_m1_connectivity(
        [
            {"net": "gate", "bbox_um": [0.0, 0.0, 1.0, 1.0]},
            {"net": "gate", "bbox_um": [1.0 - 1e-12, 1.0, 2.0, 2.0]},
        ],
        tolerance_um=1e-9,
    )

    assert result["electrically_connected"] is False
    assert result["net_component_counts"] == {"gate": 2}


def test_m1_connectivity_accepts_positive_edge_contact() -> None:
    result = analyze_m1_connectivity(
        [
            {"net": "gate", "bbox_um": [0.0, 0.0, 1.0, 1.0]},
            {"net": "gate", "bbox_um": [1.0, 0.25, 2.0, 0.75]},
        ],
        tolerance_um=0.0,
    )

    assert result["electrically_connected"] is True
    assert result["net_component_counts"] == {"gate": 1}
