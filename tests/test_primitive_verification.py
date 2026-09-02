import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.primitive_verification import (
    verify_single_conductor_primitive,
    verify_two_net_primitive,
)


def _box(bbox, net=None):
    result = {"type": "add_box", "cell": "DUT", "layer": "m1", "bbox_um": bbox}
    if net is not None:
        result["net"] = net
    return result


def test_disconnected_single_conductor_fails() -> None:
    with pytest.raises(AnalysisError) as caught:
        verify_single_conductor_primitive(
            [_box([0.0, 0.0, 1.0, 1.0]), _box([2.0, 0.0, 3.0, 1.0])]
        )

    assert caught.value.code == "PRIMITIVE_CONNECTIVITY_FAILED"


def test_two_net_edge_touch_is_a_short() -> None:
    with pytest.raises(AnalysisError) as caught:
        verify_two_net_primitive(
            [_box([0.0, 0.0, 1.0, 1.0], "P"), _box([1.0, 0.0, 2.0, 1.0], "N")],
            required_clear_space_um=0.1,
        )

    assert caught.value.code == "PRIMITIVE_CROSS_NET_SHORT"


def test_two_net_spacing_is_measured() -> None:
    result = verify_two_net_primitive(
        [_box([0.0, 0.0, 1.0, 1.0], "P"), _box([1.2, 0.0, 2.2, 1.0], "N")],
        required_clear_space_um=0.2,
    )

    assert result["cross_net_minimum_space_um"] == 0.2
    assert result["component_counts"] == {"N": 1, "P": 1}
