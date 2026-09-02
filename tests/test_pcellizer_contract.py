import hashlib

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_contract import (
    build_selection_manifest,
    build_source_layout_identity,
    normalize_occurrence_path,
    normalize_transform_descriptor,
    validate_selection_binding,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _source(tmp_path):
    path = tmp_path / "source.gds"
    path.write_bytes(b"immutable source fixture")
    return build_source_layout_identity(str(path), top_cell="TOP", dbu_um=0.001)


def _path(transform=None, array=None):
    return normalize_occurrence_path(
        top_cell="TOP",
        segments=[
            {
                "parent_cell": "TOP",
                "child_cell": "DUT",
                "instance_ordinal": 3,
                "transform": transform
                or {
                    "displacement_dbu": [1000, 2000],
                    "angle_degrees": 90,
                    "mirror": True,
                    "magnification": 1,
                },
                "array": array,
            }
        ],
    )


def _manifest(tmp_path):
    return build_selection_manifest(
        source=_source(tmp_path),
        occurrence_path=_path(),
        layer=1,
        datatype=0,
        shape_fingerprint_sha256=_digest("shape"),
        edge_dbu=[[100, 200], [100, 500]],
        neighborhood_fingerprint_sha256=_digest("neighborhood"),
    )


def test_source_identity_hashes_exact_file_bytes(tmp_path) -> None:
    source = _source(tmp_path)

    assert source["layout_sha256"] == hashlib.sha256(
        b"immutable source fixture"
    ).hexdigest()
    assert source["source_mutable"] is False
    assert source["dbu_um"] == pytest.approx(0.001)


def test_occurrence_identity_is_deterministic_and_keeps_array_member() -> None:
    array = {
        "columns": 4,
        "rows": 2,
        "column": 2,
        "row": 1,
        "a_vector_dbu": [400, 0],
        "b_vector_dbu": [0, 600],
    }

    first = _path(array=array)
    second = _path(array=dict(reversed(list(array.items()))))

    assert first["occurrence_id"] == second["occurrence_id"]
    assert first["segments"][0]["array"]["column"] == 2
    assert first["segments"][0]["array"]["row"] == 1
    assert first["authoring_supported"] is True


@pytest.mark.parametrize("angle", [0, 90, 180, 270, 360])
@pytest.mark.parametrize("mirror", [False, True])
def test_orthogonal_unit_transform_is_authoring_supported(angle, mirror) -> None:
    result = normalize_transform_descriptor(
        {
            "displacement_dbu": [0, 0],
            "angle_degrees": angle,
            "mirror": mirror,
            "magnification": 1.0,
        }
    )

    assert result["authoring_supported"] is True
    assert result["unsupported_reasons"] == []


@pytest.mark.parametrize(
    ("angle", "magnification", "reason"),
    [(45, 1, "non_orthogonal_angle"), (0, 2, "non_unit_magnification")],
)
def test_complex_transform_remains_readable_but_blocks_authoring(
    angle, magnification, reason
) -> None:
    result = normalize_transform_descriptor(
        {
            "displacement_dbu": [0, 0],
            "angle_degrees": angle,
            "mirror": False,
            "magnification": magnification,
        }
    )

    assert result["authoring_supported"] is False
    assert reason in result["unsupported_reasons"]


def test_selection_manifest_normalizes_edge_direction_and_hash(tmp_path) -> None:
    forward = _manifest(tmp_path)
    reverse = build_selection_manifest(
        source=forward["source"],
        occurrence_path=forward["occurrence_path"],
        layer=1,
        datatype=0,
        shape_fingerprint_sha256=_digest("shape"),
        edge_dbu=[[100, 500], [100, 200]],
        neighborhood_fingerprint_sha256=_digest("neighborhood"),
    )

    assert forward["edge_dbu"] == [[100, 200], [100, 500]]
    assert forward["selection_manifest_sha256"] == reverse[
        "selection_manifest_sha256"
    ]
    assert forward["fuzzy_retarget_allowed"] is False
    assert forward["shape_ordinal"] == 0
    assert forward["duplicate_geometry_count"] == 1


def test_selection_binding_passes_without_fuzzy_retarget(tmp_path) -> None:
    manifest = _manifest(tmp_path)

    result = validate_selection_binding(
        manifest,
        current_layout_sha256=manifest["source"]["layout_sha256"],
        current_shape_fingerprint_sha256=_digest("shape"),
        current_neighborhood_fingerprint_sha256=_digest("neighborhood"),
    )

    assert result["binding_verified"] is True
    assert result["fuzzy_retarget_performed"] is False
    assert result["production_ready"] is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("current_layout_sha256", _digest("changed-source"), "STALE_PCELLIZER_SOURCE"),
        (
            "current_shape_fingerprint_sha256",
            _digest("changed-shape"),
            "STALE_PCELLIZER_SHAPE",
        ),
        (
            "current_neighborhood_fingerprint_sha256",
            _digest("changed-neighborhood"),
            "STALE_PCELLIZER_NEIGHBORHOOD",
        ),
    ],
)
def test_selection_binding_fails_closed_on_stale_identity(
    tmp_path, field, value, code
) -> None:
    manifest = _manifest(tmp_path)
    arguments = {
        "current_layout_sha256": manifest["source"]["layout_sha256"],
        "current_shape_fingerprint_sha256": _digest("shape"),
        "current_neighborhood_fingerprint_sha256": _digest("neighborhood"),
    }
    arguments[field] = value

    with pytest.raises(AnalysisError) as caught:
        validate_selection_binding(manifest, **arguments)

    assert caught.value.code == code


def test_occurrence_path_rejects_discontinuous_parent_chain() -> None:
    with pytest.raises(AnalysisError) as caught:
        normalize_occurrence_path(
            top_cell="TOP",
            segments=[
                {
                    "parent_cell": "WRONG",
                    "child_cell": "DUT",
                    "instance_ordinal": 0,
                    "transform": {
                        "displacement_dbu": [0, 0],
                        "angle_degrees": 0,
                        "mirror": False,
                        "magnification": 1,
                    },
                }
            ],
        )

    assert caught.value.code == "DISCONTINUOUS_PCELLIZER_OCCURRENCE_PATH"


def test_array_member_out_of_range_is_rejected() -> None:
    with pytest.raises(AnalysisError) as caught:
        _path(
            array={
                "columns": 2,
                "rows": 1,
                "column": 2,
                "row": 0,
                "a_vector_dbu": [100, 0],
                "b_vector_dbu": [0, 0],
            }
        )

    assert caught.value.code == "INVALID_PCELLIZER_ARRAY_MEMBER"


def test_selection_recomputes_authoring_gate_from_path_segments(tmp_path) -> None:
    path = _path(
        transform={
            "displacement_dbu": [0, 0],
            "angle_degrees": 45,
            "mirror": False,
            "magnification": 1,
        }
    )
    path["authoring_supported"] = True

    manifest = build_selection_manifest(
        source=_source(tmp_path),
        occurrence_path=path,
        layer=1,
        datatype=0,
        shape_fingerprint_sha256=_digest("shape"),
        edge_dbu=[[0, 0], [0, 100]],
        neighborhood_fingerprint_sha256=_digest("neighborhood"),
    )

    assert manifest["authoring_supported"] is False


def test_nonregular_iterated_instance_is_readable_but_authoring_blocked() -> None:
    path = normalize_occurrence_path(
        top_cell="TOP",
        segments=[
            {
                "parent_cell": "TOP",
                "child_cell": "ITERATED",
                "instance_ordinal": 0,
                "transform": {
                    "displacement_dbu": [100, 200],
                    "angle_degrees": 0,
                    "mirror": False,
                    "magnification": 1,
                },
                "array": {
                    "columns": 3,
                    "rows": 1,
                    "column": 1,
                    "row": 0,
                    "a_vector_dbu": [0, 0],
                    "b_vector_dbu": [0, 0],
                    "regular": False,
                },
                "authoring_blockers": ["non_regular_iterated_instance"],
            }
        ],
    )

    assert path["segments"][0]["array"]["representation"] == "iterated_instance"
    assert path["authoring_supported"] is False


def test_selection_rejects_mutable_source_identity(tmp_path) -> None:
    source = _source(tmp_path)
    source["source_mutable"] = True

    with pytest.raises(AnalysisError) as caught:
        build_selection_manifest(
            source=source,
            occurrence_path=_path(),
            layer=1,
            datatype=0,
            shape_fingerprint_sha256=_digest("shape"),
            edge_dbu=[[0, 0], [0, 100]],
            neighborhood_fingerprint_sha256=_digest("neighborhood"),
        )

    assert caught.value.code == "MUTABLE_PCELLIZER_SOURCE_FORBIDDEN"
