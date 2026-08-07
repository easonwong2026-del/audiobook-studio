"""Standalone Audiobook Studio MCP V1 stdio server.

This module implements the small JSON-RPC surface needed by MCP clients so
the base application does not need an additional runtime dependency.  The
transport can later be replaced or supplemented by Streamable HTTP without
changing the service adapters.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Callable

from .models import API_VERSION, server_info
from .tools.projects import get_project, list_projects
from .tools.scripts import create_project, validate_structured_script

logger = logging.getLogger(__name__)

_TOOLS: dict[str, dict[str, Any]] = {
    "server_info": {
        "name": "server_info",
        "description": "返回 Audiobook Studio MCP 能力与 structured_script 版本。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "validate_structured_script": {
        "name": "validate_structured_script",
        "description": "校验内存中的 structured_script JSON，返回可定位的 errors/warnings。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "script": {"type": "object"},
            },
            "additionalProperties": True,
        },
    },
    "create_project": {
        "name": "create_project",
        "description": "通过同一套离线校验和原子存储流程创建项目。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "script"],
            "properties": {
                "project_name": {"type": "string"},
                "script": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    "list_projects": {
        "name": "list_projects",
        "description": "列出活动项目的结构化摘要。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_project": {
        "name": "get_project",
        "description": "读取单个项目摘要，不返回完整 structured_script。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
}

_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "server_info": lambda _arguments: server_info(),
    "validate_structured_script": validate_structured_script,
    "create_project": create_project,
    "list_projects": list_projects,
    "get_project": get_project,
}


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_call_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        }],
        "structuredContent": payload,
        "isError": is_error,
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one decoded JSON-RPC request; return None for notifications."""
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    is_notification = "id" not in request

    if method == "notifications/initialized":
        return None
    if method == "ping":
        return None if is_notification else _jsonrpc_result(request_id, {})
    if method == "initialize":
        if is_notification:
            return None
        requested_version = params.get("protocolVersion") if isinstance(params, dict) else None
        return _jsonrpc_result(request_id, {
            "protocolVersion": requested_version or "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "Audiobook Studio", "version": API_VERSION},
            "instructions": "Use validate_structured_script before create_project; warnings do not block creation.",
        })
    if method == "tools/list":
        return None if is_notification else _jsonrpc_result(request_id, {"tools": list(_TOOLS.values())})
    if method == "tools/call":
        if not isinstance(params, dict):
            return _jsonrpc_error(request_id, -32602, "params 必须是对象")
        name = params.get("name")
        handler = _HANDLERS.get(name)
        if handler is None:
            return _jsonrpc_error(request_id, -32602, f"未知 MCP tool：{name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _jsonrpc_error(request_id, -32602, "tool arguments 必须是对象")
        try:
            payload = handler(arguments)
            result = _tool_call_result(payload)
        except Exception as exc:  # MCP clients receive a structured tool error.
            logger.exception("MCP tool failed: %s", name)
            result = _tool_call_result({
                "error": {"code": type(exc).__name__, "message": str(exc)},
            }, is_error=True)
        return None if is_notification else _jsonrpc_result(request_id, result)
    if is_notification:
        return None
    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


def run_stdio(input_stream=None, output_stream=None) -> None:
    """Run newline-delimited JSON-RPC over stdin/stdout."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    if input_stream is sys.stdin and hasattr(input_stream, "reconfigure"):
        input_stream.reconfigure(encoding="utf-8")
    if output_stream is sys.stdout and hasattr(output_stream, "reconfigure"):
        output_stream.reconfigure(encoding="utf-8")
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                response = _jsonrpc_error(None, -32600, "JSON-RPC request 必须是对象")
            else:
                response = handle_request(request)
        except json.JSONDecodeError as exc:
            response = _jsonrpc_error(None, -32700, f"Invalid JSON: {exc}")
        except Exception as exc:  # Keep the stdio process alive for the next request.
            logger.exception("MCP request failed")
            response = _jsonrpc_error(None, -32603, str(exc))
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
