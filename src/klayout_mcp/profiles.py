"""Single source of truth for the fixed wafer-scribe TEG profile."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TegProfile:
    teg_width_um: float = 2000.0
    teg_height_um: float = 60.0
    expected_pad_count: int = 25
    source_drain_pad_count: int = 22
    odd_gate_pad: int = 23
    even_gate_pad: int = 24
    body_pad: int = 25
    pad_width_um: float = 40.0
    pad_height_um: float = 40.0
    pitch_um: float = 80.0
    device_width_um: float = 35.0
    device_height_um: float = 40.0
    default_tolerance_um: float = 0.1
    default_landing_search_half_depth_um: float = 1.0

    @property
    def dut_site_count(self) -> int:
        return self.source_drain_pad_count - 1


DEFAULT_TEG_PROFILE = TegProfile()
