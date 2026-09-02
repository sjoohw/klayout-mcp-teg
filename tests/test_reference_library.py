import hashlib

import pytest

from klayout_mcp.errors import AnalysisError
import klayout_mcp.server as server_module
from klayout_mcp.reference_library import (
    ReferenceLibrary,
    load_reference_view_manifest,
    record_reference_gui_confirmation,
)


def _inventory(top_cell="TOP"):
    return {
        "ok": True,
        "layout": {
            "top_cell": top_cell,
            "top_cells": [top_cell],
            "dbu_um": 0.001,
            "klayout_version": "0.30.10",
            "top_bbox_um": [0.0, 0.0, 20.0, 10.0],
            "cell_count": 2,
        },
        "layers": [
            {
                "layer": 10,
                "datatype": 0,
                "mapped_roles": ["m1"],
                "used": True,
            }
        ],
        "input_layout_modified": False,
        "layout_read_count": 1,
    }


def _register(tmp_path, *, node="LN14LPU"):
    source = tmp_path / f"{node}.gds"
    source.write_bytes(b"stable-reference-gds")
    library = ReferenceLibrary(tmp_path / "library")
    asset = library.register(
        source_layout_path=str(source),
        process_node=node,
        process_option="logic",
        process_revision="r1",
        inventory=_inventory(),
        profile_name="internal",
        profile_version="v7",
        layermap_sha256="a" * 64,
        purpose_tags=["mesh", "transistor"],
    )
    return source, library, asset


def _template(*, context="b" * 64, deviation=0.04):
    return {
        "template_id": "m1-space-contact",
        "rule_id": "M1.S.1",
        "violation_type": "spacing",
        "layer_tokens": ["m1"],
        "context_signature": context,
        "max_deviation_um": deviation,
        "reference_bbox_um": [1.0, 1.0, 2.0, 2.0],
        "description": "Known contact-neck spacing precedent",
    }


def _marker(index, *, context="b" * 64, deviation=0.03, concern="contact_array"):
    return {
        "marker_id": f"marker-{index}",
        "process_node": "LN14LPU",
        "process_option": "logic",
        "process_revision": "r1",
        "profile_name": "internal",
        "profile_version": "v7",
        "layermap_sha256": "a" * 64,
        "concern": concern,
        "rule_id": "M1.S.1",
        "violation_type": "spacing",
        "layer_tokens": ["m1"],
        "context_signature": context,
        "deviation_um": deviation,
        "bbox_um": [float(index), 0.0, float(index) + 0.1, 0.1],
    }


def _confirmed_precedent(tmp_path):
    _, library, asset = _register(tmp_path)
    prepared = library.prepare_view(
        reference_id=asset["reference_id"],
        concern="contact_array",
        usage_mode="reference_precedent",
        roi_bbox_um=[0.0, 0.0, 5.0, 5.0],
        relevant_layers=["m1"],
        accepted_marker_templates=[_template()],
    )
    view = prepared["view"]
    library.record_gui_confirmation(view_id=view["view_id"])
    selection = library.confirm_view(view_id=view["view_id"])["selection"]
    return library, asset, prepared, selection


def test_reference_asset_is_content_addressed_and_source_independent(tmp_path) -> None:
    source, library, asset = _register(tmp_path)
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    source.write_bytes(b"changed-original")
    loaded = library.load_asset(asset["reference_id"])

    assert loaded["source"]["layout_sha256"] == expected_hash
    assert loaded["source"]["mutable_original_is_runtime_dependency"] is False
    assert loaded["source_geometry_modified"] is False
    assert loaded["flattening_performed"] is False
    assert open(loaded["source"]["stored_path"], "rb").read() == b"stable-reference-gds"


def test_list_reference_layouts_filters_process_node(tmp_path) -> None:
    _, library, first = _register(tmp_path, node="LN14LPU")
    source = tmp_path / "LN08LPU.gds"
    source.write_bytes(b"another-reference")
    second = library.register(
        source_layout_path=str(source),
        process_node="LN08LPU",
        inventory=_inventory(),
    )

    listed = library.list_assets(process_node="LN14LPU")

    assert listed["reference_count"] == 1
    assert listed["references"][0]["reference_id"] == first["reference_id"]
    assert second["reference_id"] != first["reference_id"]


def test_reference_cannot_be_confirmed_before_klayout_gui_action(tmp_path) -> None:
    _, library, asset = _register(tmp_path)
    prepared = library.prepare_view(
        reference_id=asset["reference_id"],
        concern="routing_mesh",
        usage_mode="normal_style",
        roi_bbox_um=[0.0, 0.0, 5.0, 5.0],
        relevant_layers=["m1"],
    )

    with pytest.raises(AnalysisError) as error:
        library.confirm_view(view_id=prepared["view"]["view_id"])

    assert error.value.code == "REFERENCE_GUI_CONFIRMATION_REQUIRED"


def test_manifest_helper_records_local_gui_confirmation(tmp_path) -> None:
    _, _, _, selection = _confirmed_precedent(tmp_path)
    view_path = selection["view"]["view_manifest_path"]

    loaded = load_reference_view_manifest(view_path)
    confirmation = record_reference_gui_confirmation(view_manifest_path=view_path)

    assert loaded["view_id"] == confirmation["view_id"]
    assert confirmation["local_gui_user_action"] is True
    assert confirmation["identity_attested_by_trusted_host"] is False


def test_reference_marker_count_growth_is_allowed_per_matching_motif(tmp_path) -> None:
    library, _, _, selection = _confirmed_precedent(tmp_path)

    result = library.classify_markers(
        selection_id=selection["selection_id"],
        candidate_markers=[_marker(index) for index in range(1, 8)],
    )

    assert result["ok"] is True
    assert result["classification"] == "REF_ACCEPTED"
    assert result["summary"] == {
        "candidate_marker_count": 7,
        "ref_accepted_count": 7,
        "review_needed_count": 0,
        "reference_template_count": 1,
        "marker_count_growth_allowed": True,
    }
    assert result["drc_clean"] is False
    assert result["reference_exceptions_present"] is True


@pytest.mark.parametrize(
    ("marker", "reason"),
    [
        (_marker(1, context="c" * 64), "no_matching_reference_motif"),
        (_marker(1, deviation=0.05), "no_matching_reference_motif"),
        (_marker(1, concern="pad_joint"), "concern_mismatch"),
    ],
)
def test_unmatched_reference_marker_requests_nonblocking_review(tmp_path, marker, reason) -> None:
    library, _, _, selection = _confirmed_precedent(tmp_path)

    result = library.classify_markers(
        selection_id=selection["selection_id"], candidate_markers=[marker]
    )

    assert result["ok"] is True
    assert result["classification"] == "REVIEW_NEEDED"
    assert result["drawing_blocked"] is False
    assert result["advisory_only_until_process_adapter_validated"] is True
    assert result["markers"][0]["classification"] == "REVIEW_NEEDED"
    assert result["markers"][0]["reasons"] == [reason]


def test_nested_occurrence_requires_explicit_top_view_bbox(tmp_path) -> None:
    _, library, asset = _register(tmp_path)
    segment = {
        "parent_cell": "TOP",
        "child_cell": "UNIT",
        "instance_ordinal": 0,
        "transform": {
            "displacement_dbu": [1000, 2000],
            "angle_degrees": 90,
            "mirror": True,
            "magnification": 1.0,
        },
        "array": {
            "columns": 3,
            "rows": 2,
            "column": 2,
            "row": 1,
            "a_vector_dbu": [5000, 0],
            "b_vector_dbu": [0, 4000],
            "regular": True,
        },
        "authoring_blockers": [],
    }

    with pytest.raises(AnalysisError) as error:
        library.prepare_view(
            reference_id=asset["reference_id"],
            concern="device_geometry",
            usage_mode="normal_style",
            roi_bbox_um=[0.0, 0.0, 1.0, 1.0],
            relevant_layers=["m1"],
            occurrence_segments=[segment],
        )

    assert error.value.code == "REFERENCE_VIEW_BBOX_REQUIRED"

    prepared = library.prepare_view(
        reference_id=asset["reference_id"],
        concern="device_geometry",
        usage_mode="normal_style",
        roi_bbox_um=[0.0, 0.0, 1.0, 1.0],
        view_bbox_um=[10.0, 20.0, 11.0, 21.0],
        relevant_layers=["m1"],
        occurrence_segments=[segment],
    )
    occurrence = prepared["view"]["occurrence_path"]
    assert occurrence["segments"][0]["transform"]["mirror"] is True
    assert occurrence["segments"][0]["array"]["column"] == 2
    assert prepared["view"]["view_bbox_um"] == [10.0, 20.0, 11.0, 21.0]


def test_normal_style_cannot_hide_drc_markers(tmp_path) -> None:
    _, library, asset = _register(tmp_path)

    with pytest.raises(AnalysisError) as error:
        library.prepare_view(
            reference_id=asset["reference_id"],
            concern="contact_array",
            usage_mode="normal_style",
            roi_bbox_um=[0.0, 0.0, 5.0, 5.0],
            relevant_layers=["m1"],
            accepted_marker_templates=[_template()],
        )

    assert error.value.code == "REFERENCE_PRECEDENT_MODE_REQUIRED"


def test_generic_drawing_revalidates_and_cites_confirmed_reference(
    tmp_path, monkeypatch
) -> None:
    library, _, _, selection = _confirmed_precedent(tmp_path)
    captured = {}

    def fake_draw(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "reference_citations": kwargs["reference_citations"]}

    monkeypatch.setattr(server_module, "draw_manhattan_layout_service", fake_draw)
    result = server_module.draw_manhattan_layout(
        output_layout_path=str(tmp_path / "out.gds"),
        dbu_um=0.001,
        top_cell="TOP",
        cells=["TOP"],
        layers=[{"name": "m1", "layer": 10, "datatype": 0}],
        operations=[
            {
                "type": "add_box",
                "cell": "TOP",
                "layer": "m1",
                "box_um": [0.0, 0.0, 1.0, 1.0],
            }
        ],
        reference_selection_ids=[selection["selection_id"]],
        reference_library_root=str(library.root),
        confirm_nonproduction=True,
    )

    assert result["ok"] is True
    assert captured["reference_citations"][0]["selection_id"] == selection["selection_id"]
    assert captured["reference_citations"][0]["usage_mode"] == "reference_precedent"
    assert captured["reference_citations"][0]["citation"]["layout_sha256"]
