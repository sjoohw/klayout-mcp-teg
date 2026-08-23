from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_protocol_error_schema_and_annotations() -> None:
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
                    },
                )
                return listed, success, contract, failure, origin_failure

    listed, success, contract, failure, origin_failure = asyncio.run(exercise_server())
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
