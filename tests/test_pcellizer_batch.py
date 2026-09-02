from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_batch import plan_pcellizer_split_batch
from klayout_mcp.workflow_manifest import canonical_sha256


def _recipe() -> dict:
    core = {
        "schema_version": 1,
        "kind": "PCellizerSingleShapeRecipe",
        "snapshot_package_sha256": "a" * 64,
        "parameter_capture_sha256": "b" * 64,
        "parameter_intent_sha256": "c" * 64,
        "target": {
            "cell": "DUT",
            "occurrence_path": {},
            "layer": 10,
            "datatype": 0,
            "shape_identity": {},
            "scope": "current_occurrence",
        },
        "parameter": {
            "parameter_name": "gate_length",
            "dbu_um": 0.001,
            "min_dbu": 50,
            "nominal_dbu": 100,
            "max_dbu": 200,
            "step_dbu": 10,
            "manufacturing_grid_dbu": 5,
        },
        "operations": [
            {
                "operator": "resize_direct_box_between_captured_edges",
                "local_axis": "x",
                "endpoint_edge_indices": [3, 1],
                "endpoint_edges_top_dbu": [],
                "anchor_policy": "p1_fixed",
                "dependency_policy": "fixed_unselected_geometry",
            }
        ],
        "hierarchy_strategy": {},
        "verification_required": [],
        "source_geometry_modified": False,
        "production_ready": False,
    }
    return {**core, "pcellizer_recipe_sha256": canonical_sha256(core)}


def test_plan_one_csv_row_with_default_filename() -> None:
    result = plan_pcellizer_split_batch(
        recipe=_recipe(), table_text="split_id,gate_length_um\nnominal,0.100\n"
    )

    assert result["summary"] == {
        "row_count": 1,
        "unique_variant_count": 1,
        "output_mode": "one_standalone_gds_per_row",
        "transaction_policy": "all_or_nothing",
    }
    assert result["rows"][0]["output_filename"] == "nominal.gds"
    assert result["rows"][0]["parameters_dbu"] == {"gate_length": 100}


def test_excel_paste_tsv_supports_explicit_nm_and_metadata() -> None:
    text = (
        "split_id\tgate_length_nm\toutput_filename\tmeta.wafer_split\n"
        "s01\t50\tshort.gds\tA\n"
        "s02\t200\tlong.gds\tB\n"
    )
    result = plan_pcellizer_split_batch(recipe=_recipe(), table_text=text)

    assert result["delimiter"] == "tab"
    assert [row["parameters_dbu"]["gate_length"] for row in result["rows"]] == [50, 200]
    assert result["rows"][1]["metadata"] == {"wafer_split": "B"}


def test_single_excel_column_and_common_case_parenthesis_header() -> None:
    result = plan_pcellizer_split_batch(
        recipe=_recipe(), table_text="Gate Length (nm)\n50\n100\n"
    )

    assert [row["split_id"] for row in result["rows"]] == ["split_001", "split_002"]
    assert [row["parameters_dbu"]["gate_length"] for row in result["rows"]] == [50, 100]


@pytest.mark.parametrize("unsafe", ["COM1.split.gds", "NUL.v1.gds"])
def test_windows_device_filenames_with_extra_dots_are_rejected(unsafe: str) -> None:
    with pytest.raises(AnalysisError) as error:
        plan_pcellizer_split_batch(
            recipe=_recipe(),
            table_text=f"split_id,gate_length,output_filename\na,0.1,{unsafe}\n",
        )
    assert error.value.code == "PCELLIZER_SPLIT_TABLE_INVALID_ROWS"


def test_empty_metadata_name_is_rejected() -> None:
    with pytest.raises(AnalysisError) as error:
        plan_pcellizer_split_batch(
            recipe=_recipe(), table_text="gate_length,meta.\n0.1,x\n"
        )
    assert error.value.code == "INVALID_PCELLIZER_METADATA_HEADER"


def test_duplicate_parameter_sets_create_two_outputs_but_one_unique_variant() -> None:
    result = plan_pcellizer_split_batch(
        recipe=_recipe(),
        table_text=(
            "split_id,gate_length\n"
            "repeat_a,0.1\n"
            "repeat_b,0.1\n"
        ),
    )

    assert result["summary"]["row_count"] == 2
    assert result["summary"]["unique_variant_count"] == 1
    assert result["rows"][0]["variant_key"] == result["rows"][1]["variant_key"]


def test_table_path_preserves_raw_file_hash_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "splits.csv"
    raw = "\ufeffsplit_id,gate_length_nm\na,100\n".encode("utf-8")
    path.write_bytes(raw)

    result = plan_pcellizer_split_batch(recipe=_recipe(), table_path=str(path))

    assert result["table_source"]["kind"] == "file"
    assert result["table_source"]["size_bytes"] == len(raw)
    assert result["rows"][0]["parameters_dbu"]["gate_length"] == 100


def test_invalid_rows_are_reported_together() -> None:
    with pytest.raises(AnalysisError) as error:
        plan_pcellizer_split_batch(
            recipe=_recipe(),
            table_text=(
                "split_id,gate_length_nm\n"
                "too_small,40\n"
                "off_step,55\n"
            ),
        )

    assert error.value.code == "PCELLIZER_SPLIT_TABLE_INVALID_ROWS"
    assert error.value.details["error_count"] == 2
    assert [item["row_number"] for item in error.value.details["row_errors"]] == [2, 3]


@pytest.mark.parametrize(
    "table_text,expected_code",
    [
        ("split_id,gate_length,unexpected\na,0.1,x\n", "UNKNOWN_PCELLIZER_SPLIT_COLUMN"),
        ("split_id,gate_length\na,0.1\na,0.2\n", "PCELLIZER_SPLIT_TABLE_INVALID_ROWS"),
        (
            "split_id,gate_length,output_filename\na,0.1,../escape.gds\n",
            "PCELLIZER_SPLIT_TABLE_INVALID_ROWS",
        ),
    ],
)
def test_unknown_duplicate_and_unsafe_inputs_fail_closed(table_text: str, expected_code: str) -> None:
    with pytest.raises(AnalysisError) as error:
        plan_pcellizer_split_batch(recipe=_recipe(), table_text=table_text)
    assert error.value.code == expected_code


def test_table_input_is_exclusive_and_recipe_hash_is_verified() -> None:
    with pytest.raises(AnalysisError) as exclusive:
        plan_pcellizer_split_batch(
            recipe=_recipe(), table_path="a.csv", table_text="split_id,gate_length\na,0.1"
        )
    assert exclusive.value.code == "PCELLIZER_SPLIT_TABLE_INPUT_EXCLUSIVE"

    tampered = _recipe()
    tampered["parameter"]["max_dbu"] = 999
    with pytest.raises(AnalysisError) as hash_error:
        plan_pcellizer_split_batch(
            recipe=tampered, table_text="split_id,gate_length\na,0.1"
        )
    assert hash_error.value.code == "PCELLIZER_RECIPE_HASH_MISMATCH"
