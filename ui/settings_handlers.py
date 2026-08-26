"""UI callbacks for storage, project remnants, TTS settings, and diagnostics."""
from __future__ import annotations

import html
import json
import logging
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from lib import config
from repositories._atomic import atomic_write
from repositories.config_repo import ConfigRepository
from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRepository
from services import ProjectService
from services.environment_diagnostics import (
    diagnostics_table,
    diagnostics_to_markdown,
    run_environment_diagnostics,
)

logger = logging.getLogger(__name__)
_CONFIG_PATH_AT_IMPORT = str(getattr(ConfigRepository, "CONFIG_PATH", "") or "")

TTS_ENGINE_LEGACY = "legacy"
TTS_ENGINE_25 = "indextts25"
TTS_ENGINE_LABELS = {
    TTS_ENGINE_LEGACY: "IndexTTS 2 Legacy / 回滚",
    TTS_ENGINE_25: "IndexTTS 2.5（推荐）",
}
TTS_ENGINE_CHOICES = [
    (TTS_ENGINE_LABELS[TTS_ENGINE_LEGACY], TTS_ENGINE_LEGACY),
    (TTS_ENGINE_LABELS[TTS_ENGINE_25], TTS_ENGINE_25),
]

_ENGINE_ALIASES = {
    TTS_ENGINE_LEGACY: {
        "legacy", "indextts2", "indextts2legacy", "v2", "2", "2.0",
        "indextts2_legacy", "index_tts_2_legacy", "index_tts_2",
    },
    TTS_ENGINE_25: {
        "indextts25", "indextts2.5", "indextts2_5", "v2.5", "v25", "2.5",
        "index_tts25", "index_tts_2_5", "index_tts_25",
    },
}
_TASK_TYPE_LABELS = {
    "synthesis": "合成",
    "voice_preview": "试听",
    "preview": "试听",
    "supplement": "补录",
    "quick_tts": "临时配音",
    "export": "导出",
}
_TASK_REJECTION_MESSAGES = {
    "synthesis": "当前有生产任务正在运行，请等待任务结束或取消后再切换 TTS 引擎。",
    "voice_preview": "当前有试听任务正在运行，请等待试听结束或取消后再切换 TTS 引擎。",
    "preview": "当前有试听任务正在运行，请等待试听结束或取消后再切换 TTS 引擎。",
    "supplement": "当前有补录任务正在运行，请等待补录结束或取消后再切换 TTS 引擎。",
    "quick_tts": "当前有临时配音任务正在运行，请等待结束后再切换 TTS 引擎。",
    "export": "当前有导出任务正在运行，请等待导出结束或取消后再切换 TTS 引擎。",
}

def get_prewarm_setting() -> bool:
    """Read the「启动后预热默认 TTS 引擎」toggle (default enabled)."""
    from services.prewarm import PrewarmService

    return PrewarmService.is_enabled()


def apply_prewarm_setting(enabled: bool) -> str:
    """Persist the prewarm toggle; returns a user-facing message."""
    from services.prewarm import PrewarmService

    return PrewarmService.set_enabled(enabled)


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None and str(value).strip()), None)


def _normalize_engine(value: Any, default: str | None = None) -> str | None:
    raw = str(value or "").strip().lower().replace(" ", "").replace("-", "_")
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    for engine_id, aliases in _ENGINE_ALIASES.items():
        if raw in aliases:
            return engine_id
    return default


def _engine_from_version(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace("_", ".").replace("-", ".")
    if raw in {"v2", "2", "2.0"}:
        return TTS_ENGINE_LEGACY
    if raw in {"v2.5", "v25", "2.5"}:
        return TTS_ENGINE_25
    return None


def _version_for_engine(engine_id: str) -> str:
    return "2.5" if engine_id == TTS_ENGINE_25 else "2"


def _config_path() -> str:
    repository_path = str(getattr(ConfigRepository, "CONFIG_PATH", "") or "")
    config_path = str(getattr(config, "CONFIG_PATH", "") or "")
    if repository_path and repository_path != _CONFIG_PATH_AT_IMPORT:
        return repository_path
    return config_path or repository_path or "config.json"


def _read_raw_config() -> dict[str, Any]:
    try:
        with open(_config_path(), encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _clean_path(value: Any) -> str:
    text = str(value or "").strip()
    return os.path.abspath(os.path.expanduser(text)) if text else ""


def _profile() -> dict[str, Any]:
    getter = getattr(config, "get_tts_profile", None)
    if not callable(getter):
        return {}
    try:
        value = getter()
    except (OSError, RuntimeError, TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _resolved_model_dirs() -> tuple[str, str]:
    """Use the branch's resolver when available; return paths only."""
    try:
        from lib import environment

        resolved = environment.resolve_model_directories()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return "", ""
    v2 = resolved.get("v2", {}) if isinstance(resolved, Mapping) else {}
    v25 = resolved.get("v2.5", {}) if isinstance(resolved, Mapping) else {}
    return (
        str(v2.get("path") or "") if isinstance(v2, Mapping) else "",
        str(v25.get("path") or "") if isinstance(v25, Mapping) else "",
    )


def get_tts_engine_settings() -> dict[str, Any]:
    """Read the selected engine and both model directories without loading TTS."""
    raw = _read_raw_config()
    profile = _profile()
    profile_version = _first(profile.get("engine_version"), profile.get("version"))
    raw_version = _first(
        os.environ.get("AUDIOBOOK_STUDIO_ENGINE_VERSION"),
        os.environ.get("AUDIOBOOK_STUDIO_VERSION"),
        raw.get("engine_version"),
        raw.get("tts_version"),
    )
    explicit_engine = _first(
        os.environ.get("AUDIOBOOK_STUDIO_ENGINE"),
        raw.get("tts_engine"),
        raw.get("active_tts_engine"),
    )
    legacy_configured = _first(
        raw.get("model_dir_v2"),
        raw.get("legacy_model_dir"),
        raw.get("model_dir_2"),
        os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_V2"),
        os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_LEGACY"),
        os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR"),
        raw.get("model_dir"),
    )

    selected = _engine_from_version(raw_version)
    if selected is None:
        selected = _normalize_engine(explicit_engine)
    if selected is None and not raw_version and not explicit_engine and legacy_configured:
        selected = TTS_ENGINE_LEGACY
    if selected is None:
        selected = _engine_from_version(profile_version) or TTS_ENGINE_25

    resolved_v2, resolved_v25 = _resolved_model_dirs()
    legacy_dir = _clean_path(_first(
        legacy_configured,
        raw.get("legacy_model_dir"),
        profile.get("model_dir") if _engine_from_version(profile_version) == TTS_ENGINE_LEGACY else None,
        os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_LEGACY"),
        resolved_v2,
        config.get_model_dir(),
    ))
    engine_25_dir = _clean_path(_first(
        raw.get("indextts25_model_dir"),
        raw.get("model_dir_v25"),
        raw.get("model_dir_v2_5"),
        raw.get("model_dir_2_5"),
        profile.get("model_dir") if _engine_from_version(profile_version) == TTS_ENGINE_25 else None,
        os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_V25"),
        os.environ.get("AUDIOBOOK_STUDIO_MODEL_DIR_2_5"),
        resolved_v25,
        os.path.join(os.path.dirname(legacy_dir), "checkpoints-v2.5") if legacy_dir else None,
    ))
    return {
        "engine": selected,
        "legacy_model_dir": legacy_dir,
        "indextts25_model_dir": engine_25_dir,
    }


def _model_ready(model_dir: str, _version: str | None = None) -> bool:
    if not model_dir:
        return False
    try:
        from lib.tts_model_layout import resolve_model_config_path

        return resolve_model_config_path(_version or "v2", model_dir) is not None
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _ready_message(model_dir: str, version: str | None = None) -> str:
    if _model_ready(model_dir, version):
        return "✅ 已就绪"
    config_label = "config.yaml / config.yml" if str(version or "v2") in {"v2", "2"} else "config_v2_5.yaml / config.yaml / config.yml"
    return f"⚠ 未就绪：目录不存在或缺少 {config_label} / 必需模型文件（{html.escape(model_dir or '未配置')}）"


def _active_tts_tasks() -> list[Any]:
    try:
        return TaskRepository.list_live_tts_tasks()
    except Exception:  # pragma: no cover - engine switching must fail closed
        logger.debug("读取活动 TTS 任务失败", exc_info=True)
        return [None]


def _engine_from_task(record: Any) -> str | None:
    options = getattr(record, "options", {})
    sources = [options] if isinstance(options, Mapping) else []
    snapshot = options.get("engine_snapshot") if isinstance(options, Mapping) else None
    if isinstance(snapshot, Mapping):
        sources.insert(0, snapshot)
    for source in sources:
        selected = _engine_from_version(source.get("engine_version"))
        if selected:
            return selected
        selected = _normalize_engine(_first(
            source.get("engine_identity"), source.get("engine_id"),
            source.get("tts_engine"), source.get("engine"),
        ))
        if selected:
            return selected
    return None


def _runtime_snapshots() -> list[Mapping[str, Any]]:
    snapshots: list[Mapping[str, Any]] = []
    try:
        from services.production_jobs import ProductionJobService

        health = ProductionJobService.get_runtime_health()
        if isinstance(health, Mapping):
            snapshots.append(health)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.debug("读取 runtime health 失败", exc_info=True)
    try:
        from services.runtime_engine import read_runtime_engine_status

        status = read_runtime_engine_status()
        if isinstance(status, Mapping):
            snapshots.append(status)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.debug("读取 runtime engine status 失败", exc_info=True)
    return snapshots


def _runtime_engine_details() -> tuple[str | None, str, str]:
    engine_id: str | None = None
    engine_state = "unknown"
    runtime_state = "unknown"
    for snapshot in _runtime_snapshots():
        if engine_id is None:
            engine_id = _engine_from_version(snapshot.get("engine_version"))
            engine_id = engine_id or _normalize_engine(_first(
                snapshot.get("engine_identity"), snapshot.get("engine_id"),
                snapshot.get("tts_engine"), snapshot.get("runtime_engine"),
            ))
        engine_state = str(snapshot.get("engine_state") or snapshot.get("state") or engine_state)
        runtime_state = str(snapshot.get("runtime_state") or runtime_state)
    return engine_id, engine_state, runtime_state


def _runtime_engine_message() -> str:
    engine_id, engine_state, runtime_state = _runtime_engine_details()
    actual = bool(engine_id)
    if not actual:
        try:
            profile = config.get_tts_profile()
            engine_id = _engine_from_version(profile.get("engine_version")) if isinstance(profile, Mapping) else None
        except (OSError, RuntimeError, TypeError, ValueError):
            engine_id = None
    engine_label = TTS_ENGINE_LABELS.get(engine_id or "", "未知")
    states = {
        "ready": "已就绪", "loading": "加载中", "recovering": "回收重载中",
        "error": "错误", "uninitialized": "未加载", "unknown": "未知",
    }
    runtimes = {
        "running": "运行中", "starting": "启动中", "recovering": "恢复中",
        "stopping": "停止中", "error": "错误", "unknown": "未运行",
    }
    return (
        f"{'实际 runtime engine' if actual else '当前默认 engine'}：**{engine_label}** · "
        f"引擎状态：{states.get(engine_state, engine_state)} · "
        f"runtime：{runtimes.get(runtime_state, runtime_state)}"
    )


def _frozen_engine_message(tasks: list[Any] | None = None) -> str:
    active = tasks if tasks is not None else _active_tts_tasks()
    if not active:
        return "任务冻结 engine：**无活动任务**"
    record = active[0]
    engine_id = _engine_from_task(record)
    task_id = html.escape(str(getattr(record, "task_id", "") or "未知任务"))
    label = TTS_ENGINE_LABELS.get(engine_id or "", "未知（当前任务未记录）")
    return f"任务冻结 engine：**{label}**（任务 `{task_id}`）"


def get_tts_engine_ui_state() -> dict[str, Any]:
    settings = get_tts_engine_settings()
    tasks = _active_tts_tasks()
    return {
        **settings,
        "legacy_ready": _model_ready(settings["legacy_model_dir"], "v2"),
        "indextts25_ready": _model_ready(settings["indextts25_model_dir"], "v2.5"),
        "legacy_model_status": _ready_message(settings["legacy_model_dir"], "v2"),
        "indextts25_model_status": _ready_message(settings["indextts25_model_dir"], "v2.5"),
        "runtime_engine_message": _runtime_engine_message(),
        "frozen_engine_message": _frozen_engine_message(tasks),
    }


def _persist_tts_engine_settings(
    engine_id: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
) -> None:
    """Persist the UI choice while preserving unrelated config keys."""
    version = _version_for_engine(engine_id)
    profile = {
        "engine_backend": "indextts",
        "engine_version": version,
        "model_dir": legacy_model_dir if engine_id == TTS_ENGINE_LEGACY else engine_25_model_dir,
        "model_dir_v2": legacy_model_dir,
        "model_dir_v25": engine_25_model_dir,
    }
    for name in ("set_tts_profile", "set_tts_engine_config", "set_tts_config"):
        setter = getattr(config, name, None)
        if callable(setter):
            try:
                setter(profile)
            except TypeError:
                setter(engine=engine_id, model_dirs={
                    TTS_ENGINE_LEGACY: legacy_model_dir,
                    TTS_ENGINE_25: engine_25_model_dir,
                })
            break

    data = _read_raw_config()
    data.update({
        "tts_engine": engine_id,
        "engine_backend": "indextts",
        "engine_version": version,
        "model_dir": profile["model_dir"],
        "model_dir_v2": legacy_model_dir,
        "model_dir_v25": engine_25_model_dir,
        "legacy_model_dir": legacy_model_dir,
        "indextts25_model_dir": engine_25_model_dir,
        "tts_model_dirs": {
            TTS_ENGINE_LEGACY: legacy_model_dir,
            TTS_ENGINE_25: engine_25_model_dir,
        },
    })
    atomic_write(_config_path(), data)


def _request_runtime_recycle(engine_id: str) -> str:
    """Call the mainline runtime recycle API, with an inline-only fallback."""
    for target_name in ("ProductionRuntimeClient", "ProductionJobService"):
        try:
            if target_name == "ProductionRuntimeClient":
                from services.production_runtime import (
                    ProductionRuntimeClient as target,
                )
            else:
                from services.production_jobs import ProductionJobService as target
        except ImportError:
            continue
        for method_name in (
            "request_tts_engine_recycle",
            "request_engine_recycle",
            "request_runtime_recycle",
            "request_engine_switch",
        ):
            method = getattr(target, method_name, None)
            if not callable(method):
                continue
            try:
                method(engine_id)
            except TypeError:
                method(engine=engine_id)
            return "已提交受控 runtime recycle 请求"

    from services.production_runtime import ProductionRuntimeClient

    if str(ProductionRuntimeClient.mode()).lower() == "inline":
        ProductionRuntimeClient.reset_inline()
        ProductionRuntimeClient.ensure_running()
        return "已触发受控 runtime recycle"
    ProductionRuntimeClient.ensure_running()
    return "已记录受控 runtime recycle 请求，runtime 将在下次启动时加载新引擎"


def _tts_output_values(
    message: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
    tasks: list[Any] | None = None,
) -> tuple[str, str, str, str, str]:
    return (
        message,
        _ready_message(legacy_model_dir, "v2"),
        _ready_message(engine_25_model_dir, "v2.5"),
        _runtime_engine_message(),
        _frozen_engine_message(tasks),
    )


def refresh_tts_engine_ui(
    _engine_id: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
) -> tuple[str, str, str, str, str]:
    return _tts_output_values(
        "已刷新模型目录与 runtime 状态。",
        _clean_path(legacy_model_dir),
        _clean_path(engine_25_model_dir),
    )


def apply_tts_engine(
    engine_id: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
) -> tuple[str, str, str, str, str]:
    """Save the selected engine only when all TTS lanes are idle."""
    selected = _normalize_engine(engine_id)
    legacy_dir = _clean_path(legacy_model_dir)
    engine_25_dir = _clean_path(engine_25_model_dir)
    if not selected:
        return _tts_output_values("❌ 未知的 TTS 引擎，未保存设置。", legacy_dir, engine_25_dir)

    active = _active_tts_tasks()
    if active:
        rejection_messages = list(dict.fromkeys(
            _TASK_REJECTION_MESSAGES.get(
                str(getattr(item, "task_type", "")),
                "当前有生产任务正在运行，请等待任务结束或取消后再切换 TTS 引擎。",
            )
            for item in active
        ))
        task_labels = "、".join(
            dict.fromkeys(_TASK_TYPE_LABELS.get(str(getattr(item, "task_type", "")), "生产") for item in active)
        )
        message = (
            "当前有生产任务正在运行，请等待任务结束或取消后再切换 TTS 引擎。"
            f"（{task_labels}）\n⚠ 无法切换 TTS 引擎：" + "；".join(rejection_messages)
        )
        return _tts_output_values(
            message,
            legacy_dir,
            engine_25_dir,
            active,
        )

    try:
        _persist_tts_engine_settings(selected, legacy_dir, engine_25_dir)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        message = f"❌ TTS 引擎设置保存失败：{html.escape(str(exc))}"
    else:
        try:
            recycle_message = _request_runtime_recycle(selected)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            message = (
                f"⚠ 已保存 {TTS_ENGINE_LABELS[selected]}，但 runtime recycle 失败："
                f"{html.escape(str(exc))}。请重启生产运行时后再试。"
            )
        else:
            message = f"✅ 已应用 {TTS_ENGINE_LABELS[selected]}；{recycle_message}。"
    return _tts_output_values(message, legacy_dir, engine_25_dir)


def refresh_abnormal_projects() -> tuple:

    inspections = ProjectRepository.list_abnormal_projects()
    rows = []
    for item in inspections:
        details = "、".join([*item.missing_files, *item.invalid_files])
        modified = (
            datetime.fromtimestamp(item.modified_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if item.modified_at
            else ""
        )
        rows.append([item.name, item.status, item.path, details, modified])
    choices = [item.name for item in inspections]
    import gradio as gr

    return (
        rows,
        gr.update(choices=choices, value=choices[0] if choices else None),
        f"共发现 {len(choices)} 个异常或残留项目目录。",
    )


def refresh_abnormal_project_data() -> tuple:
    rows, selection, _status = refresh_abnormal_projects()
    return rows, selection


def open_abnormal_project(project_name: str) -> str:
    name = str(project_name or "").strip()
    if not name:
        return "⚠ 请先选择异常项目"
    inspection = ProjectRepository.inspect_project_slot(name)
    if inspection.status not in {"incomplete", "corrupted", "temporary"}:
        return "⚠ 该项目不属于可处理的工作区残留"
    try:
        import subprocess
        import sys

        if sys.platform == "win32":
            os.startfile(inspection.path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", inspection.path])
        else:
            subprocess.Popen(["xdg-open", inspection.path])
        return f"✅ 已打开：`{inspection.path}`"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 打开目录失败：{html.escape(str(exc))}"


def archive_abnormal_project(project_name: str) -> str:
    name = str(project_name or "").strip()
    if not name:
        return "⚠ 请先选择异常项目"
    try:
        target = ProjectRepository.archive_orphan_project(name)
        return f"✅ 已移动到回收站：`{target}`"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 归档失败：{html.escape(str(exc))}"


def apply_data_dir(new_dir: str, ss=None) -> tuple:
    if not new_dir or not str(new_dir).strip():
        return "⚠ 请填写保存位置", ""
    try:
        path = os.path.normpath(ProjectService.set_data_dir(str(new_dir).strip()))
        if ss is not None:
            # A same-named project in the new root is still a different asset
            # context.  Clear selected/opened/session state before the catalog
            # reconciliation callback runs; keep catalog_query by contract.
            ss.reset_for_data_root()
        return f"✅ 数据目录已设置为：{path}（本会话立即生效）", path
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 设置失败：{html.escape(str(exc))}", ""


def open_data_dir() -> str:
    data_dir = config.get_data_dir()
    try:
        import subprocess
        import sys

        if sys.platform == "win32":
            os.startfile(data_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", data_dir])
        else:
            subprocess.Popen(["xdg-open", data_dir])
        return f"✅ 已打开数据目录：`{data_dir}`"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"❌ 打开数据目录失败：{html.escape(str(exc))}"


def run_diagnostics_ui():
    report = run_environment_diagnostics()
    symbol = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(report["status"], "❓")
    return (
        f"### {symbol} 总体状态：{report['status']}",
        diagnostics_table(report),
        diagnostics_to_markdown(report),
    )
