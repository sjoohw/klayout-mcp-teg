from dataclasses import dataclass
from itertools import product

from klayout_mcp.geometry_fingerprint import _minimum_rotation, canonical_ring


@dataclass(frozen=True)
class Point:
    x: int
    y: int


def test_canonical_ring_ignores_start_point_direction_and_closing_point() -> None:
    ring = [Point(4, 1), Point(8, 3), Point(6, 9), Point(1, 7)]
    expected = canonical_ring(ring)

    for offset in range(len(ring)):
        rotated = ring[offset:] + ring[:offset]
        assert canonical_ring(rotated) == expected
        assert canonical_ring(list(reversed(rotated))) == expected
        assert canonical_ring([*rotated, rotated[0]]) == expected


def test_minimum_rotation_matches_exhaustive_reference_for_repeated_values() -> None:
    for size in range(1, 7):
        for values in product(range(3), repeat=size):
            sequence = [(value, 0) for value in values]
            expected = min(
                sequence[offset:] + sequence[:offset]
                for offset in range(size)
            )
            assert _minimum_rotation(sequence) == expected


def test_canonical_ring_handles_large_vertex_count_without_materializing_rotations() -> None:
    vertex_count = 20_000
    ring = [Point(index, (index * 7919) % 104_729) for index in range(vertex_count)]

    canonical = canonical_ring(ring[7_777:] + ring[:7_777])

    assert len(canonical) == vertex_count
    assert canonical[0] == [0, 0]
