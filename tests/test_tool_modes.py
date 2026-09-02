from __future__ import annotations

import asyncio
import json
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


async def _default_tool_names() -> tuple[set[str], dict]:
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = {**os.environ, "PYTHONPATH": str(source_root)}
    environment.pop("KLAYOUT_MCP_TOOL_MODE", None)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "klayout_mcp.server"],
        env=environment,
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            status = await session.call_tool("server_status", {})
            return {tool.name for tool in listed.tools}, status.structuredContent


async def _surface_budget(mode: str) -> dict[str, int]:
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
            initialized = await session.initialize()
            listed = await session.list_tools()
    records = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in listed.tools
    ]
    serialized = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return {
        "tool_count": len(records),
        "tools_list_characters": len(serialized),
        "instruction_characters": len(initialized.instructions or ""),
    }


def test_stock_default_is_small_drawing_surface_not_expert() -> None:
    names, status = asyncio.run(_default_tool_names())

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
    assert status["tool_surface"]["expert_is_default"] is False


def test_task_surfaces_stay_within_small_model_character_budget() -> None:
    for mode in ("drawing", "facade", "onboarding"):
        budget = asyncio.run(_surface_budget(mode))
        assert budget["tool_count"] <= 10
        assert (
            budget["tools_list_characters"] + budget["instruction_characters"]
            <= 30_000
        )


def test_facade_tool_mode_exposes_only_persistent_entrypoints() -> None:
    names, status = asyncio.run(_listed_tool_names("facade"))

    assert names == {
        "server_status",
        "host_doctor",
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
        "host_doctor",
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


def test_onboarding_mode_exposes_only_pad_corpus_and_score_workflow() -> None:
    names, status = asyncio.run(_listed_tool_names("onboarding"))

    assert names == {
        "server_status",
        "host_doctor",
        "register_pad_macro",
        "compose_registered_pad_macro",
        "onboard_transistor_corpus",
        "resolve_transistor_corpus",
        "score_transistor_adapter",
        "build_transistor_adapter_candidate",
        "register_transistor_adapter_candidate",
    }
    assert status["tool_surface"]["active_mode"] == "onboarding"
    assert set(status["tool_surface"]["active_tools"]) == names
    assert set(status["capabilities"]).issubset(names)


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
    assert "must be one of: drawing, expert, facade, onboarding" in completed.stderr
