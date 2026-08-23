from __future__ import annotations

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.padset import PadDetectionConfig, analyze_pad_boxes


def standard_pad_boxes() -> list[list[float]]:
    return [
        [20.0 + 80.0 * index, 10.0, 60.0 + 80.0 * index, 50.0]
        for index in range(25)
    ]


def test_standard_padset_produces_21_slots() -> None:
    result = analyze_pad_boxes(standard_pad_boxes())

    assert result["ok"] is True
    assert result["pad_count"] == 25
    assert result["pad_pitch_um"] == pytest.approx(80.0)
    assert result["dut_slot_count"] == 21

    first = result["dut_slots"][0]
    assert first["origin_um"] == pytest.approx([80.0, 30.0])
    assert first["device_window_um"] == pytest.approx([62.5, 10.0, 97.5, 50.0])
    assert first["routing_boundary_um"] == pytest.approx([60.0, 10.0, 100.0, 50.0])
    assert first["source_pad"] == 1
    assert first["drain_pad"] == 2
    assert first["gate_pad"] == 23
    assert first["body_pad"] == 25

    second = result["dut_slots"][1]
    assert second["gate_pad"] == 24


def test_decoy_box_is_ignored() -> None:
    boxes = standard_pad_boxes()
    boxes.append([0.0, 0.0, 10.0, 10.0])

    result = analyze_pad_boxes(boxes)

    assert result["pad_count"] == 25


def test_pitch_error_is_structured() -> None:
    boxes = standard_pad_boxes()
    boxes[10] = [821.0, 10.0, 861.0, 50.0]

    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(boxes)

    assert failure.value.code == "PAD_PITCH_MISMATCH"
    assert failure.value.details["mismatches"]


def test_device_window_is_not_auto_shrunk() -> None:
    config = PadDetectionConfig(device_width_um=41.0)

    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(standard_pad_boxes(), config)

    assert failure.value.code == "DEVICE_WINDOW_TOO_LARGE"
    assert failure.value.details["requested_width_um"] == 41.0


def test_size_tolerance_does_not_allow_device_window_into_pads() -> None:
    boxes = []
    for index in range(25):
        center_x = 40.0 + 80.0 * index
        boxes.append([center_x - 20.04, 10.0, center_x + 20.04, 50.0])

    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(boxes, PadDetectionConfig(device_width_um=40.0))

    assert failure.value.code == "DEVICE_WINDOW_TOO_LARGE"
    assert failure.value.details["available_width_um"] == pytest.approx(39.92)


@pytest.mark.parametrize(
    "config",
    [
        PadDetectionConfig(device_width_um=True),
        PadDetectionConfig(size_tolerance_um=True),
    ],
)
def test_boolean_geometry_config_is_rejected(config: PadDetectionConfig) -> None:
    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(standard_pad_boxes(), config)

    assert failure.value.code == "INVALID_CONFIG"


def test_user_device_window_override() -> None:
    config = PadDetectionConfig(device_width_um=30.0, device_height_um=32.0)

    result = analyze_pad_boxes(standard_pad_boxes(), config)

    first = result["dut_slots"][0]
    assert first["device_window_um"] == pytest.approx([65.0, 14.0, 95.0, 46.0])


def test_multiple_matching_rows_are_ambiguous() -> None:
    first_row = standard_pad_boxes()
    second_row = [[x1, y1 + 100.0, x2, y2 + 100.0] for x1, y1, x2, y2 in first_row]

    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(first_row + second_row)

    assert failure.value.code == "PADSET_AMBIGUOUS"


def test_rectangular_pad_config_is_rejected() -> None:
    config = PadDetectionConfig(
        expected_pad_width_um=40.0,
        expected_pad_height_um=39.0,
    )

    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(standard_pad_boxes(), config)

    assert failure.value.code == "PAD_MUST_BE_SQUARE"
    assert failure.value.next_action


def test_nonstandard_pad_topology_is_rejected_before_gate_mapping() -> None:
    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(standard_pad_boxes(), PadDetectionConfig(expected_pad_count=24))

    assert failure.value.code == "UNSUPPORTED_PAD_TOPOLOGY"


def test_negative_tolerance_is_rejected() -> None:
    with pytest.raises(AnalysisError) as failure:
        analyze_pad_boxes(standard_pad_boxes(), PadDetectionConfig(size_tolerance_um=-0.1))

    assert failure.value.code == "INVALID_CONFIG"
