from klayout_mcp.server import (
    analyze_pad_boxes,
    assemble_teg,
    compare_layouts,
    describe_process_capability,
    describe_pdk_profile_inputs,
    describe_organization_preset,
    describe_dut_pcell,
    draw_manhattan_layout,
    extract_layout_style,
    export_pcell_code,
    generate_dut_geometry,
    generate_phase1_direct_teg,
    host_doctor,
    inspect_layout,
    inventory_pcellizer_hierarchy,
    create_pcellizer_snapshot,
    inspect_pcellizer_snapshot,
    recover_pcellizer_snapshot,
    plan_pcellizer_process_inputs,
    define_pcellizer_parameter,
    compile_pcellizer_recipe,
    plan_pcellizer_split_table,
    generate_pcellizer_split_batch,
    inspect_pcellizer_batch,
    plan_direct_measurement_teg,
    plan_single_transistor_context,
    plan_phase1_device_doe,
    plan_metal_resistor_primitive,
    plan_mom_capacitor_primitive,
    plan_maximum_contact_array,
    plan_phase1_direct_teg_layout,
    plan_staged_mesh_segment,
    plan_teg_dut_sequence,
    list_reference_layouts,
    server_status,
    teg_status,
    verify_design_rules,
    validate_process_capability_profile,
)


DEVICE_SPECIFIC_W_L = "device_specific_w_l"
TRANSVERSE_LONGITUDINAL_AXES = (
    "width_is_transverse_axis_length_is_longitudinal_axis"
)


def test_teg_status_missing_job_is_read_only(tmp_path, monkeypatch) -> None:
    workflow_root = tmp_path / "missing-workflow-root"
    output_root = tmp_path / "missing-workflow-output"
    monkeypatch.setenv("KLAYOUT_MCP_WORKFLOW_ROOT", str(workflow_root))
    monkeypatch.setenv("KLAYOUT_MCP_WORKFLOW_OUTPUT_ROOT", str(output_root))

    result = teg_status("missing-job")

    assert result["ok"] is False
    assert result["code"] == "WORKFLOW_JOB_NOT_FOUND"
    assert not workflow_root.exists()
    assert not output_root.exists()


def test_server_status() -> None:
    result = server_status()

    assert result["ok"] is True
    assert result["server"] == "klayout-teg-mcp"
    assert result["tool_surface"]["expert_is_readiness_claim"] is False
    assert result["tool_surface"]["mode_reduces_tools_not_common_instruction"] is True
    assert "registration is not target-process readiness" in result["capabilities_semantics"]
    assert result["known_limitations"] == {
        "stock_classification": "generic_nonproduction_drawing_and_contract_framework",
        "transistor_primitive_adapter": "not_implemented",
        "conceptual_transistor_is_phase1_fallback": False,
        "phase1_padset_import_or_preservation": "not_implemented",
        "phase1_pad_geometry": "synthesized_from_frame_and_pad_count",
        "phase1_dut_to_pad_route_geometry": "bounded_polyline_compiled_to_multi_rail_mesh",
        "phase1_standalone_mesh_compiler_integrated": True,
        "phase1_global_search_budget": "global_node_and_wall_time_budget_enforced",
        "same_target_concurrent_writers_supported": True,
        "generic_manhattan_same_target_no_clobber": True,
        "exact_gemma4_qualified": False,
    }
    assert result["capabilities"] == [
        "analyze_pad_boxes",
        "analyze_padset",
        "render_boundary_overlay",
        "select_routed_units",
        "plan_transistor_array",
        "plan_single_transistor_context",
        "describe_dut_pcell",
        "generate_dut_geometry",
        "inspect_sample_dut",
        "plan_teg_dut_sequence",
        "verify_design_rules",
        "assemble_teg",
        "export_pcell_code",
        "plan_kelvin_m1_routing",
        "generate_kelvin_m1_teg",
        "compare_kelvin_layouts",
        "draw_manhattan_layout",
        "inspect_layout",
        "extract_layout_style",
        "inventory_pcellizer_hierarchy",
        "create_pcellizer_snapshot",
        "inspect_pcellizer_snapshot",
        "recover_pcellizer_snapshot",
        "plan_pcellizer_process_inputs",
        "define_pcellizer_parameter",
        "compile_pcellizer_recipe",
        "plan_pcellizer_split_table",
        "generate_pcellizer_split_batch",
        "inspect_pcellizer_batch",
        "compare_layouts",
        "plan_staged_mesh_segment",
        "plan_maximum_contact_array",
        "plan_direct_measurement_teg",
        "plan_phase1_device_doe",
        "describe_process_capability",
        "describe_pdk_profile_inputs",
        "describe_organization_preset",
        "validate_process_capability_profile",
        "plan_metal_resistor_primitive",
        "plan_mom_capacitor_primitive",
        "plan_phase1_terminal_routes",
        "guide_phase1_direct_workflow",
        "plan_phase1_direct_teg_layout",
        "generate_phase1_direct_teg",
        "host_doctor",
        "register_pad_macro",
        "compose_registered_pad_macro",
        "onboard_transistor_corpus",
        "resolve_transistor_corpus",
        "score_transistor_adapter",
        "build_transistor_adapter_candidate",
        "register_transistor_adapter_candidate",
        "teg_intake",
        "teg_status",
        "teg_plan",
        "teg_generate",
        "teg_verify",
        "register_reference_layout",
        "list_reference_layouts",
        "prepare_reference_view",
        "confirm_reference_view",
        "consult_reference_selection",
        "classify_reference_drc_markers",
    ]
    assert result["klayout_adapter"] == "subprocess"
    assert result["runtime"]["python"]
    assert result["runtime"]["mcp_sdk"]
    assert result["klayout_support"] == {
        "minimum_version": "0.30.0",
        "validated_version": "0.30.10",
        "version_reported_by_layout_tools": True,
    }
    assert result["layout_contract"]["routing"]["style"] == "orthogonal_only"
    assert result["layout_contract"]["routing"]["diagonal_segments_allowed"] is False
    assert result["evidence_ladder_contract"]["ordered_states"][0] == (
        "intent_draft_complete"
    )
    assert result["evidence_ladder_contract"]["production_ready_requires"] == (
        "outside_current_evidence_ladder"
    )
    assert result["evidence_ladder_contract"][
        "signoff_evidence_approved_means_production_ready"
    ] is False
    assert result["evidence_ladder_contract"]["mock_evidence_can_reach_signoff"] is False
    assert result["workflow_document_contract"]["schema_version"] == 1
    assert result["workflow_document_contract"]["schema_frozen"] is True
    assert result["workflow_document_contract"]["draft_authorizes_generation"] is False
    assert result["reference_library_contract"]["reference_marker_count_is_acceptance_limit"] is False
    assert result["reference_library_contract"]["matching_repetitions_allowed"] is True
    assert result["reference_library_contract"]["unmatched_markers_block_drawing"] is False
    assert (
        result["workflow_document_contract"]
        ["measurement_layout_reference_is_file_hash_verification"]
        is False
    )
    assert result["approval_verifier_contract"]["backend_configured"] is False
    assert result["persistent_facade"]["default_approval_backend_configured"] is False
    assert result["persistent_facade"]["target_production_transistor_engine_configured"] is False
    assert result["persistent_facade"]["external_report_normalization_contract_available"] is True
    assert result["persistent_facade"]["drc_lvs_pex_execution_runner_configured"] is False
    assert "fail closed" in result["persistent_facade"]["stock_execution_limit"]
    assert result["recommended_entrypoints"]["generic_nonproduction_drawing"] == [
        "draw_manhattan_layout"
    ]
    assert result["recommended_entrypoints"]["stock_persistent_intake_or_status"] == [
        "teg_intake",
        "teg_status",
    ]
    assert result["approval_verifier_contract"]["default_behavior"] == "fail_closed"
    assert result["approval_verifier_contract"]["mcp_can_mint_approval"] is False
    assert result["workflow_store_contract"]["full_manifest_ancestry_revalidated"] is True
    assert result["workflow_store_contract"]["incomplete_drafts_persisted"] is True
    assert result["workflow_store_contract"]["validate_only_does_not_persist"] is True
    assert result["external_verification_runner_contract"]["stock_runner_configured"] is False
    assert result["persistent_facade"]["default_approval_backend_configured"] is False
    assert result["external_evidence_contract"]["mock_evidence_can_reach_signoff"] is False
    assert (
        result["layout_contract"]["metal_spacing"]
        ["equal_width_space_to_width_ratio_minimum"]
        == 1.0
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]
        ["all_other_added_m1_routing"]
        == "terminal_access_then_orthogonal_mesh_required"
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]["terminal_square"]["size_um"]
        == 0.300
    )
    assert result["layout_contract"]["kelvin_m1_routing"]["mesh_definition"][
        "expansion_rail_counts"
    ] == [1, 2, 4, 6]
    assert (
        result["layout_contract"]["kelvin_m1_routing"]["mesh_definition"]
        ["expansion_style"]
        == "one_sided_from_persistent_baseline_rail"
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]
        ["mesh_structure_interface_rule"]
        ["end_tie_must_align_to_receiving_rail_centerline"]
        is True
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]["orthogonal_bend_rule"]
        ["outer_faces_must_align"]
        is True
    )
    assert result["layout_contract"]["kelvin_m1_routing"]["pad_roles_left_to_right"] == [
        "SENSE+",
        "FORCE+",
        "FORCE-",
        "SENSE-",
    ]
    assert (
        result["layout_contract"]["kelvin_m1_routing"]
        ["voltage_sense_terminal_route"]["horizontal_jog_allowed"]
        is False
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]
        ["voltage_sense_vertical_horizontal_joint"]
        ["edge_only_or_single_rail_overlap_allowed"]
        is False
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]
        ["voltage_sense_vertical_horizontal_joint"]
        ["topology"]
        == "pitch_aligned_natural_90_degree_mesh_corner"
    )
    assert (
        result["layout_contract"]["kelvin_m1_routing"]
        ["unnecessary_down_side_up_detour_allowed"]
        is False
    )
    assert result["layout_contract"]["parasitic_resistance"]["optimized"] is False
    assert (
        result["layout_contract"]["direct_measurement_mesh_routing"]
        ["long_single_rail_allowed"]
        is False
    )


def test_list_reference_layouts_tool_uses_explicit_library_root(tmp_path) -> None:
    library_root = tmp_path / "reference-library"
    result = list_reference_layouts(library_root=str(library_root))

    assert result["ok"] is True
    assert result["reference_count"] == 0
    assert library_root.exists() is False


def test_generic_mesh_and_contact_tools_expose_deterministic_evidence() -> None:
    mesh = plan_staged_mesh_segment(
        dbu_um=0.0025,
        start_um=[0.0, 0.0],
        end_um=[20.0, 0.0],
        corridor_um=[0.0, -2.0, 20.0, 2.0],
        rail_width_um=0.3,
        rail_space_um=0.3,
        landing_span_um=0.3,
        cross_tie_pitch_um=1.2,
    )
    contacts = plan_maximum_contact_array(
        dbu_um=0.0025,
        array_center_um=[0.0, 0.0],
        array_axis="y",
        available_width_um=1.0,
        contact_size_um=0.065,
        contact_space_um=0.075,
        active_enclosure_um=0.005,
        metal_enclosure_um=0.035,
        metal_space_um=0.065,
        alignment="away_from_positive",
        neighbor_metal_near_edge_um=0.525,
        neighbor_side="positive",
        neighbor_clearance_um=0.065,
    )

    assert mesh["evidence"]["rail_count"] == 7
    assert mesh["evidence"]["single_rail_fallback_allowed"] is False
    assert contacts["evidence"]["legal_contact_count"] == 5


def test_direct_measurement_planner_exposes_default_contract() -> None:
    result = plan_direct_measurement_teg()

    assert result["ok"] is True
    assert result["request"]["frame_um"] == [2000.0, 54.0]
    assert result["request"]["pads"]["count"] == 25
    assert result["request"]["measurement_mode"] == "direct"
    assert result["routing_policy"]["preferred_layer"] == "first_metal"
    assert result["planning_status"] == "questions_required"
    assert result["stop_before_drawing"] is True
    assert result["invented_defaults_are_forbidden"] is True


def test_phase1_device_doe_tool_requires_process_confirmation() -> None:
    result = plan_phase1_device_doe(
        process_profile="demo",
        process_profile_version="1",
        family="resistor",
        device_type="metal1",
        measurement="kelvin_4t",
        required_terminals=["F+", "F-", "S+", "S-"],
        supported_axes=["width_um", "length_um"],
        baseline={"width_um": 0.3, "length_um": 1.0},
        sweeps={"width_um": [0.1, 0.3]},
    )

    assert result["ok"] is False
    assert result["code"] == "PROCESS_PROFILE_CONFIRMATION_REQUIRED"


def test_process_capability_tool_requires_target_onboarding() -> None:
    result = describe_process_capability("target_process")

    assert result["ok"] is False
    assert result["code"] == "NO_BUNDLED_PROCESS_CAPABILITY"
    assert result["details"]["bundled_process_profiles"] == []


def test_pdk_profile_input_contract_separates_process_and_teg_choices() -> None:
    result = describe_pdk_profile_inputs()

    assert result["policy"]["bundled_process_profile_is_fallback"] is False
    assert "layers" in result["required_for_core_profile"]
    assert "contact_or_via_used" in result["conditional_inputs"]
    assert "frame size and Pad topology" in result["not_pdk_inputs"]
    assert "company terminal names and measurement modes" in result["not_pdk_inputs"]
    assert "project max routing width" in result["not_pdk_inputs"]


def test_reference_organization_preset_is_pdk_independent() -> None:
    result = describe_organization_preset()

    assert result["pdk_independent"] is True
    assert result["approval_status"] == "reference_only"
    assert result["terminal_order_by_family_and_measurement"]["transistor"]["dc_4t"] == [
        "G",
        "D",
        "S",
        "B",
    ]
    assert result["transistor_context_defaults"] == {
        "fill_dut_window": True,
        "measured_device_selection": "balanced_central_region",
        "default_measured_device_count": 1,
        "measurement_edge_inset_um": 5.0,
        "surrounding_device_routing": "none",
        "diffusion_sharing": "compatible_neighbors",
        "default_fill_style": "same_as_measured",
        "allowed_fill_styles": ["same_as_measured", "standard_cell_like"],
        "standard_cell_sequence": ["nmos", "pmos", "pmos", "nmos"],
        "sequence_axis": "x",
        "standard_cell_height_required": True,
    }


def test_external_process_capability_requires_schema_version() -> None:
    result = validate_process_capability_profile({})

    assert result["ok"] is False
    assert result["code"] == "UNSUPPORTED_PROCESS_CAPABILITY_SCHEMA"


def test_passive_primitive_tool_requires_a_capable_process_profile() -> None:
    result = plan_metal_resistor_primitive(
        process_capability={},
        device_name="metal_resistor",
        layer_role="m1",
        measurement="direct_2t",
        width_um=0.1,
        length_um=1.0,
        terminal_size_um=0.3,
        dimension_semantics=TRANSVERSE_LONGITUDINAL_AXES,
    )

    assert result["ok"] is False
    assert result["code"] == "UNSUPPORTED_PROCESS_CAPABILITY_SCHEMA"


def test_mom_primitive_tool_requires_a_capable_process_profile() -> None:
    result = plan_mom_capacitor_primitive(
        process_capability={},
        device_name="mom",
        layer_role="m1",
        finger_width_um=0.1,
        finger_space_um=0.1,
        finger_length_um=2.0,
        finger_count=6,
        bus_width_um=0.3,
    )

    assert result["ok"] is False
    assert result["code"] == "UNSUPPORTED_PROCESS_CAPABILITY_SCHEMA"


def test_phase1_layout_composer_requires_a_capable_process_profile(tmp_path) -> None:
    result = plan_phase1_direct_teg_layout(
        output_layout_path=str(tmp_path / "teg.gds"),
        top_cell="TEG",
        process_capability={},
        request_plan={},
        primitive_instances=[],
        pad_rail_width_um=0.3,
    )

    assert result["ok"] is False
    assert result["code"] == "UNSUPPORTED_PROCESS_CAPABILITY_SCHEMA"


def test_phase1_generator_requires_a_capable_process_profile(tmp_path) -> None:
    result = generate_phase1_direct_teg(
        output_layout_path=str(tmp_path / "teg.gds"),
        top_cell="TEG",
        process_capability={},
        request_plan={},
        primitive_instances=[],
        pad_rail_width_um=0.3,
    )

    assert result["ok"] is False
    assert result["code"] == "UNSUPPORTED_PROCESS_CAPABILITY_SCHEMA"




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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
    )

    assert result["ok"] is True
    assert result["total_units"] == 8
    assert result["routed_count"] == 4
    assert "terminals" in result
    assert result["production_ready"] is False
    assert result["routing_policy"]["orthogonal_routing_verified"] is True
    assert result["layout_contract"]["dimension_semantics"]["confirmed"] is True


def test_generate_geometry_requires_dimension_semantics_confirmation() -> None:
    result = generate_dut_geometry(w_um=0.1, l_um=1.0)

    assert result["ok"] is False
    assert result["code"] == "DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED"
    assert result["details"]["automatic_axis_inference"] is False
    assert result["next_action"]


def test_transverse_width_may_exceed_longitudinal_length() -> None:
    result = generate_dut_geometry(
        w_um=0.3,
        l_um=0.1,
        dimension_semantics=TRANSVERSE_LONGITUDINAL_AXES,
    )

    assert result["ok"] is True
    contract = result["layout_contract"]["dimension_semantics"]
    assert contract["width_axis"] == "transverse_to_current_flow"
    assert contract["length_axis"] == "longitudinal_to_current_flow"
    assert contract["numeric_order_required"] is False


def test_describe_dut_pcell_tool() -> None:
    result = describe_dut_pcell()

    assert result["ok"] is True
    assert result["contract_version"] == 2
    assert result["production_ready"] is False
    assert result["terminals"]["gate"]["name"] == "G"


def test_plan_teg_dut_sequence_tool_returns_structured_site_error() -> None:
    result = plan_teg_dut_sequence(dut_slots=[], site_parameter_sets=[])

    assert result["ok"] is False
    assert result["code"] == "DIMENSION_SEMANTICS_CONFIRMATION_REQUIRED"
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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
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

    result = export_pcell_code(
        layermap_path=str(layermap),
        dimension_semantics=DEVICE_SPECIFIC_W_L,
    )

    assert result["ok"] is False
    assert result["code"] == "CONCEPTUAL_PCELL_EXPORT_REQUIRES_OPT_IN"
    assert result["details"]["production_ready"] is False


def test_export_pcell_code_tool_with_explicit_layermap(tmp_path) -> None:
    layermap = _write_pcell_layermap(tmp_path)
    result = export_pcell_code(
        layermap_path=str(layermap),
        confirm_conceptual_export=True,
        dimension_semantics=DEVICE_SPECIFIC_W_L,
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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
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
        dimension_semantics=DEVICE_SPECIFIC_W_L,
    )

    assert result["ok"] is False
    assert result["code"] == "OUTPUT_EXISTS"
    assert result["details"]["output_script_path"] == str(output_path.resolve())
    assert result["next_action"]
    assert output_path.read_text(encoding="utf-8") == "keep me"
