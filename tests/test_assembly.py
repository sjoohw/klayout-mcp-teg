from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from klayout_mcp.assembly import plan_teg_assembly, plan_teg_dut_sequence
from klayout_mcp.errors import AnalysisError
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.server import analyze_padset, assemble_teg


DEVICE_SPECIFIC_W_L = "device_specific_w_l"


def _sequence_slots(*, resolved: bool = False) -> list[dict]:
    status = "resolved" if resolved else "unresolved"
    return [
        {
            "site": site,
            "origin_um": [80.0 * site, 30.0],
            "source_pad": site,
            "drain_pad": site + 1,
            "gate_pad": 23 if site % 2 else 24,
            "body_pad": 25,
            "landings": {
                role: {"status": status}
                for role in ("source", "drain", "gate", "body")
            },
        }
        for site in range(1, 22)
    ]


def test_plan_teg_assembly_with_single_config(tmp_path) -> None:
    padset_file = tmp_path / "padset.gds"
    padset_file.write_bytes(b"dummy")
    layermap_file = tmp_path / "layers.yaml"
    layermap_file.write_text("layers:\n  m1:\n    layer: 1\n    datatype: 0\n", encoding="utf-8")
    out_gds = tmp_path / "teg_out.gds"

    sweep = [{"w_um": 1.2, "l_um": 0.12, "routed_device_count": 8}]
    plan = plan_teg_assembly(
        padset_path=str(padset_file),
        layermap_path=str(layermap_file),
        dut_sweep=sweep,
        output_gds_path=str(out_gds),
    )

    assert plan.total_sites == 21
    assert len(plan.dut_sweep) == 21
    assert plan.dut_sweep[0]["parameters"]["w_um"] == 1.2
    assert plan.dut_sweep[20]["site"] == 21


def test_plan_teg_assembly_sweep_count_mismatch(tmp_path) -> None:
    padset_file = tmp_path / "padset.gds"
    padset_file.write_bytes(b"dummy")
    layermap_file = tmp_path / "layers.yaml"
    layermap_file.write_text("layers:\n  m1:\n    layer: 1\n    datatype: 0\n", encoding="utf-8")

    # 5 configs provided, but 21 expected
    sweep = [{"w_um": 1.0} for _ in range(5)]
    with pytest.raises(AnalysisError) as exc:
        plan_teg_assembly(
            padset_path=str(padset_file),
            layermap_path=str(layermap_file),
            dut_sweep=sweep,
            output_gds_path="out.gds",
        )
    assert exc.value.code == "SWEEP_COUNT_MISMATCH"


def test_plan_teg_assembly_selects_variable_dut_count_on_fixed_padset(tmp_path) -> None:
    padset_file = tmp_path / "padset.gds"
    padset_file.write_bytes(b"dummy")
    layermap_file = tmp_path / "layers.yaml"
    layermap_file.write_text("layers:\n  m1: [1, 0]\n", encoding="utf-8")

    plan = plan_teg_assembly(
        padset_path=str(padset_file),
        layermap_path=str(layermap_file),
        dut_sweep=[{"l_um": 0.12}],
        dut_site_indices=[2, 10, 20],
        output_gds_path=str(tmp_path / "selected.gds"),
    )

    assert plan.total_sites == 21
    assert plan.selected_sites == (2, 10, 20)
    assert [item["site"] for item in plan.dut_sweep] == [2, 10, 20]


def test_sequence_plan_accepts_selected_dut_subset() -> None:
    result = plan_teg_dut_sequence(
        _sequence_slots(resolved=True),
        [
            {"site": 3, "parameters": {"l_um": 0.1}},
            {"site": 11, "parameters": {"l_um": 0.2}},
            {"site": 19, "parameters": {"l_um": 0.1}},
        ],
    )

    assert result["available_site_count"] == 21
    assert result["selected_site_count"] == 3
    assert result["selected_sites"] == [3, 11, 19]
    assert [item["site"] for item in result["site_plan"]] == [3, 11, 19]
    assert result["variant_count"] == 2


def test_plan_teg_dut_sequence_validation() -> None:
    slots = _sequence_slots(resolved=True)
    site_params = [{"site": i, "parameters": {"w_um": 1.0 + i * 0.1}} for i in range(1, 22)]

    result = plan_teg_dut_sequence(slots, site_params)
    assert result["ok"] is True
    assert result["production_ready"] is False
    assert result["total_sites"] == 21
    assert len(result["site_plan"]) == 21
    assert result["variant_count"] == 21
    assert result["all_landings_resolved"] is True


def test_sequence_plan_reuses_variants_and_is_input_order_independent() -> None:
    slots = _sequence_slots(resolved=True)
    site_params = [
        {"site": site, "parameters": {"l_um": 0.1 if site <= 10 else 0.2}}
        for site in range(1, 22)
    ]

    forward = plan_teg_dut_sequence(slots, site_params)
    reverse = plan_teg_dut_sequence(list(reversed(slots)), list(reversed(site_params)))

    assert forward == reverse
    assert forward["variant_count"] == 2
    assert forward["site_plan"][0]["variant_id"] == "VARIANT_001"
    assert forward["site_plan"][9]["variant_id"] == "VARIANT_001"
    assert forward["site_plan"][10]["variant_id"] == "VARIANT_002"


def test_sequence_plan_reports_unresolved_landings() -> None:
    result = plan_teg_dut_sequence(
        _sequence_slots(resolved=False),
        [{"site": site, "parameters": {}} for site in range(1, 22)],
    )

    assert result["ok"] is True
    assert result["all_landings_resolved"] is False
    assert len(result["unresolved_landings"]) == 21
    assert result["site_plan"][0]["landing_readiness"] == {
        "all_resolved": False,
        "role_status": {
            "source": "unresolved",
            "drain": "unresolved",
            "gate": "unresolved",
            "body": "unresolved",
        },
    }


def test_sequence_plan_treats_null_landing_as_unresolved() -> None:
    slots = _sequence_slots(resolved=True)
    slots[0]["landings"]["source"] = None

    result = plan_teg_dut_sequence(
        slots,
        [{"site": site, "parameters": {}} for site in range(1, 22)],
    )

    assert result["all_landings_resolved"] is False
    assert result["site_plan"][0]["landing_readiness"]["role_status"]["source"] == (
        "unresolved"
    )


@pytest.mark.parametrize(
    "origin_um",
    [None, [], [1.0], "1,2", [True, 2.0], [float("nan"), 2.0]],
)
def test_sequence_plan_rejects_invalid_slot_origin(origin_um: object) -> None:
    slots = _sequence_slots(resolved=True)
    slots[0]["origin_um"] = origin_um

    with pytest.raises(AnalysisError) as caught:
        plan_teg_dut_sequence(
            slots,
            [{"site": site, "parameters": {}} for site in range(1, 22)],
        )

    assert caught.value.code == "INVALID_DUT_SLOT_ORIGIN"
    assert caught.value.details["site"] == 1
    assert caught.value.next_action


def test_sequence_plan_rejects_wrong_pad_mapping() -> None:
    slots = _sequence_slots(resolved=True)
    slots[0]["gate_pad"] = 24

    with pytest.raises(AnalysisError) as caught:
        plan_teg_dut_sequence(
            slots,
            [{"site": site, "parameters": {}} for site in range(1, 22)],
        )

    assert caught.value.code == "DUT_PAD_MAPPING_MISMATCH"
    assert caught.value.details["mismatches"]["gate_pad"] == {
        "expected": 23,
        "actual": 24,
    }


def test_sequence_plan_rejects_unknown_parameter() -> None:
    site_params = [{"site": site, "parameters": {}} for site in range(1, 22)]
    site_params[0]["parameters"] = {"not_a_real_parameter": -123}

    with pytest.raises(AnalysisError) as caught:
        plan_teg_dut_sequence(_sequence_slots(resolved=True), site_params)

    assert caught.value.code == "UNKNOWN_DUT_PARAMETER"
    assert caught.value.details["unknown_parameters"] == ["not_a_real_parameter"]


def test_sequence_plan_rejects_topology_change() -> None:
    site_params = [{"site": site, "parameters": {}} for site in range(1, 22)]
    site_params[1]["parameters"] = {"array_cols": 7}

    with pytest.raises(AnalysisError) as caught:
        plan_teg_dut_sequence(_sequence_slots(resolved=True), site_params)

    assert caught.value.code == "DUT_TOPOLOGY_MISMATCH"


def test_plan_rejects_existing_or_input_output(tmp_path) -> None:
    padset = tmp_path / "padset.gds"
    padset.write_bytes(b"dummy")
    layermap = tmp_path / "layers.yaml"
    layermap.write_text("layers:\n  m1: [1, 0]\n", encoding="utf-8")
    existing = tmp_path / "existing.gds"
    existing.write_bytes(b"keep")

    with pytest.raises(AnalysisError) as exc:
        plan_teg_assembly(str(padset), str(layermap), [{}], str(existing))
    assert exc.value.code == "OUTPUT_ALREADY_EXISTS"
    assert existing.read_bytes() == b"keep"

    with pytest.raises(AnalysisError) as exc:
        plan_teg_assembly(str(padset), str(layermap), [{}], str(padset))
    assert exc.value.code == "OUTPUT_CONFLICTS_WITH_INPUT"
    assert padset.read_bytes() == b"dummy"


def test_plan_rejects_fractional_integer_parameter(tmp_path) -> None:
    padset = tmp_path / "padset.gds"
    padset.write_bytes(b"dummy")
    layermap = tmp_path / "layers.yaml"
    layermap.write_text("layers:\n  m1: [1, 0]\n", encoding="utf-8")

    with pytest.raises(AnalysisError) as exc:
        plan_teg_assembly(
            str(padset),
            str(layermap),
            [{"array_rows": 2.5}],
            str(tmp_path / "out.gds"),
        )

    assert exc.value.code == "INVALID_SITE_PARAMETER"
    assert exc.value.details["cause_code"] == "INVALID_DUT_INTEGER_PARAMETER"


def test_plan_rejects_array_geometry_outside_device_window(tmp_path) -> None:
    padset = tmp_path / "padset.gds"
    padset.write_bytes(b"dummy")
    layermap = tmp_path / "layers.yaml"
    layermap.write_text("layers:\n  m1: [1, 0]\n", encoding="utf-8")

    with pytest.raises(AnalysisError) as exc:
        plan_teg_assembly(
            str(padset),
            str(layermap),
            [{"array_cols": 8, "pitch_x_um": 20.0}],
            str(tmp_path / "out.gds"),
        )

    assert exc.value.code == "INVALID_SITE_PARAMETER"
    assert exc.value.details["cause_code"] == "DEVICE_EXCEEDS_WINDOW"


def test_conceptual_assembly_roundtrip_is_truthful_and_non_destructive(
    tmp_path,
) -> None:
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    padset = tmp_path / "padset.gds"
    layermap = tmp_path / "layers.yaml"
    output = tmp_path / "conceptual-teg.gds"
    fixture_script = Path(__file__).parent / "fixtures" / "create_synthetic_padset.py"
    completed = subprocess.run(
        [
            str(executable),
            "-b",
            "-r",
            str(fixture_script),
            "-rd",
            f"output_path={padset}",
            "-rd",
            "landing_routes=1",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    layermap.write_text(
        """layers:
  m1: [10, 2]
  active: [1, 0]
  poly: [2, 0]
  contact: [3, 0]
  text: [100, 0]
""",
        encoding="utf-8",
    )
    input_before = hashlib.sha256(padset.read_bytes()).hexdigest()

    analysis = analyze_padset(
        str(padset),
        str(layermap),
        klayout_executable=str(executable),
    )
    assert analysis["ok"] is True
    assert analysis["padset"]["snapshot_sha256"] == input_before
    sequence = plan_teg_dut_sequence(
        analysis["dut_slots"],
        [
            {"site": site, "parameters": {"l_um": 0.12}}
            for site in range(1, 22)
        ],
    )
    assert sequence["ok"] is True
    assert sequence["variant_count"] == 1
    assert len(sequence["site_plan"]) == 21

    result = assemble_teg(
        str(padset),
        str(layermap),
        str(output),
        [{"l_um": 0.12}],
        confirm_conceptual_export=True,
        dimension_semantics=DEVICE_SPECIFIC_W_L,
        klayout_executable=str(executable),
    )

    assert result["ok"] is True
    assert result["production_ready"] is False
    assert result["geometry_status"] == "conceptual_scaffold"
    assert result["roundtrip_verified"] is True
    assert result["pcell_dependency_count"] == 0
    assert result["variant_roundtrip"] == []
    assert result["teg_label"] == {
        "string": "TEG_DUT_ARRAY_V1",
        "rotation_degrees": 90,
        "mirrored": False,
        "roundtrip_verified": True,
    }
    assert result["direct_instance_count"] == 0
    assert result["electrical_connectivity_verified"] is False
    assert (
        result["known_terminal_state"]
        == "canonical_conceptual_geometry_with_reported_internal_opens"
    )
    assert result["assembled_sites"] == 21
    assert result["variant_count"] == 1
    assert result["created_dut_cells"] == ["DUT_VARIANT_001"]
    assert len(result["site_variants"]) == 21
    assert {item["variant_id"] for item in result["site_variants"]} == {
        "VARIANT_001"
    }
    assert result["variants"][0]["shape_counts"]["m1"] == 28
    assert {
        item["net"]
        for item in result["variants"][0]["m1_connectivity"]["open_nets"]
    } == {"source", "drain"}
    assert [item["origin_um"] for item in result["site_variants"]] == [
        item["origin_um"] for item in analysis["dut_slots"]
    ]
    assert result["unresolved_padset_landings"] == analysis["m1_connectivity"][
        "unresolved_landings"
    ]
    assert result["warning"]
    assert output.is_file()
    assert hashlib.sha256(padset.read_bytes()).hexdigest() == input_before

    output_before = output.read_bytes()
    repeated = assemble_teg(
        str(padset),
        str(layermap),
        str(output),
        [{"l_um": 0.12}],
        confirm_conceptual_export=True,
        dimension_semantics=DEVICE_SPECIFIC_W_L,
        klayout_executable=str(executable),
    )
    assert repeated["ok"] is False
    assert repeated["code"] == "OUTPUT_ALREADY_EXISTS"
    assert output.read_bytes() == output_before

    selected_output = tmp_path / "conceptual-teg-selected.gds"
    selected = assemble_teg(
        str(padset),
        str(layermap),
        str(selected_output),
        [{"l_um": 0.12}],
        dut_site_indices=[2, 10, 20],
        export_static=False,
        confirm_conceptual_export=True,
        dimension_semantics=DEVICE_SPECIFIC_W_L,
        klayout_executable=str(executable),
    )

    assert selected["ok"] is True
    assert selected["total_sites"] == 21
    assert selected["assembled_sites"] == 3
    assert selected["selected_sites"] == [2, 10, 20]
    assert selected["direct_instance_count"] == 28
    assert [item["site"] for item in selected["site_variants"]] == [2, 10, 20]

    editable_output = tmp_path / "conceptual-teg-editable.gds"
    three_variant_sweep = [
        {"l_um": 0.1 if site <= 7 else 0.2 if site <= 14 else 0.3}
        for site in range(1, 22)
    ]
    editable = assemble_teg(
        str(padset),
        str(layermap),
        str(editable_output),
        three_variant_sweep,
        export_static=False,
        confirm_conceptual_export=True,
        dimension_semantics=DEVICE_SPECIFIC_W_L,
        klayout_executable=str(executable),
    )

    assert editable["ok"] is True
    assert editable["export_static"] is False
    assert editable["assembled_sites"] == 21
    assert editable["variant_count"] == 3
    assert editable["created_dut_cells"] == [
        "DUT_VARIANT_001",
        "DUT_VARIANT_002",
        "DUT_VARIANT_003",
    ]
    assert [item["variant_id"] for item in editable["site_variants"]] == (
        ["VARIANT_001"] * 7
        + ["VARIANT_002"] * 7
        + ["VARIANT_003"] * 7
    )
    assert editable["direct_instance_count"] == 46
    assert editable["roundtrip_verified"] is True
    assert editable["pcell_dependency_count"] == 0
    assert editable["variant_roundtrip"] == [
        {"cell_name": "DUT_VARIANT_001", "geometry_xor_clean": True},
        {"cell_name": "DUT_VARIANT_002", "geometry_xor_clean": True},
        {"cell_name": "DUT_VARIANT_003", "geometry_xor_clean": True},
    ]
    assert hashlib.sha256(padset.read_bytes()).hexdigest() == input_before
