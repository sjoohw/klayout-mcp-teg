from __future__ import annotations

import pytest

from klayout_mcp.dbu_grid import DbuGridError, is_on_dbu_grid, micron_to_dbu


def test_arithmetic_representation_drift_is_normalized() -> None:
    assert micron_to_dbu(0.1 + 0.2, 0.001) == 300


def test_materially_off_grid_coordinate_is_rejected() -> None:
    with pytest.raises(DbuGridError):
        micron_to_dbu(0.3000001, 0.001)
    assert is_on_dbu_grid(0.3000001, 0.001) is False


@pytest.mark.parametrize("invalid", [0, -0.001, float("nan"), True])
def test_invalid_dbu_is_rejected(invalid: object) -> None:
    with pytest.raises(DbuGridError):
        micron_to_dbu(0.3, invalid)
