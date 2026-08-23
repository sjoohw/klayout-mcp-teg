"""MCP transport envelope, error semantics, and tool annotations."""

from __future__ import annotations

from functools import wraps
import json
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ConfigDict, RootModel


class McpToolResult(RootModel[dict[str, Any]]):
    """Structured result envelope shared by all public MCP tools."""

    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {"ok": {"const": True}},
                    "required": ["ok"],
                },
                {
                    "properties": {
                        "ok": {"const": False},
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                        "next_action": {"type": "string"},
                    },
                    "required": ["ok", "code", "message", "details"],
                },
            ]
        }
    )


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ADDITIVE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def protocol_tool(
    server: FastMCP,
    *,
    annotations: ToolAnnotations,
) -> Callable[[Callable[..., McpToolResult]], Callable[..., McpToolResult]]:
    """Register a tool while preserving direct-Python structured dict results."""

    def decorator(
        function: Callable[..., McpToolResult],
    ) -> Callable[..., McpToolResult]:
        @wraps(function)
        def transport_wrapper(*args: Any, **kwargs: Any) -> McpToolResult | CallToolResult:
            result = function(*args, **kwargs)
            if isinstance(result, dict) and result.get("ok") is False:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(result, ensure_ascii=False, indent=2),
                        )
                    ],
                    structuredContent=result,
                    isError=True,
                )
            return result

        server.tool(annotations=annotations)(transport_wrapper)
        return function

    return decorator
