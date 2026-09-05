"""运行时配置：数据目录（项目 / 产物）外置，默认不在程序目录内。

设计目标（用户需求）：有声书的项目与合成产物不应和有声书程序 / 文档混在一起，
默认存放在用户主目录下的 AudiobookStudio/，并允许用户在 UI 中自选保存位置。

解析优先级：
  1. 环境变量 AUDIOBOOK_STUDIO_DATA_DIR（最高优先，测试隔离用）
  2. config.json 的 data_dir 字段（用户 UI 设置的持久化值）
  3. 默认 ~/AudiobookStudio

兼容：旧版本把项目放在程序目录的 workspace/projects/。通过 legacy 目录
（默认 <程序>/workspace/projects，或环境变量 AUDIOBOOK_STUDIO_LEGACY_DIR）
继续可打开历史项目，无需手动迁移。

阶段四重构：所有磁盘操作降级为委托 ``repositories.ConfigRepository``，
公有函数签名不变（向后兼容）。
"""
from __future__ import annotations
import json
import logging
import os
import shutil
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass

from repositories.config_repo import ConfigRepository

logger = logging.getLogger(__name__)

PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # audiobook-studio
CONFIG_PATH = os.path.join(PROGRAM_DIR, "config.json")

# 环境变量优先级最高（测试隔离 / 多机部署均可借环境变量覆盖）。
ENV_DATA_DIR = "AUDIOBOOK_STUDIO_DATA_DIR"
ENV_LEGACY_DIR = "AUDIOBOOK_STUDIO_LEGACY_DIR"
ENV_MODEL_DIR = "AUDIOBOOK_STUDIO_MODEL_DIR"
ENV_PYTHON = "AUDIOBOOK_STUDIO_PYTHON"
ENV_FFMPEG = "AUDIOBOOK_STUDIO_FFMPEG"
ENV_TTS_BACKEND = "AUDIOBOOK_STUDIO_TTS_BACKEND"
ENV_TTS_VERSION = "AUDIOBOOK_STUDIO_TTS_VERSION"
INDEXTTS25_GPT_ACCEL_CONFIG_KEY = "indextts25_gpt_accel_enabled"
TTS_PERFORMANCE_CONFIG_KEY = "tts_performance"
TTS2_PERFORMANCE_KEY = "tts2"
TTS25_PERFORMANCE_KEY = "tts25"

# Keep the two lanes separate: a v2 change must never silently rewrite the
# v2.5 policy.  CUDA Kernel is an automatic TTS2 optimization, not a user
# policy switch.
TTS_PERFORMANCE_DEFAULTS = {
    TTS2_PERFORMANCE_KEY: {
        "cuda_kernel": True,
        "gpt_accel": False,
        "s2mel_compile": False,
        "conditioning_cache": False,
    },
    TTS25_PERFORMANCE_KEY: {
        "gpt_accel": False,
    },
}

_DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), "AudiobookStudio")

# 模型目录自动探测的默认候选（相对程序目录的兄弟目录），缺省回退。
_DEFAULT_MODEL_DIR = os.path.normpath(
    os.path.join(PROGRAM_DIR, "..", "index-tts", "checkpoints")
)


def _read_config() -> dict:
    """读取 config.json，返回 dict（降级为调 ConfigRepository）。

    保留既有 `except Exception: pass` 行为（既有代码，非新引入）。
    """
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("读取配置文件失败: %s", exc)
    return {}


def get_int(key: str, default: int = 0) -> int:
    """读取配置中的整数项，缺省返回 default。

    用于 ``tts_engine`` 的 ``embedding_cache_max`` 等有界缓存上限配置；
    解析失败（缺键 / 非整数）时安全回退到 default。
    """
    try:
        return int(_read_config().get(key, default))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    """读取配置中的布尔项，缺省或格式错误时安全回退到 default。"""
    data = _read_config()
    value = data.get(key, default) if isinstance(data, dict) else default
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


def _bool_value(value, default: bool) -> bool:
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


def _performance_lane(version: str) -> str:
    raw = str(version or "").strip().lower().replace("_", ".").replace("-", ".")
    return TTS25_PERFORMANCE_KEY if raw in {"25", "2.5", "v25", "v2.5", "tts25"} else TTS2_PERFORMANCE_KEY


def get_tts_performance(version: str | None = None, *, data: Mapping | None = None) -> dict:
    """Return normalized, version-isolated TTS performance settings.

    ``data`` is injectable for UI/tests; omitting it reads the existing
    ``config.json`` through the normal config facade.  The old flat v2.5 key
    is read only as a migration fallback for the preceding release.
    """
    source = data if isinstance(data, Mapping) else _read_config()
    stored = source.get(TTS_PERFORMANCE_CONFIG_KEY)
    result = deepcopy(TTS_PERFORMANCE_DEFAULTS)
    for lane, defaults in TTS_PERFORMANCE_DEFAULTS.items():
        lane_data = stored.get(lane) if isinstance(stored, Mapping) else None
        for field, default in defaults.items():
            if lane == TTS2_PERFORMANCE_KEY and field == "cuda_kernel":
                # Settings/default profiles always request the automatic CUDA
                # path. Explicit task snapshots are normalized separately.
                result[lane][field] = True
                continue
            if isinstance(lane_data, Mapping) and field in lane_data:
                result[lane][field] = _bool_value(lane_data[field], default)
            elif (
                lane == TTS25_PERFORMANCE_KEY
                and field == "gpt_accel"
                and INDEXTTS25_GPT_ACCEL_CONFIG_KEY in source
            ):
                result[lane][field] = _bool_value(
                    source[INDEXTTS25_GPT_ACCEL_CONFIG_KEY], default
                )
            elif (
                data is None
                and lane == TTS25_PERFORMANCE_KEY
                and field == "gpt_accel"
            ):
                # Keep the old get_bool seam usable by existing callers/tests
                # while the nested schema remains the source of truth.
                result[lane][field] = get_bool(
                    INDEXTTS25_GPT_ACCEL_CONFIG_KEY, default
                )
    if version is None:
        return result
    return dict(result[_performance_lane(version)])


def normalize_tts_performance(
    version: str,
    value: Mapping | None,
    *,
    data: Mapping | None = None,
) -> dict:
    """Normalize one explicit profile lane over its current/default values."""
    normalized = get_tts_performance(version, data=data)
    if not isinstance(value, Mapping):
        return normalized
    for field, default in normalized.items():
        if field in value:
            normalized[field] = _bool_value(value[field], default)
    return normalized


def merge_tts_performance(data: Mapping | None, updates: Mapping | None) -> dict:
    """Merge known performance fields without dropping other config keys."""
    merged = deepcopy(dict(data)) if isinstance(data, Mapping) else {}
    current = get_tts_performance(data=merged)
    stored = merged.get(TTS_PERFORMANCE_CONFIG_KEY)
    container = deepcopy(dict(stored)) if isinstance(stored, Mapping) else {}
    updates = updates if isinstance(updates, Mapping) else {}
    for lane, defaults in TTS_PERFORMANCE_DEFAULTS.items():
        lane_data = container.get(lane)
        lane_data = deepcopy(dict(lane_data)) if isinstance(lane_data, Mapping) else {}
        for field, default in defaults.items():
            lane_data.setdefault(field, current[lane].get(field, default))
        lane_update = updates.get(lane)
        if isinstance(lane_update, Mapping):
            for field, default in defaults.items():
                if field in lane_update:
                    lane_data[field] = _bool_value(lane_update[field], default)
        if lane == TTS2_PERFORMANCE_KEY:
            lane_data["cuda_kernel"] = True
        container[lane] = lane_data
    merged[TTS_PERFORMANCE_CONFIG_KEY] = container
    # Compatibility for the immediately preceding local setting.  New reads
    # prefer the nested lane, so this is an alias rather than shared state.
    merged[INDEXTTS25_GPT_ACCEL_CONFIG_KEY] = bool(
        container[TTS25_PERFORMANCE_KEY]["gpt_accel"]
    )
    return merged


def _data_dir_path() -> str:
    """返回数据根目录路径（不创建目录），供各子目录在「运行时」再按需 makedirs。

    拆分为纯路径解析，避免在模块导入期就创建目录（导入 app 不应在用户主目录落下
    空文件夹；测试也可借环境变量把数据重定向到临时区）。
    """
    d = os.environ.get(ENV_DATA_DIR)
    if not d:
        d = _read_config().get("data_dir") or _DEFAULT_DATA_DIR
    return d


def get_data_dir() -> str:
    """返回数据根目录（所有运行时产物所在地），不存在则创建。"""
    d = _data_dir_path()
    os.makedirs(d, exist_ok=True)
    return d


def get_legacy_dir() -> str:
    """返回旧版项目目录（程序目录内 workspace/projects），用于向后兼容打开。"""
    d = os.environ.get(ENV_LEGACY_DIR)
    if not d:
        d = os.path.join(PROGRAM_DIR, "workspace", "projects")
    return d


def set_data_dir(d: str) -> str:
    """设置并持久化数据目录，返回规范化后的绝对路径。

    阶段四：内部实现改为委托 ``ConfigRepository.set_data_dir()``。
    """
    return ConfigRepository.set_data_dir(d)


def get_projects_root() -> str:
    # 纯路径，不在此创建目录（create_project 会在建项目时 makedirs）。
    return os.path.join(_data_dir_path(), "projects")


def get_voice_library() -> str:
    # 纯路径；写入前由 save_to_lib / _lib_voices 负责 makedirs。
    return os.path.join(_data_dir_path(), "voice_library")


def get_preview_dir() -> str:
    p = os.path.join(get_data_dir(), "preview")
    os.makedirs(p, exist_ok=True)
    return p


def get_test_output_dir() -> str:
    p = os.path.join(get_data_dir(), "test_output")
    os.makedirs(p, exist_ok=True)
    return p


def migrate_legacy_voice_library() -> None:
    """一次性迁移：把程序目录内旧的 voice_library 克隆音色拷贝进外置数据目录。

    仅当新目录不存在同名文件时拷贝，绝不删除旧文件（安全可重入）。避免既有克隆音色
    因「数据目录外置」而突然在 UI 音色库中“消失”。在 app 启动时调用一次即可。
    """
    legacy = os.path.join(PROGRAM_DIR, "voice_library")
    if not os.path.isdir(legacy):
        return
    new = get_voice_library()
    if os.path.abspath(legacy) == os.path.abspath(new):
        return
    os.makedirs(new, exist_ok=True)
    for name in os.listdir(legacy):
        src = os.path.join(legacy, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(new, name)
        if not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except OSError as exc:
                logger.debug("迁移旧音色失败: %s", exc)


def _read_config_key(key: str, default=None):
    """读取 config.json 的单个键（缺省返回 default）。"""
    return _read_config().get(key, default)


def get_model_dir() -> str:
    """返回 IndexTTS2 模型目录（解析优先级：环境变量 > config.json > 自动探测默认）。

    不强制要求目录已存在：调用方（``tts_engine.init_engine``）在真正加载前校验存在性
    并给出可操作提示，避免模型缺失时崩溃。
    """
    d = os.environ.get(ENV_MODEL_DIR)
    if not d:
        d = _read_config_key("model_dir")
    if not d:
        d = _DEFAULT_MODEL_DIR
    return os.path.abspath(d)


def get_tts_profile(overrides=None) -> dict:
    """Resolve the selected runtime engine without loading torch or TTS."""
    from .tts_profile import resolve_profile

    return resolve_profile(overrides)


def get_public_tts_profile(overrides=None) -> dict:
    """Return path-free engine identity fields for UI/status/task payloads."""
    from .tts_profile import public_profile

    return public_profile(get_tts_profile(overrides))


def get_python_path() -> str:
    """返回共享解析器选择的 Python；保留 ``python`` 作为缺失时兼容回退。"""
    from .environment import resolve_python_interpreter
    return resolve_python_interpreter().executable or "python"


def get_ffmpeg_path() -> str:
    """返回 ffmpeg 可执行文件路径（环境变量 > PATH 探测 > 默认 ``ffmpeg``）。

    导出 mp3 / m4b 需要 ffmpeg；缺省返回 ``ffmpeg`` 让 subprocess 在 PATH 中查找，
    找不到时由 ``audio_pipeline`` 抛出 ``ExportError``（明确失败，不再静默回退 WAV）。
    """
    env = os.environ.get(ENV_FFMPEG)
    if env:
        return env
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


@dataclass(frozen=True)
class WorkspacePaths:
    """运行时工作区路径集合（数据目录动态解析的单一真相源）。

    所有路径均在调用 ``get_workspace_paths()`` 时据 ``config.get_data_dir()`` 即时解析，
    因此 ``config.set_data_dir()`` 成功后，后续调用天然使用新路径，无需重启程序。

    Attributes:
        data_dir: 数据根目录（项目 / 产物 / 预览 / 缓存统一所在地）。
        projects_dir: 项目目录（``<data_dir>/projects``）。
        voice_library_dir: 用户音色库（``<data_dir>/voice_library``）。
        preview_dir: 预览目录（``<data_dir>/preview``）。
        task_cache_dir: 任务缓存目录（``<preview_dir>/supplement_tasks``，补录任务隔离）。
    """

    data_dir: str
    projects_dir: str
    voice_library_dir: str
    preview_dir: str
    task_cache_dir: str


def get_workspace_paths() -> WorkspacePaths:
    """据当前数据目录动态解析出全部工作区子目录（frozen，调用期即时计算）。"""
    data_dir = get_data_dir()
    preview_dir = get_preview_dir()
    return WorkspacePaths(
        data_dir=data_dir,
        projects_dir=get_projects_root(),
        voice_library_dir=get_voice_library(),
        preview_dir=preview_dir,
        task_cache_dir=os.path.join(preview_dir, "supplement_tasks"),
    )
