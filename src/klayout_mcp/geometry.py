"""Unit-explicit geometry types used outside the KLayout process."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .errors import AnalysisError


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        coordinates = (self.x, self.y)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in coordinates
        ):
            raise AnalysisError(
                code="INVALID_POINT",
                message="Point coordinates must be finite numbers.",
                details={"point_um": self.to_list()},
                next_action="Replace boolean, text, NaN, or infinite coordinates with finite micron values.",
            )
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))

    def to_list(self) -> list[float]:
        return [self.x, self.y]


@dataclass(frozen=True, slots=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x1, self.y1, self.x2, self.y2)):
            raise AnalysisError(
                code="INVALID_BOX",
                message="Box coordinates must be finite numbers.",
                details={"box_um": self.to_list()},
                next_action="Remove NaN or infinite coordinates.",
            )
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise AnalysisError(
                code="INVALID_BOX",
                message="Box coordinates must define positive width and height.",
                details={"box_um": self.to_list()},
                next_action="Provide [x1, y1, x2, y2] with x2>x1 and y2>y1.",
            )

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> Point:
        return Point((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def to_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]

    @classmethod
    def from_sequence(cls, values: Sequence[float] | Box) -> "Box":
        if isinstance(values, Box):
            return values
        if (
            isinstance(values, (str, bytes, bytearray))
            or not isinstance(values, Sequence)
            or len(values) != 4
        ):
            raise AnalysisError(
                code="INVALID_BOX",
                message="Each box must contain four coordinates.",
                details={
                    "box": (
                        list(values)
                        if isinstance(values, Sequence)
                        and not isinstance(values, (str, bytes, bytearray))
                        else str(values)
                    )
                },
                next_action="Provide [x1, y1, x2, y2] in microns.",
            )
        try:
            return cls(*(float(value) for value in values))
        except (TypeError, ValueError) as exc:
            raise AnalysisError(
                code="INVALID_BOX",
                message="Box coordinates must be numbers.",
                details={"box": list(values)},
                next_action="Provide numeric coordinates in microns.",
            ) from exc
