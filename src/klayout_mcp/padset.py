"""Deterministic pad-row and DUT-slot analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Sequence

from .errors import AnalysisError
from .geometry import Box, Point
from .profiles import DEFAULT_TEG_PROFILE


@dataclass(frozen=True, slots=True)
class PadDetectionConfig:
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count
    expected_pad_width_um: float = DEFAULT_TEG_PROFILE.pad_width_um
    expected_pad_height_um: float = DEFAULT_TEG_PROFILE.pad_height_um
    expected_pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um
    size_tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um
    alignment_tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um
    pitch_tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um

    def validate(self) -> None:
        if (
            self.expected_pad_count != DEFAULT_TEG_PROFILE.expected_pad_count
            or self.source_drain_pad_count
            != DEFAULT_TEG_PROFILE.source_drain_pad_count
        ):
            raise AnalysisError(
                code="UNSUPPORTED_PAD_TOPOLOGY",
                message="This TEG profile requires 25 pads and 22 Source/Drain pads.",
                details={
                    "expected_pad_count": self.expected_pad_count,
                    "source_drain_pad_count": self.source_drain_pad_count,
                },
                next_action="Use expected_pad_count=25 and source_drain_pad_count=22.",
            )
        positive = {
            "expected_pad_width_um": self.expected_pad_width_um,
            "expected_pad_height_um": self.expected_pad_height_um,
            "expected_pitch_um": self.expected_pitch_um,
            "device_width_um": self.device_width_um,
            "device_height_um": self.device_height_um,
        }
        invalid = {
            name: value
            for name, value in positive.items()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        }
        if invalid:
            raise AnalysisError(
                "INVALID_CONFIG",
                "Geometry dimensions must be finite positive numbers.",
                details=invalid,
                next_action="Replace boolean, text, NaN, infinite, or nonpositive dimensions.",
            )
        tolerances = {
            "size_tolerance_um": self.size_tolerance_um,
            "alignment_tolerance_um": self.alignment_tolerance_um,
            "pitch_tolerance_um": self.pitch_tolerance_um,
        }
        invalid_tolerances = {
            name: value
            for name, value in tolerances.items()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        }
        if invalid_tolerances:
            raise AnalysisError(
                code="INVALID_CONFIG",
                message="Geometry tolerances must be finite and nonnegative.",
                details=invalid_tolerances,
                next_action="Replace boolean, text, NaN, infinite, or negative tolerances.",
            )
        if self.expected_pad_width_um != self.expected_pad_height_um:
            raise AnalysisError(
                code="PAD_MUST_BE_SQUARE",
                message="Pad width and height must be equal.",
                details={
                    "pad_width_um": self.expected_pad_width_um,
                    "pad_height_um": self.expected_pad_height_um,
                },
                next_action="Provide one square pad size. Example: pad_size_um=40.0.",
            )


def _close(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def _candidate_rows(boxes: list[Box], tolerance: float) -> list[list[Box]]:
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda item: (item.center.y, item.center.x)):
        for row in rows:
            row_y = median(item.center.y for item in row)
            if abs(box.center.y - row_y) <= tolerance:
                row.append(box)
                break
        else:
            rows.append([box])
    return rows


def analyze_pad_boxes(
    raw_boxes_um: Sequence[Sequence[float]],
    config: PadDetectionConfig | None = None,
) -> dict[str, object]:
    """Analyze normalized M1 pad boxes and derive Source/Drain DUT slots.

    Gate and Body landing extraction requires the later KLayout M1-region adapter.
    This function only establishes deterministic pad ordering and slot boundaries.
    """

    cfg = config or PadDetectionConfig()
    cfg.validate()
    boxes = [Box.from_sequence(values) for values in raw_boxes_um]
    pad_candidates = [
        box
        for box in boxes
        if _close(box.width, cfg.expected_pad_width_um, cfg.size_tolerance_um)
        and _close(box.height, cfg.expected_pad_height_um, cfg.size_tolerance_um)
    ]

    rows = [
        sorted(row, key=lambda item: item.center.x)
        for row in _candidate_rows(pad_candidates, cfg.alignment_tolerance_um)
        if len(row) == cfg.expected_pad_count
    ]
    if not rows:
        raise AnalysisError(
            code="PAD_ROW_NOT_FOUND",
            message="No aligned pad row matches the expected count and size.",
            details={
                "input_box_count": len(boxes),
                "pad_candidate_count": len(pad_candidates),
                "expected_pad_count": cfg.expected_pad_count,
            },
            next_action="Check the pad size, count, M1 extraction, or tolerances.",
        )
    if len(rows) > 1:
        raise AnalysisError(
            code="PADSET_AMBIGUOUS",
            message="Multiple pad rows match the expected pattern.",
            details={"candidate_row_count": len(rows)},
            next_action="Provide a row bounding box or select the padset top cell.",
        )

    pads = rows[0]
    pitches = [pads[index + 1].center.x - pads[index].center.x for index in range(len(pads) - 1)]
    bad_pitches = [
        {"between_pad": [index + 1, index + 2], "pitch_um": pitch}
        for index, pitch in enumerate(pitches)
        if not _close(pitch, cfg.expected_pitch_um, cfg.pitch_tolerance_um)
    ]
    if bad_pitches:
        raise AnalysisError(
            code="PAD_PITCH_MISMATCH",
            message="Pad pitch does not match the expected value.",
            details={"expected_pitch_um": cfg.expected_pitch_um, "mismatches": bad_pitches},
            next_action="Check pad detection, orientation, or the expected pitch.",
        )

    slots: list[dict[str, object]] = []
    for index in range(cfg.source_drain_pad_count - 1):
        left_pad = pads[index]
        right_pad = pads[index + 1]
        gap_left = left_pad.x2
        gap_right = right_pad.x1
        gap_width = gap_right - gap_left
        if gap_width <= 0:
            raise AnalysisError(
                code="PAD_OVERLAP",
                message="Adjacent Source/Drain pads overlap.",
                details={"pad_numbers": [index + 1, index + 2], "gap_width_um": gap_width},
                next_action="Correct the pad geometry before deriving DUT slots.",
            )
        if cfg.device_width_um > gap_width + 1e-9:
            raise AnalysisError(
                code="DEVICE_WINDOW_TOO_LARGE",
                message="Requested device width does not fit between adjacent pads.",
                details={
                    "site": index + 1,
                    "available_width_um": gap_width,
                    "requested_width_um": cfg.device_width_um,
                },
                next_action="Reduce the device width or provide a different padset. Automatic shrink is disabled.",
            )

        center = Point((gap_left + gap_right) / 2.0, (left_pad.center.y + right_pad.center.y) / 2.0)
        device_window = Box(
            center.x - cfg.device_width_um / 2.0,
            center.y - cfg.device_height_um / 2.0,
            center.x + cfg.device_width_um / 2.0,
            center.y + cfg.device_height_um / 2.0,
        )
        routing_boundary = Box(
            gap_left,
            device_window.y1,
            gap_right,
            device_window.y2,
        )
        site_number = index + 1
        slots.append(
            {
                "site": site_number,
                "origin_um": center.to_list(),
                "source_pad": site_number,
                "drain_pad": site_number + 1,
                "gate_pad": (
                    DEFAULT_TEG_PROFILE.odd_gate_pad
                    if site_number % 2
                    else DEFAULT_TEG_PROFILE.even_gate_pad
                ),
                "body_pad": DEFAULT_TEG_PROFILE.body_pad,
                "device_window_um": device_window.to_list(),
                "routing_boundary_um": routing_boundary.to_list(),
                "boundary_anchors_um": {
                    "source": [routing_boundary.x1, center.y],
                    "drain": [routing_boundary.x2, center.y],
                    "gate": [center.x, routing_boundary.y2],
                    "body": [center.x, routing_boundary.y1],
                },
                "landing_status": "unresolved",
            }
        )

    pad_results = [
        {"number": number, "bbox_um": pad.to_list(), "center_um": pad.center.to_list()}
        for number, pad in enumerate(pads, start=1)
    ]
    return {
        "ok": True,
        "units": "um",
        "pad_count": len(pads),
        "pad_pitch_um": median(pitches),
        "pads": pad_results,
        "dut_slot_count": len(slots),
        "dut_slots": slots,
        "warnings": [
            "Gate and Body landings are unresolved until M1 connectivity analysis is available."
        ],
    }
