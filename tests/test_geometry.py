from __future__ import annotations

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.geometry import Box, Point


@pytest.mark.parametrize("value", ["1234", b"1234"])
def test_box_rejects_text_sequences(value: str | bytes) -> None:
    with pytest.raises(AnalysisError) as caught:
        Box.from_sequence(value)

    assert caught.value.code == "INVALID_BOX"
    assert caught.value.next_action


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (float("nan"), 0.0),
        (0.0, float("inf")),
        (True, 0.0),
        ("0", 0.0),
    ],
)
def test_point_rejects_nonfinite_or_nonnumeric_coordinates(x: object, y: object) -> None:
    with pytest.raises(AnalysisError) as caught:
        Point(x, y)  # type: ignore[arg-type]

    assert caught.value.code == "INVALID_POINT"
    assert caught.value.next_action


def test_point_normalizes_integer_coordinates() -> None:
    assert Point(1, 2).to_list() == [1.0, 2.0]
