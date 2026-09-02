from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from klayout_mcp.klayout_adapter import find_klayout_executable


def test_stock_golden_tour_over_real_stdio(tmp_path) -> None:
    """Keep the README stock quickstart aligned with the public MCP protocol."""

    try:
        klayout = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")

    project_root = Path(__file__).resolve().parents[1]
    source_root = project_root / "src"
    bundled = (
        project_root
        / "examples"
        / "gds"
        / "kelvin_m1_w24_48_100nm_l2_3um.gds"
    )
    output = tmp_path / "one-unit.gds"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "klayout_mcp.server"],
        cwd=str(project_root),
        env={
            **os.environ,
            "PYTHONPATH": str(source_root),
            "KLAYOUT_EXE": str(klayout),
        },
    )

    async def exercise_server():
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                status = await session.call_tool("server_status", {})
                source = await session.call_tool(
                    "inspect_layout",
                    {
                        "layout_path": str(bundled),
                        "top_cell": "SLN001_PADSET",
                        "text_limit": 10,
                    },
                )
                drawing = await session.call_tool(
                    "draw_manhattan_layout",
                    {
                        "output_layout_path": str(output),
                        "dbu_um": 0.001,
                        "top_cell": "TOP",
                        "cells": ["TOP", "UNIT"],
                        "layers": [
                            {"name": "m1", "layer": 15, "datatype": 0},
                            {"name": "text", "layer": 100, "datatype": 0},
                        ],
                        "operations": [
                            {
                                "type": "add_box",
                                "cell": "UNIT",
                                "layer": "m1",
                                "bbox_um": [0, 0, 1, 0.3],
                            },
                            {
                                "type": "add_instance",
                                "parent_cell": "TOP",
                                "child_cell": "UNIT",
                                "origin_um": [10, 5],
                                "rotation_deg": 0,
                                "mirror_x": False,
                            },
                            {
                                "type": "add_text",
                                "cell": "TOP",
                                "layer": "text",
                                "text": "GOLDEN_TOUR",
                                "origin_um": [0, 0],
                            },
                        ],
                        "confirm_nonproduction": True,
                    },
                )
                generated = await session.call_tool(
                    "inspect_layout",
                    {"layout_path": str(output), "top_cell": "TOP"},
                )
                return status, source, drawing, generated

    status, source, drawing, generated = asyncio.run(exercise_server())

    assert status.isError is False
    assert status.structuredContent["persistent_facade"][
        "default_approval_backend_configured"
    ] is False
    assert "fail closed" in status.structuredContent["persistent_facade"][
        "stock_execution_limit"
    ]

    assert source.isError is False
    assert source.structuredContent["layout"]["dbu_um"] == 0.00025
    assert source.structuredContent["layout"]["top_cell"] == "SLN001_PADSET"
    assert source.structuredContent["layout"]["top_bbox_um"] == [0.0, 0.0, 2000.0, 54.0]

    assert drawing.isError is False
    assert drawing.structuredContent["fresh_reload_verified"] is True
    assert drawing.structuredContent["top_cell_count"] == 1
    assert drawing.structuredContent["production_ready"] is False

    assert generated.isError is False
    assert generated.structuredContent["layout"]["top_cell"] == "TOP"
    assert generated.structuredContent["layout"]["dbu_um"] == 0.001
    assert generated.structuredContent["layout"]["cell_count"] == 2
    assert generated.structuredContent["shape_totals"]["box"] == 1
    assert generated.structuredContent["shape_totals"]["text"] == 1
