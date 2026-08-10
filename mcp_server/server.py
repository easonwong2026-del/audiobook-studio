"""Standalone Audiobook Studio MCP V1 stdio server.

This module implements the small JSON-RPC surface needed by MCP clients so
the base application does not need an additional runtime dependency.  The
transport can later be replaced or supplemented by Streamable HTTP without
changing the service adapters.
"""
from __future__ import annotations

import json
import logging
import re
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
    get_runtime_health,
    list_production_tasks,
    pause_production,
    plan_production,
    resume_production,
    retry_failed_segments,
    start_production,
)
from .tools.export import (
    get_delivery_manifest,
    get_export_task,
    list_exports,
    plan_export,
    start_export,
)
from .tools.quality import (
    get_quality_report,
    get_repair_task,
    get_segment_review,
    list_repairs,
    list_review_segments,
    mark_segment_review,
    regenerate_segments,
    run_technical_qa,
)
from .tools.workflow import get_workflow_state

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
                        "pending", "running", "pausing", "paused", "recovering",
                        "cancelling", "cancelled", "done", "error",
                        "interrupted", "needs_attention",
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
    "get_runtime_health": {
        "name": "get_runtime_health",
        "description": "返回 GPU-free 的 TTS 运行时 / 引擎健康快照（不加载模型）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

_TOOLS.update({
    "get_workflow_state": {
        "name": "get_workflow_state",
        "description": "派生整书工作流阶段、阻断项与 Agent 可执行的下一步。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_quality_report": {
        "name": "get_quality_report",
        "description": "返回项目技术 QA 与人工 Review 分离的质量汇总。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "list_review_segments": {
        "name": "list_review_segments",
        "description": "按质量状态列出待检查或待修复段落。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "unreviewed", "needs_review", "needs_fix", "passed",
                        "technical_warning", "regenerating", "pass", "warning", "fail",
                    ],
                },
            },
            "additionalProperties": False,
        },
    },
    "get_segment_review": {
        "name": "get_segment_review",
        "description": "读取单段 active audio revision、技术 QA 与人工 Review。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "segment_id"],
            "properties": {
                "project_name": {"type": "string"},
                "segment_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "mark_segment_review": {
        "name": "mark_segment_review",
        "description": "标记单段人工 Review 状态、问题类型和备注。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "segment_id", "review_status"],
            "properties": {
                "project_name": {"type": "string"},
                "segment_id": {"type": "string"},
                "review_status": {
                    "type": "string",
                    "enum": ["unreviewed", "needs_review", "needs_fix", "passed"],
                },
                "issue_type": {"type": "string"},
                "review_note": {"type": "string"},
                "reviewed_by": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "run_technical_qa": {
        "name": "run_technical_qa",
        "description": "对指定段落或整项目 active revisions 运行非 AI 技术 QA。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "segment_ids": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    "regenerate_segments": {
        "name": "regenerate_segments",
        "description": "创建 revision-safe Repair Job 并通过唯一 Production Runtime 重合成。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "segment_ids"],
            "properties": {
                "project_name": {"type": "string"},
                "segment_ids": {
                    "type": "array", "minItems": 1, "items": {"type": "string"},
                },
                "emotion": {"type": ["string", "null"]},
                "emo_alpha": {"type": ["number", "null"]},
                "speech_rate": {"type": ["number", "null"]},
                "voice_override": {"type": ["string", "null"]},
                "note": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "get_repair_task": {
        "name": "get_repair_task",
        "description": "刷新并读取 Repair Job 状态与 revision 结果。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "repair_id"],
            "properties": {
                "project_name": {"type": "string"},
                "repair_id": {"type": "string"},
                "refresh": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    "list_repairs": {
        "name": "list_repairs",
        "description": "列出项目 Repair 历史。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "plan_export": {
        "name": "plan_export",
        "description": "检查 active revisions、QA、metadata 与 FFmpeg 交付准备度。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "format": {"type": "string", "enum": ["wav", "mp3", "m4b"]},
                "qa_policy": {
                    "type": "string",
                    "enum": ["require_passed", "technical", "allow_unreviewed"],
                },
                "subtitle_formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["srt", "lrc"]},
                },
            },
            "additionalProperties": False,
        },
    },
    "start_export": {
        "name": "start_export",
        "description": "通过 readiness gate 导出并保存 Export Job 与 Delivery Manifest。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "format": {"type": "string", "enum": ["wav", "mp3", "m4b"]},
                "bitrate": {"type": "string"},
                "qa_policy": {
                    "type": "string",
                    "enum": ["require_passed", "technical", "allow_unreviewed"],
                },
                "subtitle_formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["srt", "lrc"]},
                },
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "get_export_task": {
        "name": "get_export_task",
        "description": "读取一个 Export Job 的公共状态与相对输出路径。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name", "export_id"],
            "properties": {
                "project_name": {"type": "string"},
                "export_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "list_exports": {
        "name": "list_exports",
        "description": "列出项目 Export Job 历史。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {"project_name": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_delivery_manifest": {
        "name": "get_delivery_manifest",
        "description": "读取指定导出或最近成功导出的公共交付清单。",
        "inputSchema": {
            "type": "object",
            "required": ["project_name"],
            "properties": {
                "project_name": {"type": "string"},
                "export_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
})

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
    "get_runtime_health": get_runtime_health,
    "get_workflow_state": get_workflow_state,
    "get_quality_report": get_quality_report,
    "list_review_segments": list_review_segments,
    "get_segment_review": get_segment_review,
    "mark_segment_review": mark_segment_review,
    "run_technical_qa": run_technical_qa,
    "regenerate_segments": regenerate_segments,
    "get_repair_task": get_repair_task,
    "list_repairs": list_repairs,
    "plan_export": plan_export,
    "start_export": start_export,
    "get_export_task": get_export_task,
    "list_exports": list_exports,
    "get_delivery_manifest": get_delivery_manifest,
}


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_call_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    # MCP requires structuredContent to be a JSON object.  Adapters should
    # return objects; this safety net keeps list/primitive payloads from
    # breaking clients with an invalid_type error.
    structured = payload
    if not isinstance(payload, dict):
        logger.warning(
            "MCP tool returned non-object payload (type=%s); wrapping for "
            "structuredContent compatibility",
            type(payload).__name__,
        )
        structured = {"result": payload}
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(structured, ensure_ascii=False, sort_keys=True),
        }],
        "structuredContent": structured,
        "isError": is_error,
    }


def _public_error_message(value: Any) -> str:
    return re.sub(
        r"(?:[A-Za-z]:[\\/]|/(?:Users|home|private|tmp|var|opt)/)[^\s,;)]*",
        "<local-path>",
        str(value or ""),
    )


def _public_error_value(value: Any) -> Any:
    if isinstance(value, str):
        return _public_error_message(value)
    if isinstance(value, dict):
        return {
            str(key): _public_error_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_error_value(item) for item in value]
    return value


def _exception_payload(exc: Exception) -> dict[str, Any]:
    converter = getattr(exc, "as_payload", None)
    if callable(converter):
        payload = converter()
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            return payload
    plan = getattr(exc, "plan", None)
    if isinstance(plan, dict):
        return {
            "error": {
                "code": "EXPORT_NOT_READY",
                "message": "交付准备度检查未通过",
                "fix_hint": "处理 blockers 后重新调用 plan_export。",
                "details": {"blockers": plan.get("blockers", [])},
            }
        }
    return {
        "error": {
            "code": type(exc).__name__.upper(),
            "message": _public_error_message(exc),
            "fix_hint": "",
            "details": {},
        }
    }


def _normalize_tool_payload(payload: Any) -> tuple[Any, bool]:
    """Normalize adapter/domain failures to one Agent-facing error contract."""
    if not isinstance(payload, dict):
        return payload, False
    raw_error = payload.get("error")
    # A payload with a top-level "error" key is a failure envelope only when
    # it carries the full error contract (code + message).  Task snapshots
    # may legitimately expose an "error" object for needs_attention without
    # being a tool failure.
    if isinstance(raw_error, dict) and "message" in raw_error:
        details = raw_error.get("details")
        if not isinstance(details, dict):
            details = {
                key: value
                for key, value in raw_error.items()
                if key not in {"code", "message", "fix_hint", "details"}
            }
        return {
            "error": {
                "code": str(raw_error.get("code") or "TOOL_ERROR"),
                "message": _public_error_message(raw_error.get("message") or "工具调用失败"),
                "fix_hint": str(raw_error.get("fix_hint") or ""),
                "details": _public_error_value(details),
            }
        }, True
    if payload.get("success") is False:
        errors = payload.get("errors")
        issues = errors if isinstance(errors, list) else []
        first = issues[0] if issues and isinstance(issues[0], dict) else {}
        return {
            "error": {
                "code": str(first.get("code") or "TOOL_REJECTED"),
                "message": _public_error_message(
                    first.get("message") or "工具调用被业务规则拒绝"
                ),
                "fix_hint": str(first.get("fix_hint") or ""),
                "details": {"errors": _public_error_value(issues)},
            }
        }, True
    return payload, False


def _validate_arguments(schema: dict[str, Any], value: Any, path: str = "arguments") -> None:
    """Enforce the small JSON Schema subset advertised by MCP tools."""
    expected = schema.get("type")
    allowed_types = expected if isinstance(expected, list) else [expected]
    if value is None and "null" in allowed_types:
        return
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        None: True,
    }
    if not any(type_matches.get(item, False) for item in allowed_types):
        raise ValueError(f"{path} 类型不符合 schema")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path} 缺少必填字段: {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ValueError(f"{path} 包含未知字段: {', '.join(extra)}")
        for key, child in value.items():
            if key in properties:
                _validate_arguments(properties[key], child, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0) or 0):
            raise ValueError(f"{path} 项目数量不足")
        child_schema = schema.get("items")
        if isinstance(child_schema, dict):
            for index, child in enumerate(value):
                _validate_arguments(child_schema, child, f"{path}[{index}]")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 不在允许值中")


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
            _validate_arguments(_TOOLS[name]["inputSchema"], arguments)
            payload = handler(arguments)
            payload, payload_is_error = _normalize_tool_payload(payload)
            result = _tool_call_result(
                payload,
                is_error=payload_is_error,
            )
        except Exception as exc:  # MCP clients receive a structured tool error.
            logger.exception("MCP tool failed: %s", name)
            payload, _payload_is_error = _normalize_tool_payload(
                _exception_payload(exc)
            )
            result = _tool_call_result(payload, is_error=True)
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
