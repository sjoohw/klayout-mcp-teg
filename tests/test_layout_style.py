from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from klayout_mcp import style_service
from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.server import extract_layout_style


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_extract_kelvin_style_is_geometry_based_and_deterministic(tmp_path: Path) -> None:
    try:
        executable = find_klayout_executable()
    except AnalysisError:
        pytest.skip("KLayout executable is not installed")

    layout = PROJECT_ROOT / "examples" / "gds" / "kelvin_m1_w24_48_100nm_l2_3um.gds"
    layermap = PROJECT_ROOT / "examples" / "settings" / "sln001_kelvin_reference_layermap.yaml"
    if not layout.is_file():
        pytest.skip("Bundled Kelvin example GDS is not present")
    output = tmp_path / "style.json"

    first = extract_layout_style(
        layout_path=str(layout),
        top_cell="SLN001_PADSET",
        layermap_path=str(layermap),
        histogram_limit=12,
        output_profile_path=str(output),
        klayout_executable=str(executable),
    )
    second = extract_layout_style(
        layout_path=str(layout),
        top_cell="SLN001_PADSET",
        layermap_path=str(layermap),
        histogram_limit=12,
        klayout_executable=str(executable),
    )

    assert first["ok"] is True
    assert first["output_profile_fresh_read_verified"] is True
    assert first["style_profile_sha256"] == second["style_profile_sha256"]
    profile = first["style_profile"]
    assert "layout_path" not in profile["source"]
    assert "layermap_path" not in profile["source"]
    assert profile["source"]["runtime_paths_in_profile"] is False
    assert len(profile["source"]["layermap_sha256"]) == 64
    assert profile["layout"]["dbu_um"] == 0.00025
    assert profile["layout"]["top_bbox_um"] == [0.0, 0.0, 2000.0, 54.0]
    assert profile["hierarchy_style"]["flattening_performed"] is False
    m1 = next(
        item for item in profile["layer_styles"] if item["layer_token"] == "15/0"
    )
    assert m1["mapped_roles"] == ["m1"]
    assert m1["orthogonal_geometry_verified"] is True
    assert m1["merged_topology"]["hole_count"] > 1000
    assert profile["inference_boundaries"]["role_inference_performed"] is False
    assert profile["inference_boundaries"]["design_rule_inference_performed"] is False
    assert any(
        item["descriptor_id"] == "observed-layer-15-0"
        for item in profile["reference_style_descriptors"]
    )
    assert profile["production_ready"] is False


def test_style_profile_never_overwrites_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "style.json"
    output.write_text("keep", encoding="utf-8")

    result = extract_layout_style(
        layout_path="missing.gds",
        output_profile_path=str(output),
    )

    assert result["ok"] is False
    assert result["code"] == "OUTPUT_ALREADY_EXISTS"
    assert output.read_text(encoding="utf-8") == "keep"


def test_style_profile_concurrent_publish_preserves_one_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "style.json"
    barrier = threading.Barrier(2)
    real_publish = style_service.publish_new_file

    def synchronized_publish(staged_path, final_path):
        barrier.wait()
        return real_publish(staged_path, final_path)

    monkeypatch.setattr(style_service, "publish_new_file", synchronized_publish)

    def write_profile(writer: int) -> str:
        try:
            style_service._atomic_json(output, {"writer": writer})
        except AnalysisError as exc:
            assert exc.code == "OUTPUT_ALREADY_EXISTS"
            return "already_exists"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write_profile, (1, 2)))

    assert outcomes.count("published") == 1
    assert outcomes.count("already_exists") == 1
    assert json.loads(output.read_text(encoding="utf-8")) in (
        {"writer": 1},
        {"writer": 2},
    )
    assert list(tmp_path.glob(".klayout-stage-file-style-*.json")) == []
