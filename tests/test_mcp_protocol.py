from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_protocol_error_schema_and_annotations(tmp_path) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "klayout_mcp.server"],
        env={**os.environ, "PYTHONPATH": str(source_root)},
    )

    async def exercise_server():
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                success = await session.call_tool("server_status", {})
                contract = await session.call_tool("describe_dut_pcell", {})
                failure = await session.call_tool(
                    "analyze_pad_boxes", {"boxes_um": []}
                )
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
                origin_failure = await session.call_tool(
                    "plan_teg_dut_sequence",
                    {
                        "dut_slots": slots,
                        "site_parameter_sets": [
                            {"site": site, "parameters": {}}
                            for site in range(1, 22)
                        ],
                        "dimension_semantics": "device_specific_w_l",
                    },
                )
                references = await session.call_tool(
                    "list_reference_layouts",
                    {"library_root": str(tmp_path / "reference-library")},
                )
                missing_confirmation = await session.call_tool(
                    "confirm_reference_view",
                    {
                        "view_id": "view-does-not-exist",
                        "library_root": str(tmp_path / "reference-library"),
                    },
                )
                return (
                    listed,
                    success,
                    contract,
                    failure,
                    origin_failure,
                    references,
                    missing_confirmation,
                )

    (
        listed,
        success,
        contract,
        failure,
        origin_failure,
        references,
        missing_confirmation,
    ) = asyncio.run(exercise_server())
    tools = {tool.name: tool for tool in listed.tools}

    analyze = tools["analyze_pad_boxes"]
    assert len(analyze.outputSchema["oneOf"]) == 2
    assert analyze.outputSchema["oneOf"][1]["required"] == [
        "ok",
        "code",
        "message",
        "details",
    ]
    assert analyze.annotations.readOnlyHint is True
    assert analyze.annotations.destructiveHint is False
    assert tools["assemble_teg"].annotations.readOnlyHint is False
    assert tools["assemble_teg"].annotations.destructiveHint is False
    assert tools["plan_kelvin_m1_routing"].annotations.readOnlyHint is True
    assert tools["guide_phase1_direct_workflow"].annotations.readOnlyHint is True
    assert "intake_plan" in tools["guide_phase1_direct_workflow"].inputSchema["properties"]
    assert tools["generate_kelvin_m1_teg"].annotations.readOnlyHint is False
    assert tools["generate_kelvin_m1_teg"].annotations.destructiveHint is False
    assert tools["teg_intake"].annotations.readOnlyHint is False
    assert tools["teg_status"].annotations.readOnlyHint is True
    assert tools["teg_plan"].annotations.readOnlyHint is False
    assert tools["teg_generate"].annotations.readOnlyHint is False
    assert tools["teg_verify"].annotations.readOnlyHint is False
    assert "design_intent_draft" in tools["teg_intake"].inputSchema["properties"]
    assert "job_id" in tools["teg_status"].inputSchema["properties"]
    assert "approval_reference" in tools["teg_plan"].inputSchema["properties"]
    assert "output_name" in tools["teg_generate"].inputSchema["properties"]
    assert "measurement_manifest" in tools["teg_verify"].inputSchema["properties"]
    assert "external_reports" in tools["teg_verify"].inputSchema["properties"]
    intake_schema = tools["teg_intake"].inputSchema
    draft_schema = intake_schema["$defs"]["DesignIntentDraftInput"]
    assert draft_schema["required"] == [
        "schema_version",
        "intent_id",
        "units",
        "process",
        "frame",
        "pads",
        "devices",
        "terminal_contracts",
        "terminal_net_pad_map",
        "measurement_requirements",
        "routing_policy",
        "verification_policy",
        "output_policy",
        "unresolved_questions",
    ]
    assert draft_schema["properties"]["schema_version"]["const"] == 1
    assert draft_schema["properties"]["units"]["const"] == "um"
    assert intake_schema["$defs"]["DeviceInput"]["properties"]["family"][
        "enum"
    ] == ["transistor", "resistor", "capacitor"]
    assert intake_schema["$defs"]["RoutingPolicyInput"]["properties"][
        "manhattan_only"
    ]["const"] is True
    approval_schema = tools["teg_plan"].inputSchema
    assert approval_schema["properties"]["approval_reference"]["$ref"] == (
        "#/$defs/ApprovalReferenceInput"
    )
    assert "draft_sha256" in approval_schema["$defs"][
        "ApprovalReferenceInput"
    ]["required"]
    verify_schema = tools["teg_verify"].inputSchema
    measurement_schema = verify_schema["$defs"]["MeasurementManifestInput"]
    assert "dut_pin_map" in measurement_schema["required"]
    assert "electrical_topology" in measurement_schema["required"]
    assert verify_schema["$defs"]["StimulusInput"]["properties"]["target"][
        "$ref"
    ] == "#/$defs/TerminalReferenceInput"
    assert verify_schema["$defs"]["InactiveTerminalStateInput"]["properties"][
        "state"
    ]["enum"] == ["force", "float", "ground", "guard", "follow_shared_pad"]
    assert tools["compare_kelvin_layouts"].annotations.readOnlyHint is True
    assert tools["register_reference_layout"].annotations.readOnlyHint is False
    assert tools["list_reference_layouts"].annotations.readOnlyHint is True
    assert tools["prepare_reference_view"].annotations.readOnlyHint is False
    assert tools["confirm_reference_view"].annotations.readOnlyHint is False
    assert tools["consult_reference_selection"].annotations.readOnlyHint is True
    assert tools["classify_reference_drc_markers"].annotations.readOnlyHint is True
    assert "view_bbox_um" in tools["prepare_reference_view"].inputSchema["properties"]
    assert "candidate_markers" in tools["classify_reference_drc_markers"].inputSchema["properties"]
    assert "work_directory_path" in tools["generate_kelvin_m1_teg"].inputSchema["properties"]
    assert "dimension_semantics" in tools["generate_dut_geometry"].inputSchema["properties"]
    assert (
        tools["generate_dut_geometry"].inputSchema["properties"]
        ["dimension_semantics"]["default"]
        is None
    )

    assert success.isError is False
    assert success.structuredContent["ok"] is True
    assert contract.isError is False
    assert contract.structuredContent["pcell_name"] == "DutTransistorArray"
    assert failure.isError is True
    assert failure.structuredContent["ok"] is False
    assert failure.structuredContent["code"] == "PAD_ROW_NOT_FOUND"
    assert failure.structuredContent["next_action"]
    assert origin_failure.isError is True
    assert origin_failure.structuredContent["ok"] is False
    assert origin_failure.structuredContent["code"] == "INVALID_DUT_SLOT_ORIGIN"
    assert origin_failure.structuredContent["next_action"]
    assert references.isError is False
    assert references.structuredContent["reference_count"] == 0
    assert missing_confirmation.isError is True
    assert missing_confirmation.structuredContent["code"] == "REFERENCE_DOCUMENT_NOT_FOUND"
