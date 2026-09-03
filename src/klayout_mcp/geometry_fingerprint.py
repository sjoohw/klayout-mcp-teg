"""Pure helpers for deterministic geometry fingerprints."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol


GEOMETRY_FINGERPRINT_ALGORITHM_ID = "merged-polygon-rings-v2"


class PointLike(Protocol):
    x: int
    y: int


def _minimum_rotation(sequence: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return the lexicographically minimum cyclic rotation in linear time."""

    size = len(sequence)
    if size < 2:
        return list(sequence)
    doubled = list(sequence) + list(sequence)
    left = 0
    right = 1
    offset = 0
    while left < size and right < size and offset < size:
        left_value = doubled[left + offset]
        right_value = doubled[right + offset]
        if left_value == right_value:
            offset += 1
            continue
        if left_value > right_value:
            left += offset + 1
            if left <= right:
                left = right + 1
        else:
            right += offset + 1
            if right <= left:
                right = left + 1
        offset = 0
    start = min(left, right)
    return doubled[start : start + size]


def canonical_ring(points: Iterable[PointLike]) -> list[list[int]]:
    """Normalize ring start point and direction without quadratic rotations."""

    ring = [(int(point.x), int(point.y)) for point in points]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    if not ring:
        return []
    forward = _minimum_rotation(ring)
    reverse = _minimum_rotation(list(reversed(ring)))
    return [list(point) for point in min(forward, reverse)]
