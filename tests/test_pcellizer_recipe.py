from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_contract import (
    build_selection_manifest,
    build_source_layout_identity,
    normalize_occurrence_path,
)
from klayout_mcp.pcellizer_fingerprint import build_shape_identity
from klayout_mcp.pcellizer_intent import build_pcellizer_parameter_intent
from klayout_mcp.pcellizer_recipe import compile_pcellizer_single_shape_recipe
from klayout_mcp.pcellizer_snapshot import (
    create_pcellizer_snapshot_package,
    inspect_pcellizer_snapshot_package,
)
from klayout_mcp.workflow_manifest import canonical_sha256


NEIGHBORHOOD_HASH = "b" * 64


def _snapshot(
    tmp_path: Path, *, split_endpoints: bool = False, ruler_length_dbu: int = 100
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"hierarchical-box-source")
    source = build_source_layout_identity(str(source_path), top_cell="TOP", dbu_um=0.001)
    occurrence = normalize_occurrence_path(top_cell="TOP", segments=[])
    identity = build_shape_identity(
        geometry={"kind": "box", "bbox_dbu": [0, 0, 100, 50]},
        layer=10,
        datatype=0,
        shape_ordinal=0,
    )
    second_identity = build_shape_identity(
        geometry={"kind": "box", "bbox_dbu": [200, 0, 300, 50]},
        layer=10,
        datatype=0,
        shape_ordinal=1,
    )
    top_edges = [
        [[0, 0], [100, 0]],
        [[100, 0], [100, 50]],
        [[100, 50], [0, 50]],
        [[0, 50], [0, 0]],
    ]
    endpoint_edges = [top_edges[3], top_edges[1]]
    selections = [
        {
            "cell": "TOP",
            "layer": 10,
            "datatype": 0,
            "occurrence_path": occurrence,
            "shape_identity": identity,
            "top_edges_dbu": top_edges,
        }
    ]
    if split_endpoints:
        selections.append(
            {
                "cell": "TOP",
                "layer": 10,
                "datatype": 0,
                "occurrence_path": occurrence,
                "shape_identity": second_identity,
                "top_edges_dbu": [
                    [[200, 0], [300, 0]],
                    [[300, 0], [300, 50]],
                    [[300, 50], [200, 50]],
                    [[200, 50], [200, 0]],
                ],
            }
        )
    selection_indices = [0, 1] if split_endpoints else [0, 0]
    identities = [identity, second_identity] if split_endpoints else [identity, identity]
    bindings = [
        {
            "endpoint_dbu": [0, 25],
            "selection_index": selection_indices[0],
            "edge_index": 3,
            "edge_dbu": endpoint_edges[0],
        },
        {
            "endpoint_dbu": [100, 25],
            "selection_index": selection_indices[1],
            "edge_index": 1,
            "edge_dbu": endpoint_edges[1],
        },
    ]
    endpoint_manifests = []
    for endpoint_index, (selection_index, shape_identity, edge) in enumerate(
        zip(selection_indices, identities, endpoint_edges)
    ):
        endpoint_manifests.append(
            {
                "endpoint_index": endpoint_index,
                "selection_index": selection_index,
                "manifest": build_selection_manifest(
                    source=source,
                    occurrence_path=occurrence,
                    layer=10,
                    datatype=0,
                    shape_fingerprint_sha256=shape_identity["shape_fingerprint_sha256"],
                    shape_ordinal=shape_identity["shape_ordinal"],
                    edge_dbu=edge,
                    neighborhood_fingerprint_sha256=NEIGHBORHOOD_HASH,
                ),
            }
        )
    capture = {
        "schema_version": 1,
        "kind": "PCellizerParameterCapture",
        "source": source,
        "ruler": {
            "ruler_dbu": [[0, 25], [100, 25]],
            "orientation": "horizontal",
            "length_dbu": ruler_length_dbu,
            "endpoint_bindings": bindings,
            "edge_snap": "exact_dbu",
            "ambiguous": False,
        },
        "selected_shapes": selections,
        "endpoint_manifests": endpoint_manifests,
        "scope": "current_occurrence",
        "selection_mode": "explicit_shapes_and_ruler",
        "edge_snap": "exact_dbu",
        "source_layout_modified": False,
        "flattening_performed": False,
        "production_ready": False,
    }
    capture["parameter_capture_sha256"] = canonical_sha256(capture)
    return create_pcellizer_snapshot_package(
        capture=capture, package_root=str(tmp_path / "store")
    )


def _intent(snapshot: dict, **overrides) -> dict:
    values = {
        "snapshot_package_sha256": snapshot["manifest"]["snapshot_package_sha256"],
        "parameter_name": "line_width",
        "min_um": 0.05,
        "nominal_um": 0.1,
        "max_um": 0.15,
        "step_um": 0.01,
        "dbu_um": 0.001,
        "manufacturing_grid_um": 0.005,
        "dimension_semantics": "transverse_width",
        "anchor_policy": "p1_fixed",
        "dependency_policy": "fixed_unselected_geometry",
    }
    values.update(overrides)
    return build_pcellizer_parameter_intent(**values)


def test_inspect_and_compile_single_box_recipe_deterministically(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)
    inspected = inspect_pcellizer_snapshot_package(package_dir=snapshot["package_dir"])
    intent = _intent(snapshot)

    first = compile_pcellizer_single_shape_recipe(
        package_dir=snapshot["package_dir"], parameter_intent=intent
    )
    second = compile_pcellizer_single_shape_recipe(
        package_dir=snapshot["package_dir"], parameter_intent=intent
    )

    assert inspected["manifest"]["snapshot_package_sha256"] == intent["snapshot_package_sha256"]
    assert first == second
    assert first["operations"][0]["local_axis"] == "x"
    assert first["parameter"]["nominal_dbu"] == 100
    assert first["hierarchy_strategy"] == {
        "flattening_performed": False,
        "preserve_unselected_occurrences": True,
        "occurrence_specific_identity_wrapper_required": False,
    }
    assert first["source_geometry_modified"] is False
    assert first["production_ready"] is False


def test_recipe_rejects_intent_from_another_snapshot(tmp_path) -> None:
    snapshot = _snapshot(tmp_path / "one")
    other = _snapshot(tmp_path / "two")

    with pytest.raises(AnalysisError) as error:
        compile_pcellizer_single_shape_recipe(
            package_dir=snapshot["package_dir"], parameter_intent=_intent(other)
        )

    assert error.value.code == "PCELLIZER_RECIPE_SNAPSHOT_MISMATCH"


def test_recipe_rejects_two_different_shapes(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, split_endpoints=True)

    with pytest.raises(AnalysisError) as error:
        compile_pcellizer_single_shape_recipe(
            package_dir=snapshot["package_dir"], parameter_intent=_intent(snapshot)
        )

    assert error.value.code == "PCELLIZER_RECIPE_EXACTLY_ONE_SHAPE_REQUIRED"


def test_recipe_rejects_nominal_not_equal_to_capture(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)

    with pytest.raises(AnalysisError) as error:
        compile_pcellizer_single_shape_recipe(
            package_dir=snapshot["package_dir"],
            parameter_intent=_intent(snapshot, nominal_um=0.12),
        )

    assert error.value.code == "PCELLIZER_RECIPE_NOMINAL_MISMATCH"


def test_center_anchor_rejects_half_dbu_drift(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)

    with pytest.raises(AnalysisError) as error:
        compile_pcellizer_single_shape_recipe(
            package_dir=snapshot["package_dir"],
            parameter_intent=_intent(
                snapshot,
                min_um=0.099,
                nominal_um=0.1,
                max_um=0.101,
                step_um=0.001,
                manufacturing_grid_um=0.001,
                anchor_policy="center_fixed",
            ),
        )

    assert error.value.code == "PCELLIZER_CENTER_ANCHOR_HALF_DBU"


def test_recipe_rederives_ruler_length_instead_of_trusting_capture_metadata(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, ruler_length_dbu=99)

    with pytest.raises(AnalysisError) as error:
        compile_pcellizer_single_shape_recipe(
            package_dir=snapshot["package_dir"], parameter_intent=_intent(snapshot)
        )

    assert error.value.code == "PCELLIZER_RECIPE_RULER_METADATA_MISMATCH"
