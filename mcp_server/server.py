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
from .tools.voice_assets import get_voice_asset, list_voice_assets
from .tools.voice_cast import (
    add_character_roles,
    bind_cast_role,
    check_chapter_roles,
    finalize_voice_cast,
    get_character_roster,
    get_voice_binding_status,
    get_voice_cast,
    set_character_roster,
    set_voice_cast,
    update_character_role,
    validate_character_roster,
    validate_voice_cast,
)
from .tools.production import (
    cancel_production,
    get_production_task,
    list_production_tasks,
    pause_production,
    plan_production,
    resume_production,
    retry_failed_segments,
    start_production,
)

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
    "list_voice_assets": {
        "name": "list_voice_assets",
        "description": "列出全局音色资产的稳定 ID 与元数据，不返回绝对路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "category": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "get_voice_asset": {
        "name": "get_voice_asset",
        "description": "读取一个稳定 voice_asset_id 的音色资产元数据。",
        "inputSchema": {
            "type": "object",
            "required": ["voice_asset_id"],
            "properties": {"voice_asset_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "set_character_roster": {
        "name": "set_character_roster",
        "description": "首次写入项目 Character Roster；不会静默覆盖已有角色表。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "roles"],
            "properties": {"project_name": {"type": "string"}, "roles": {}},
            "additionalProperties": False,
        },
    },
    "get_character_roster": {
        "name": "get_character_roster",
        "description": "读取项目完整 Character Roster。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "add_character_roles": {
        "name": "add_character_roles",
        "description": "以 additive 方式向项目 Character Roster 增加角色。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "roles"],
            "properties": {"project_name": {"type": "string"}, "roles": {}},
            "additionalProperties": False,
        },
    },
    "update_character_role": {
        "name": "update_character_role",
        "description": "显式更新一个角色定义；role_id 不可修改。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "role_id", "updates"],
            "properties": {
                "project_name": {"type": "string"},
                "role_id": {"type": "string"},
                "updates": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    "validate_character_roster": {
        "name": "validate_character_roster",
        "description": "校验角色 ID、canonical name、alias 及冲突。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}, "roles": {}},
            "additionalProperties": False,
        },
    },
    "set_voice_cast": {
        "name": "set_voice_cast",
        "description": "首次创建项目 Voice Cast，可先保存 draft。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "roles"],
            "properties": {"project_name": {"type": "string"}, "roles": {}},
            "additionalProperties": False,
        },
    },
    "get_voice_cast": {
        "name": "get_voice_cast",
        "description": "读取项目 Voice Cast 及校验摘要。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "bind_cast_role": {
        "name": "bind_cast_role",
        "description": "为一个 role_id 绑定或补绑声音；锁定角色必须显式 force_rebind。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "role_id", "voice_asset_id"],
            "properties": {
                "project_name": {"type": "string"},
                "role_id": {"type": "string"},
                "voice_asset_id": {"type": "string"},
                "force_rebind": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    "validate_voice_cast": {
        "name": "validate_voice_cast",
        "description": "校验演员表完整性、音频资产与锁定规则。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}, "roles": {}},
            "additionalProperties": False,
        },
    },
    "finalize_voice_cast": {
        "name": "finalize_voice_cast",
        "description": "在所有角色就绪后锁定项目 Voice Cast。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_voice_binding_status": {
        "name": "get_voice_binding_status",
        "description": "返回项目角色绑定、锁定与合成就绪状态。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "check_chapter_roles": {
        "name": "check_chapter_roles",
        "description": "检查增量章节中的已知、新增和未绑定角色。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "chapters"],
            "properties": {"project_name": {"type": "string"}, "chapters": {"type": "array"}},
            "additionalProperties": False,
        },
    },
    "plan_production": {
        "name": "plan_production",
        "description": "检查项目、章节、角色绑定和段落状态，返回可机器读取的生产计划。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "scope": {
                    "type": "object",
                    "properties": {
                        "all": {"type": "boolean", "default": True},
                        "chapter_ids": {"type": "array", "items": {"type": "string"}},
                        "segment_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    },
    "start_production": {
        "name": "start_production",
        "description": "异步启动统一生产任务，立即返回稳定 task_id。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "scope": {
                    "type": "object",
                    "properties": {
                        "all": {"type": "boolean", "default": True},
                        "chapter_ids": {"type": "array", "items": {"type": "string"}},
                        "segment_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
                "options": {
                    "type": "object",
                    "properties": {
                        "num_beams": {"type": "integer", "minimum": 1},
                        "emotion": {"type": ["string", "null"]},
                        "emo_alpha": {"type": ["number", "null"]},
                        "speech_rate": {"type": ["number", "null"]},
                    },
                    "additionalProperties": False,
                },
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "get_production_task": {
        "name": "get_production_task",
        "description": "读取生产任务的持久化状态和实时进度。",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "list_production_tasks": {
        "name": "list_production_tasks",
        "description": "按项目、状态或来源倒序列出生产任务。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "pending", "running", "pausing", "paused", "cancelling",
                        "cancelled", "done", "error", "interrupted",
                    ],
                },
                "source": {"type": "string", "enum": ["mcp", "web", "system", "recovery"]},
            },
            "additionalProperties": False,
        },
    },
    "pause_production": {
        "name": "pause_production",
        "description": "请求在当前段边界暂停生产。",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "resume_production": {
        "name": "resume_production",
        "description": "恢复 paused 或 interrupted 生产任务。",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "cancel_production": {
        "name": "cancel_production",
        "description": "请求取消生产；运行中先返回 cancelling，段边界后进入 cancelled。",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "retry_failed_segments": {
        "name": "retry_failed_segments",
        "description": "只重新生产指定任务中实际失败或缺失的段落。",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
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
    "list_voice_assets": list_voice_assets,
    "get_voice_asset": get_voice_asset,
    "set_character_roster": set_character_roster,
    "get_character_roster": get_character_roster,
    "add_character_roles": add_character_roles,
    "update_character_role": update_character_role,
    "validate_character_roster": validate_character_roster,
    "set_voice_cast": set_voice_cast,
    "get_voice_cast": get_voice_cast,
    "bind_cast_role": bind_cast_role,
    "validate_voice_cast": validate_voice_cast,
    "finalize_voice_cast": finalize_voice_cast,
    "get_voice_binding_status": get_voice_binding_status,
    "check_chapter_roles": check_chapter_roles,
    "plan_production": plan_production,
    "start_production": start_production,
    "get_production_task": get_production_task,
    "list_production_tasks": list_production_tasks,
    "pause_production": pause_production,
    "resume_production": resume_production,
    "cancel_production": cancel_production,
    "retry_failed_segments": retry_failed_segments,
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
