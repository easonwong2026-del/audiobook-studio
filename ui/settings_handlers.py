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

TTS2_PERFORMANCE_LABELS = {
    "cuda_kernel": "CUDA Kernel",
    "gpt_accel": "GPT Accel",
    "s2mel_compile": "s2mel torch.compile",
    "conditioning_cache": "多音色 Conditioning Cache",
}
TTS25_PERFORMANCE_LABELS = {"gpt_accel": "GPT Accel"}

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


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return bool(default)


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
    performance = config.get_tts_performance(data=raw)
    return {
        "engine": selected,
        "legacy_model_dir": legacy_dir,
        "indextts25_model_dir": engine_25_dir,
        "tts2_performance": performance[config.TTS2_PERFORMANCE_KEY],
        "tts25_performance": performance[config.TTS25_PERFORMANCE_KEY],
        config.INDEXTTS25_GPT_ACCEL_CONFIG_KEY: _bool_value(
            performance[config.TTS25_PERFORMANCE_KEY]["gpt_accel"], True
        ),
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
        "tts_status_message": _performance_status_message(settings, tasks),
    }


def _performance_from_values(
    indextts25_gpt_accel_enabled: Any,
    tts2_cuda_kernel: Any,
    tts2_gpt_accel: Any,
    tts2_s2mel_compile: Any,
    tts2_conditioning_cache: Any,
) -> dict[str, dict[str, bool]]:
    return {
        config.TTS2_PERFORMANCE_KEY: {
            "cuda_kernel": _bool_value(tts2_cuda_kernel, True),
            "gpt_accel": _bool_value(tts2_gpt_accel, False),
            "s2mel_compile": _bool_value(tts2_s2mel_compile, False),
            "conditioning_cache": _bool_value(tts2_conditioning_cache, False),
        },
        config.TTS25_PERFORMANCE_KEY: {
            "gpt_accel": _bool_value(indextts25_gpt_accel_enabled, True),
        },
    }


def _performance_changed(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    return any(
        dict(left.get(lane) or {}) != dict(right.get(lane) or {})
        for lane in (config.TTS2_PERFORMANCE_KEY, config.TTS25_PERFORMANCE_KEY)
    )


def _performance_status_message(
    settings: Mapping[str, Any],
    tasks: list[Any] | None = None,
) -> str:
    selected = str(settings.get("engine") or TTS_ENGINE_25)
    requested = {
        config.TTS2_PERFORMANCE_KEY: dict(settings.get("tts2_performance") or {}),
        config.TTS25_PERFORMANCE_KEY: dict(settings.get("tts25_performance") or {}),
    }
    runtime = None
    for snapshot in _runtime_snapshots():
        runtime_engine = _engine_from_version(snapshot.get("engine_version"))
        runtime_engine = runtime_engine or _normalize_engine(
            _first(snapshot.get("engine_identity"), snapshot.get("engine"))
        )
        if runtime_engine == selected:
            runtime = snapshot
            break
    if runtime is not None and str(runtime.get("engine_state") or runtime.get("state")) == "ready":
        runtime_requested = runtime.get("performance")
        if isinstance(runtime_requested, Mapping):
            lane = config.TTS25_PERFORMANCE_KEY if selected == TTS_ENGINE_25 else config.TTS2_PERFORMANCE_KEY
            if dict(runtime_requested.get(lane) or runtime_requested) == requested[lane]:
                unavailable = []
                status = runtime.get("performance_status")
                if isinstance(status, Mapping):
                    labels = TTS2_PERFORMANCE_LABELS | TTS25_PERFORMANCE_LABELS
                    for field, item in status.items():
                        if isinstance(item, Mapping) and item.get("state") == "unavailable":
                            unavailable.append(labels.get(field, field))
                if unavailable:
                    return "状态：当前环境不可用，已安全回退 baseline（" + "、".join(unavailable) + "）"
                message = "状态：已生效"
                if (
                    selected == TTS_ENGINE_25
                    and requested[config.TTS25_PERFORMANCE_KEY].get("gpt_accel")
                    and os.environ.get("AUDIOBOOK_STUDIO_DISABLE_INDEXTTS25_ACCEL") == "1"
                ):
                    return message + "；GPT Accel 实际被环境变量关闭"
                return message
    active = tasks if tasks is not None else _active_tts_tasks()
    if active:
        return "状态：已保存，将在当前任务结束后生效"
    return "状态：已保存，将在下一次 runtime 初始化/切换时生效"


def _persist_tts_engine_settings(
    engine_id: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
    indextts25_gpt_accel_enabled: bool = True,
    *,
    performance: Mapping[str, Any] | None = None,
) -> None:
    """Persist the UI choice while preserving unrelated config keys."""
    version = _version_for_engine(engine_id)
    accel_enabled = _bool_value(indextts25_gpt_accel_enabled, True)
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
    performance_updates = performance if isinstance(performance, Mapping) else {
        config.TTS25_PERFORMANCE_KEY: {"gpt_accel": accel_enabled},
    }
    data = config.merge_tts_performance(data, performance_updates)
    saved_performance = config.get_tts_performance(data=data)
    data.update({
        "tts_engine": engine_id,
        "engine_backend": "indextts",
        "engine_version": version,
        "model_dir": profile["model_dir"],
        "model_dir_v2": legacy_model_dir,
        "model_dir_v25": engine_25_model_dir,
        "legacy_model_dir": legacy_model_dir,
        "indextts25_model_dir": engine_25_model_dir,
        config.INDEXTTS25_GPT_ACCEL_CONFIG_KEY: saved_performance[
            config.TTS25_PERFORMANCE_KEY
        ]["gpt_accel"],
        "tts_model_dirs": {
            TTS_ENGINE_LEGACY: legacy_model_dir,
            TTS_ENGINE_25: engine_25_model_dir,
        },
    })
    atomic_write(_config_path(), data)


def _persist_with_performance(
    engine_id: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
    performance: Mapping[str, Any],
) -> None:
    """Call the private persistence seam while retaining old test/caller arity."""
    accel_enabled = bool(
        (performance.get(config.TTS25_PERFORMANCE_KEY) or {}).get("gpt_accel", True)
    )
    try:
        _persist_tts_engine_settings(
            engine_id,
            legacy_model_dir,
            engine_25_model_dir,
            accel_enabled,
            performance=performance,
        )
    except TypeError as exc:
        if "performance" not in str(exc):
            raise
        _persist_tts_engine_settings(
            engine_id,
            legacy_model_dir,
            engine_25_model_dir,
            accel_enabled,
        )


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
    indextts25_gpt_accel_enabled: bool = True,
    tts2_cuda_kernel: bool = True,
    tts2_gpt_accel: bool = False,
    tts2_s2mel_compile: bool = False,
    tts2_conditioning_cache: bool = False,
) -> tuple[str, str, str, str, str]:
    del _engine_id, tts2_cuda_kernel, tts2_gpt_accel, tts2_s2mel_compile
    del tts2_conditioning_cache
    settings = get_tts_engine_settings()
    message = "已刷新模型目录与 runtime 状态。\n" + _performance_status_message(settings)
    if (
        _bool_value(indextts25_gpt_accel_enabled, True)
        and os.environ.get("AUDIOBOOK_STUDIO_DISABLE_INDEXTTS25_ACCEL") == "1"
    ):
        message += "\n⚠ GPT Accel：配置：开启；实际：环境变量已强制关闭。"
    return _tts_output_values(
        message,
        _clean_path(legacy_model_dir),
        _clean_path(engine_25_model_dir),
    )


def apply_tts_engine(
    engine_id: str,
    legacy_model_dir: str,
    engine_25_model_dir: str,
    indextts25_gpt_accel_enabled: bool = True,
    tts2_cuda_kernel: bool = True,
    tts2_gpt_accel: bool = False,
    tts2_s2mel_compile: bool = False,
    tts2_conditioning_cache: bool = False,
) -> tuple[str, str, str, str, str]:
    """Save engine/profile settings and apply performance changes safely."""
    selected = _normalize_engine(engine_id)
    legacy_dir = _clean_path(legacy_model_dir)
    engine_25_dir = _clean_path(engine_25_model_dir)
    performance = _performance_from_values(
        indextts25_gpt_accel_enabled,
        tts2_cuda_kernel,
        tts2_gpt_accel,
        tts2_s2mel_compile,
        tts2_conditioning_cache,
    )
    if not selected:
        return _tts_output_values("❌ 未知的 TTS 引擎，未保存设置。", legacy_dir, engine_25_dir)

    current = get_tts_engine_settings()
    engine_changed = any((
        selected != current.get("engine"),
        legacy_dir != current.get("legacy_model_dir"),
        engine_25_dir != current.get("indextts25_model_dir"),
    ))
    active = _active_tts_tasks()
    if active:
        if engine_changed:
            rejection_messages = list(dict.fromkeys(
                _TASK_REJECTION_MESSAGES.get(
                    str(getattr(item, "task_type", "")),
                    "当前有生产任务正在运行，请等待任务结束或取消后再切换 TTS 引擎。",
                )
                for item in active
            ))
            task_labels = "、".join(
                dict.fromkeys(
                    _TASK_TYPE_LABELS.get(
                        str(getattr(item, "task_type", "")), "生产"
                    )
                    for item in active
                )
            )
            message = (
                "当前有生产任务正在运行，请等待任务结束或取消后再切换 TTS 引擎。"
                f"（{task_labels}）\n⚠ 无法切换 TTS 引擎："
                + "；".join(rejection_messages)
            )
            return _tts_output_values(message, legacy_dir, engine_25_dir, active)

    try:
        _persist_with_performance(
            selected,
            legacy_dir,
            engine_25_dir,
            performance,
        )
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
            message = (
                f"✅ 已保存并应用 {TTS_ENGINE_LABELS[selected]}；"
                f"{recycle_message}。"
            )
            if active:
                message += " 当前任务继续使用原 runtime，设置将在任务结束后生效。"
    return _tts_output_values(message, legacy_dir, engine_25_dir)


def _apply_performance_updates(
    updates: Mapping[str, Any],
    changed_lanes: set[str] | frozenset[str],
) -> tuple[str, str, str, str, str]:
    """Persist only the changed version lane and schedule its safe reload."""
    current = get_tts_engine_settings()
    current_performance = {
        config.TTS2_PERFORMANCE_KEY: current["tts2_performance"],
        config.TTS25_PERFORMANCE_KEY: current["tts25_performance"],
    }
    desired = {
        lane: dict(current_performance[lane])
        for lane in current_performance
    }
    for lane in changed_lanes:
        lane_update = updates.get(lane)
        if isinstance(lane_update, Mapping):
            desired[lane].update(lane_update)
    if not _performance_changed(current_performance, desired):
        return _tts_output_values(
            _performance_status_message(current),
            current["legacy_model_dir"],
            current["indextts25_model_dir"],
        )
    try:
        _persist_with_performance(
            current["engine"],
            current["legacy_model_dir"],
            current["indextts25_model_dir"],
            {lane: updates[lane] for lane in changed_lanes if lane in updates},
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _tts_output_values(
            f"❌ 性能设置保存失败：{html.escape(str(exc))}",
            current["legacy_model_dir"],
            current["indextts25_model_dir"],
        )

    selected_lane = (
        config.TTS25_PERFORMANCE_KEY
        if current["engine"] == TTS_ENGINE_25
        else config.TTS2_PERFORMANCE_KEY
    )
    active = _active_tts_tasks()
    reload_needed = (
        selected_lane in changed_lanes
        and current_performance[selected_lane] != desired[selected_lane]
    )
    try:
        recycle_message = (
            _request_runtime_recycle(current["engine"])
            if reload_needed else "当前 runtime 无需重载"
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _tts_output_values(
            f"⚠ 性能设置已保存，但 runtime recycle 失败：{html.escape(str(exc))}",
            current["legacy_model_dir"],
            current["indextts25_model_dir"],
            active,
        )
    if reload_needed and active:
        message = "✅ 性能设置已保存，将在当前任务结束后生效。"
    elif reload_needed:
        message = f"✅ 性能设置已保存；{recycle_message}。"
    else:
        message = "✅ 性能设置已保存；切换到对应 TTS 版本后生效。"
    return _tts_output_values(
        message,
        current["legacy_model_dir"],
        current["indextts25_model_dir"],
        active,
    )


def apply_tts2_performance_settings(
    tts2_cuda_kernel: bool = True,
    tts2_gpt_accel: bool = False,
    tts2_s2mel_compile: bool = False,
    tts2_conditioning_cache: bool = False,
) -> tuple[str, str, str, str, str]:
    return _apply_performance_updates(
        {
            config.TTS2_PERFORMANCE_KEY: {
                "cuda_kernel": _bool_value(tts2_cuda_kernel, True),
                "gpt_accel": _bool_value(tts2_gpt_accel, False),
                "s2mel_compile": _bool_value(tts2_s2mel_compile, False),
                "conditioning_cache": _bool_value(tts2_conditioning_cache, False),
            },
        },
        {config.TTS2_PERFORMANCE_KEY},
    )


def apply_tts25_performance_settings(
    indextts25_gpt_accel_enabled: bool = True,
) -> tuple[str, str, str, str, str]:
    return _apply_performance_updates(
        {
            config.TTS25_PERFORMANCE_KEY: {
                "gpt_accel": _bool_value(indextts25_gpt_accel_enabled, True),
            },
        },
        {config.TTS25_PERFORMANCE_KEY},
    )


def apply_tts_performance_settings(
    indextts25_gpt_accel_enabled: bool = True,
    tts2_cuda_kernel: bool = True,
    tts2_gpt_accel: bool = False,
    tts2_s2mel_compile: bool = False,
    tts2_conditioning_cache: bool = False,
) -> tuple[str, str, str, str, str]:
    """Compatibility callback for callers that submit both version lanes."""
    return _apply_performance_updates(
        _performance_from_values(
            indextts25_gpt_accel_enabled,
            tts2_cuda_kernel,
            tts2_gpt_accel,
            tts2_s2mel_compile,
            tts2_conditioning_cache,
        ),
        {config.TTS2_PERFORMANCE_KEY, config.TTS25_PERFORMANCE_KEY},
    )


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
