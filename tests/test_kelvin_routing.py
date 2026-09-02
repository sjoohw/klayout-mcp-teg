from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.kelvin_routing import (
    build_kelvin_geometry_dbu,
    build_kelvin_routing_spec,
    geometry_box_counts,
    kelvin_routing_plan_result,
)
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.server import generate_kelvin_m1_teg


DIMENSION_SEMANTICS = "width_is_transverse_axis_length_is_longitudinal_axis"


def confirmed_spec():
    return build_kelvin_routing_spec(
        dimension_semantics=DIMENSION_SEMANTICS,
        confirm_routing_contract=True,
    )


def test_kelvin_plan_requires_explicit_dimension_confirmation() -> None:
    with pytest.raises(AnalysisError) as caught:
        build_kelvin_routing_spec(
            dimension_semantics=None,
            confirm_routing_contract=True,
        )

    assert caught.value.code == "KELVIN_DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED"


def test_kelvin_plan_preserves_confirmed_six_split_product_and_roles() -> None:
    result = kelvin_routing_plan_result(confirmed_spec())

    assert result["ok"] is True
    assert {(item["width_nm"], item["length_nm"]) for item in result["splits"]} == {
        (22, 300),
        (22, 1000),
        (100, 300),
        (100, 1000),
        (300, 300),
        (300, 1000),
    }
    assert result["pad_roles_left_to_right"] == [
        "SENSE+",
        "FORCE+",
        "FORCE-",
        "SENSE-",
    ]
    assert result["mesh"]["expansion_rail_counts"] == [1, 2, 4, 6]
    assert result["mesh"]["clear_space_nm"] == 700


def test_kelvin_geometry_matches_golden_cell_inventory_on_sln001_dbu() -> None:
    geometry = build_kelvin_geometry_dbu(confirmed_spec(), dbu_um=0.00025)
    counts = geometry_box_counts(geometry)

    assert counts["KELVIN_COMMON_VOLTAGE_SENSE_MESH"] == 290
    assert counts["KELVIN_K1_LOCAL_ROUTING_MESH"] == 142
    assert counts["KELVIN_K2_LOCAL_ROUTING_MESH"] == 140
    assert counts["KELVIN_K3_LOCAL_ROUTING_MESH"] == 140
    assert counts["KELVIN_K4_LOCAL_ROUTING_MESH"] == 142
    assert counts["KELVIN_K5_LOCAL_ROUTING_MESH"] == 142
    assert counts["KELVIN_K6_LOCAL_ROUTING_MESH"] == 140
    assert len(geometry["top_instances"]) == 6
    for cell in geometry["cells"].values():
        for left, bottom, right, top in cell["boxes_dbu"]:
            assert left < right
            assert bottom < top


def test_kelvin_six_split_set_rejects_missing_cartesian_combination() -> None:
    splits = [
        {"width_nm": width, "length_nm": length}
        for width, length in (
            (22, 300),
            (22, 1000),
            (100, 300),
            (100, 1000),
            (300, 300),
            (300, 300),
        )
    ]
    with pytest.raises(AnalysisError) as caught:
        build_kelvin_routing_spec(
            dimension_semantics=DIMENSION_SEMANTICS,
            confirm_routing_contract=True,
            splits=splits,
        )

    assert caught.value.code == "KELVIN_CARTESIAN_SPLIT_SET_REQUIRED"


def test_kelvin_custom_three_by_two_split_set_supports_long_lines() -> None:
    custom_splits = [
        {"width_nm": width_nm, "length_nm": length_nm}
        for width_nm, length_nm in (
            (24, 2000),
            (48, 3000),
            (100, 3000),
            (100, 2000),
            (48, 2000),
            (24, 3000),
        )
    ]
    spec = build_kelvin_routing_spec(
        dimension_semantics=DIMENSION_SEMANTICS,
        confirm_routing_contract=True,
        splits=custom_splits,
    )
    geometry = build_kelvin_geometry_dbu(spec, dbu_um=0.00025)

    assert {(split.width_nm, split.length_nm) for split in spec.splits} == {
        (24, 2000),
        (24, 3000),
        (48, 2000),
        (48, 3000),
        (100, 2000),
        (100, 3000),
    }
    assert len(geometry["top_instances"]) == 6
    for cell in geometry["cells"].values():
        for left, bottom, right, top in cell["boxes_dbu"]:
            assert left < right
            assert bottom < top


def test_generate_kelvin_rebuild_is_semantically_equal_to_golden(tmp_path: Path) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    reference = (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "SLN001_kelvin_m1"
        / "SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds"
    )
    if not reference.is_file():
        pytest.skip("Golden Kelvin reference is not present")
    work_directory = tmp_path / "output" / "kelvin-regression"
    output = work_directory / "regenerated.gds"
    result = generate_kelvin_m1_teg(
        template_gds_path=str(reference),
        output_gds_path=str(output),
        work_directory_path=str(work_directory),
        dimension_semantics=DIMENSION_SEMANTICS,
        confirm_routing_contract=True,
        reference_gds_path=str(reference),
        klayout_executable=str(executable),
    )

    assert result["ok"] is True
    assert output.is_file()
    assert result["removed_existing_kelvin_instance_count"] == 6
    assert result["top_cell_count"] == 1
    assert result["kelvin_direct_top_instance_count"] == 6
    assert result["fresh_reload_verified"] is True
    assert result["orthogonal_box_only_verified"] is True
    assert result["m1_component_count"] == 7
    assert result["reference_comparison"]["equivalent"] is True
    assert all(
        layer["geometry_xor_clean"]
        for layer in result["reference_comparison"]["layers"]
    )


def test_generate_kelvin_concurrent_same_target_preserves_one_winner(
    tmp_path: Path,
) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    reference = (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "SLN001_kelvin_m1"
        / "SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds"
    )
    if not reference.is_file():
        pytest.skip("Golden Kelvin reference is not present")
    work_directory = tmp_path / "output" / "kelvin-race"
    output = work_directory / "regenerated.gds"
    barrier = threading.Barrier(2)

    def generate() -> dict:
        barrier.wait()
        return generate_kelvin_m1_teg(
            template_gds_path=str(reference),
            output_gds_path=str(output),
            work_directory_path=str(work_directory),
            dimension_semantics=DIMENSION_SEMANTICS,
            confirm_routing_contract=True,
            reference_gds_path=str(reference),
            klayout_executable=str(executable),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: generate(), range(2)))

    assert sum(result["ok"] is True for result in results) == 1
    loser = next(result for result in results if result["ok"] is False)
    assert loser["code"] == "OUTPUT_ALREADY_EXISTS"
    assert output.is_file()
    assert output.stat().st_size > 0
    assert list(work_directory.glob(".klayout-stage-file-kelvin-*")) == []
