import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_fingerprint import (
    bind_ruler_to_selected_edges,
    build_neighborhood_fingerprint,
    build_shape_identity,
    micron_ruler_to_exact_dbu,
    normalize_geometry_record,
)


def test_polygon_fingerprint_ignores_ring_start_and_direction() -> None:
    first = build_shape_identity(
        geometry={
            "kind": "polygon",
            "hull_dbu": [[0, 0], [10, 0], [10, 5], [0, 5]],
            "holes_dbu": [],
        },
        layer=1,
        datatype=0,
        shape_ordinal=2,
    )
    second = build_shape_identity(
        geometry={
            "kind": "polygon",
            "hull_dbu": [[10, 5], [10, 0], [0, 0], [0, 5]],
            "holes_dbu": [],
        },
        layer=1,
        datatype=0,
        shape_ordinal=2,
    )

    assert first == second


def test_path_fingerprint_is_direction_independent_with_swapped_extensions() -> None:
    forward = normalize_geometry_record(
        {
            "kind": "path",
            "points_dbu": [[0, 0], [10, 0], [10, 20]],
            "width_dbu": 4,
            "begin_extension_dbu": 1,
            "end_extension_dbu": 3,
        }
    )
    reverse = normalize_geometry_record(
        {
            "kind": "path",
            "points_dbu": [[10, 20], [10, 0], [0, 0]],
            "width_dbu": 4,
            "begin_extension_dbu": 3,
            "end_extension_dbu": 1,
        }
    )

    assert forward == reverse


def test_shape_ordinal_distinguishes_duplicate_geometry() -> None:
    arguments = {
        "geometry": {"kind": "box", "bbox_dbu": [0, 0, 10, 20]},
        "layer": 1,
        "datatype": 0,
        "duplicate_geometry_count": 2,
    }

    first = build_shape_identity(shape_ordinal=3, **arguments)
    second = build_shape_identity(shape_ordinal=4, **arguments)

    assert first["geometry"] == second["geometry"]
    assert first["shape_fingerprint_sha256"] != second["shape_fingerprint_sha256"]


def test_neighborhood_fingerprint_is_input_order_independent() -> None:
    first = build_shape_identity(
        geometry={"kind": "box", "bbox_dbu": [0, 0, 10, 20]},
        layer=1,
        datatype=0,
        shape_ordinal=0,
    )
    second = build_shape_identity(
        geometry={"kind": "box", "bbox_dbu": [20, 0, 30, 20]},
        layer=1,
        datatype=0,
        shape_ordinal=1,
    )

    a = build_neighborhood_fingerprint([first, second], radius_dbu=1)
    b = build_neighborhood_fingerprint([second, first], radius_dbu=1)

    assert a == b


def test_ruler_converts_to_exact_integer_dbu() -> None:
    assert micron_ruler_to_exact_dbu(
        [[0.1, 0.2], [0.3, 0.2]], dbu_um=0.001
    ) == [[100, 200], [300, 200]]


def test_off_grid_and_diagonal_rulers_fail_closed() -> None:
    with pytest.raises(AnalysisError) as off_grid:
        micron_ruler_to_exact_dbu(
            [[0.1004, 0.2], [0.3, 0.2]], dbu_um=0.001
        )
    assert off_grid.value.code == "OFF_GRID_PCELLIZER_RULER"

    with pytest.raises(AnalysisError) as diagonal:
        micron_ruler_to_exact_dbu([[0.1, 0.2], [0.3, 0.4]], dbu_um=0.001)
    assert diagonal.value.code == "NON_MANHATTAN_PCELLIZER_RULER"


def test_ruler_binds_to_one_edge_at_each_endpoint() -> None:
    result = bind_ruler_to_selected_edges(
        [[10, 5], [30, 5]],
        [
            {
                "top_edges_dbu": [
                    [[0, 0], [10, 0]],
                    [[10, 0], [10, 10]],
                    [[10, 10], [0, 10]],
                    [[0, 10], [0, 0]],
                ]
            },
            {
                "top_edges_dbu": [
                    [[30, 0], [40, 0]],
                    [[40, 0], [40, 10]],
                    [[40, 10], [30, 10]],
                    [[30, 10], [30, 0]],
                ]
            },
        ],
    )

    assert result["orientation"] == "horizontal"
    assert result["length_dbu"] == 20
    assert [item["selection_index"] for item in result["endpoint_bindings"]] == [0, 1]


def test_ruler_binding_preserves_user_drawn_p1_p2_direction() -> None:
    result = bind_ruler_to_selected_edges(
        [[30, 5], [10, 5]],
        [
            {"top_edges_dbu": [[[10, 0], [10, 10]]]},
            {"top_edges_dbu": [[[30, 0], [30, 10]]]},
        ],
    )

    assert result["ruler_dbu"] == [[30, 5], [10, 5]]
    assert [item["selection_index"] for item in result["endpoint_bindings"]] == [1, 0]


def test_ruler_endpoint_at_polygon_corner_is_ambiguous() -> None:
    with pytest.raises(AnalysisError) as caught:
        bind_ruler_to_selected_edges(
            [[10, 0], [30, 0]],
            [
                {
                    "top_edges_dbu": [
                        [[0, 0], [10, 0]],
                        [[10, 0], [10, 10]],
                    ]
                },
                {"top_edges_dbu": [[[30, 0], [30, 10]]]},
            ],
        )

    assert caught.value.code == "AMBIGUOUS_PCELLIZER_RULER_ENDPOINT"
