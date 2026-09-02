from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _listed_tool_names(mode: str) -> tuple[set[str], dict]:
    source_root = Path(__file__).resolve().parents[1] / "src"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "klayout_mcp.server"],
        env={
            **os.environ,
            "PYTHONPATH": str(source_root),
            "KLAYOUT_MCP_TOOL_MODE": mode,
        },
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            status = await session.call_tool("server_status", {})
            return {tool.name for tool in listed.tools}, status.structuredContent


def test_facade_tool_mode_exposes_only_persistent_entrypoints() -> None:
    names, status = asyncio.run(_listed_tool_names("facade"))

    assert names == {
        "server_status",
        "teg_intake",
        "teg_status",
        "teg_plan",
        "teg_generate",
        "teg_verify",
    }
    assert status["tool_surface"]["active_mode"] == "facade"
    assert set(status["tool_surface"]["active_tools"]) == names
    assert set(status["capabilities"]).issubset(names)
    assert status["persistent_facade"]["tools"] == [
        "teg_intake",
        "teg_status",
        "teg_plan",
        "teg_generate",
        "teg_verify",
    ]
    assert all(
        tool in names
        for tools in status["recommended_entrypoints"].values()
        for tool in tools
    )


def test_drawing_tool_mode_exposes_small_deterministic_surface() -> None:
    names, status = asyncio.run(_listed_tool_names("drawing"))

    assert names == {
        "server_status",
        "draw_manhattan_layout",
        "inspect_layout",
        "extract_layout_style",
        "compare_layouts",
        "plan_staged_mesh_segment",
        "plan_maximum_contact_array",
    }
    assert status["tool_surface"]["active_mode"] == "drawing"
    assert set(status["tool_surface"]["active_tools"]) == names
    assert set(status["capabilities"]).issubset(names)
    assert status["persistent_facade"]["tools"] == []
    assert all(
        tool in names
        for tools in status["recommended_entrypoints"].values()
        for tool in tools
    )


def test_unknown_tool_mode_fails_fast_without_expert_fallback() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio; from klayout_mcp.server import configure_tool_mode; "
                "asyncio.run(configure_tool_mode('typo'))"
            ),
        ],
        env={**os.environ, "PYTHONPATH": str(source_root)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "must be one of: drawing, expert, facade" in completed.stderr
