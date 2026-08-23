from klayout_mcp.server import (
    analyze_pad_boxes,
    assemble_teg,
    describe_dut_pcell,
    export_pcell_code,
    generate_dut_geometry,
    plan_teg_dut_sequence,
    server_status,
    verify_design_rules,
)


def test_server_status() -> None:
    result = server_status()

    assert result["ok"] is True
    assert result["server"] == "klayout-teg-mcp"
    assert result["capabilities"] == [
        "analyze_pad_boxes",
        "analyze_padset",
        "render_boundary_overlay",
        "select_routed_units",
        "plan_transistor_array",
        "describe_dut_pcell",
        "generate_dut_geometry",
        "inspect_sample_dut",
        "plan_teg_dut_sequence",
        "verify_design_rules",
        "assemble_teg",
        "export_pcell_code",
    ]
    assert result["klayout_adapter"] == "subprocess"
    assert result["runtime"]["python"]
    assert result["runtime"]["mcp_sdk"]
    assert result["klayout_support"] == {
        "minimum_version": "0.30.0",
        "validated_version": "0.30.10",
        "version_reported_by_layout_tools": True,
    }




def test_tool_returns_structured_error() -> None:
    result = analyze_pad_boxes(boxes_um=[])

    assert result["ok"] is False
    assert result["code"] == "PAD_ROW_NOT_FOUND"
    assert result["next_action"]


def test_assemble_teg_requires_explicit_conceptual_opt_in() -> None:
    result = assemble_teg(
        padset_path="missing.gds",
        layermap_path="missing.yaml",
        output_gds_path="output.gds",
        dut_sweep=[{}],
    )

    assert result["ok"] is False
    assert result["code"] == "CONCEPTUAL_EXPORT_REQUIRES_OPT_IN"
    assert result["details"]["production_ready"] is False
    assert result["next_action"]


def test_assemble_teg_rejects_nonpositive_timeout_with_action() -> None:
    result = assemble_teg(
        padset_path="missing.gds",
        layermap_path="missing.yaml",
        output_gds_path="output.gds",
        dut_sweep=[{}],
        confirm_conceptual_export=True,
        timeout_seconds=0.0,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_TIMEOUT"
    assert result["next_action"]


def test_assemble_teg_rejects_generated_layer_collisions(tmp_path) -> None:
    layermap = tmp_path / "layers.yaml"
    layermap.write_text(
        """layers:
  m1: [10, 2]
  active: [1, 0]
  poly: [1, 0]
  contact: [3, 0]
  text: [100, 0]
""",
        encoding="utf-8",
    )

    result = assemble_teg(
        padset_path=str(tmp_path / "missing.gds"),
        layermap_path=str(layermap),
        output_gds_path=str(tmp_path / "output.gds"),
        dut_sweep=[{}],
        confirm_conceptual_export=True,
    )

    assert result["ok"] is False
    assert result["code"] == "ASSEMBLY_LAYERMAP_COLLISION"
    assert result["details"]["collisions"] == [
        {"layer": 1, "datatype": 0, "roles": ["active", "poly"]}
    ]


def test_generate_dut_geometry_tool() -> None:
    result = generate_dut_geometry(
        w_um=1.5,
        l_um=0.15,
        array_rows=2,
        array_cols=4,
        routed_device_count=4,
    )

    assert result["ok"] is True
    assert result["total_units"] == 8
    assert result["routed_count"] == 4
    assert "terminals" in result
    assert result["production_ready"] is False


def test_describe_dut_pcell_tool() -> None:
    result = describe_dut_pcell()

    assert result["ok"] is True
    assert result["contract_version"] == 1
    assert result["production_ready"] is False
    assert result["terminals"]["gate"]["name"] == "G"


def test_plan_teg_dut_sequence_tool_returns_structured_site_error() -> None:
    result = plan_teg_dut_sequence(dut_slots=[], site_parameter_sets=[])

    assert result["ok"] is False
    assert result["code"] == "DUT_SITE_COUNT_MISMATCH"
    assert result["next_action"]


def test_plan_teg_dut_sequence_tool_returns_structured_origin_error() -> None:
    slots = [
        {
            "site": site,
            "origin_um": None if site == 1 else [80.0 * site, 30.0],
            "source_pad": site,
            "drain_pad": site + 1,
            "gate_pad": 23 if site % 2 else 24,
            "body_pad": 25,
        }
        for site in range(1, 22)
    ]

    result = plan_teg_dut_sequence(
        dut_slots=slots,
        site_parameter_sets=[
            {"site": site, "parameters": {}} for site in range(1, 22)
        ],
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_DUT_SLOT_ORIGIN"
    assert result["details"]["site"] == 1
    assert result["next_action"]


def test_verify_design_rules_tool() -> None:
    geom = generate_dut_geometry(
        w_um=1.5,
        l_um=0.15,
        array_rows=2,
        array_cols=4,
        routed_device_count=4,
    )
    result = verify_design_rules(dut_geometry=geom)

    assert result["ok"] is True
    assert result["drc_clean"] is True


def _write_pcell_layermap(tmp_path):
    path = tmp_path / "pcell-layers.yaml"
    path.write_text(
        """layers:
  m1: [10, 0]
  active: [20, 0]
  poly: [30, 0]
  contact: [40, 0]
""",
        encoding="utf-8",
    )
    return path


def test_export_pcell_code_requires_conceptual_opt_in(tmp_path) -> None:
    layermap = _write_pcell_layermap(tmp_path)

    result = export_pcell_code(layermap_path=str(layermap))

    assert result["ok"] is False
    assert result["code"] == "CONCEPTUAL_PCELL_EXPORT_REQUIRES_OPT_IN"
    assert result["details"]["production_ready"] is False


def test_export_pcell_code_tool_with_explicit_layermap(tmp_path) -> None:
    layermap = _write_pcell_layermap(tmp_path)
    result = export_pcell_code(
        layermap_path=str(layermap),
        confirm_conceptual_export=True,
    )

    assert result["ok"] is True
    assert result["production_ready"] is False
    assert result["electrical_connectivity_verified"] is False
    assert "class DutTransistorArrayPCell" in result["source_code"]
    assert result["code_length"] > 100


def test_export_pcell_code_writes_new_file(tmp_path) -> None:
    output_path = tmp_path / "nested" / "teg_pcell.py"
    layermap = _write_pcell_layermap(tmp_path)

    result = export_pcell_code(
        layermap_path=str(layermap),
        output_script_path=str(output_path),
        confirm_conceptual_export=True,
    )

    assert result["ok"] is True
    assert result["output_script_path"] == str(output_path.resolve())
    assert output_path.read_text(encoding="utf-8") == result["source_code"]


def test_export_pcell_code_preserves_existing_file(tmp_path) -> None:
    output_path = tmp_path / "teg_pcell.py"
    output_path.write_text("keep me", encoding="utf-8")
    layermap = _write_pcell_layermap(tmp_path)

    result = export_pcell_code(
        layermap_path=str(layermap),
        output_script_path=str(output_path),
        confirm_conceptual_export=True,
    )

    assert result["ok"] is False
    assert result["code"] == "OUTPUT_EXISTS"
    assert result["details"]["output_script_path"] == str(output_path.resolve())
    assert result["next_action"]
    assert output_path.read_text(encoding="utf-8") == "keep me"
