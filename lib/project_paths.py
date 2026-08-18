"""Project-local storage layout with legacy path compatibility.

v3 layout (Storage Layout v3):

    <project root>/
    ├── 01_原始资料/
    │   ├── 书稿/                  # source_book（原 v2 02_原始文件 + 03_章节文本）
    │   └── 项目音色/              # project_voices（用户音色拷贝资产）
    ├── 02_生成音频/
    │   ├── 分段音频/              # segments
    │   ├── 章节音频/              # chapter_audio
    │   ├── 合并音频/              # merged_audio
    │   └── 补录音频/              # supplement_audio
    ├── 03_导出成品/
    │   ├── 正式导出/              # delivery_official
    │   └── 补录/                  # delivery_supplement
    └── 99_系统数据/
        ├── 配置/                  # config（全部系统 JSON）
        ├── 章节数据/              # chapter_data
        ├── 质检/                  # quality
        ├── 任务/                  # tasks
        ├── 缓存/                  # cache
        ├── 日志/                  # logs
        └── 临时/                  # temp

Backward compatibility:
  - v2 canonical layout (01_项目配置 … 09_导出文件 + cache/ logs/);
  - v1 legacy English layout (voices/ segments/ chapters/ output/ cache/ logs/).

``detect_storage_version`` is the single version-decision entry point; business
modules must resolve directories/files through ``project_dir`` / ``project_file``
and must never guess a project path with ``os.path.join(project_dir, "exports")``
style hard coding.
"""
from __future__ import annotations

import json
import os
from typing import Final


STORAGE_VERSION: Final[int] = 3

# ── v3 一级/二级目录（用户可见，稳定不可变）──
V3_DIRS: Final[dict[str, str]] = {
    # 一级
    "source_root": "01_原始资料",
    "generated_root": "02_生成音频",
    "delivery_root": "03_导出成品",
    "system_root": "99_系统数据",
    # 01_原始资料
    "source_book": "01_原始资料/书稿",
    "project_voices": "01_原始资料/项目音色",
    # 02_生成音频
    "segments": "02_生成音频/分段音频",
    "chapter_audio": "02_生成音频/章节音频",
    "merged_audio": "02_生成音频/合并音频",
    "supplement_audio": "02_生成音频/补录音频",
    # 03_导出成品
    "delivery_official": "03_导出成品/正式导出",
    "delivery_supplement": "03_导出成品/补录",
    # 99_系统数据
    "config": "99_系统数据/配置",
    "chapter_data": "99_系统数据/章节数据",
    "quality": "99_系统数据/质检",
    "tasks": "99_系统数据/任务",
    "cache": "99_系统数据/缓存",
    "logs": "99_系统数据/日志",
    "temp": "99_系统数据/临时",
    "migration_preserved": "99_系统数据/迁移保留",
}

# ── v2 canonical（backward read：storage_version == 2）──
V2_DIRS: Final[dict[str, str]] = {
    "config": "01_项目配置",
    "source": "02_原始文件",
    "chapter_text": "03_章节文本",
    "voices": "04_角色与声音",
    "segments": "05_分段音频",
    "chapter_audio": "06_章节音频",
    "merged_audio": "07_合并音频",
    "quality": "08_质检记录",
    "exports": "09_导出文件",
    "cache": "cache",
    "logs": "logs",
}

# ── v1 legacy（backward read：no manifest / 英文布局）──
V1_DIRS: Final[dict[str, str]] = {
    "source_root": "",
    "source_book": "",
    "project_voices": "voices",
    "segments": "segments",
    "chapter_audio": "",
    "merged_audio": "",
    "supplement_audio": "",
    "delivery_official": "output",
    "delivery_supplement": "output",
    "config": "",
    "chapter_data": "chapters",
    "quality": "08_质检记录",
    "tasks": "",
    "cache": "cache",
    "logs": "logs",
    "temp": "",
}

# ── v3 文件级路径（系统 JSON 归属）──
V3_FILES: Final[dict[str, str]] = {
    "project_meta": "99_系统数据/配置/project.json",
    "structured_script": "99_系统数据/配置/structured_script.json",
    "voice_bindings": "99_系统数据/配置/voice_bindings.json",
    "character_roster": "99_系统数据/配置/character_roster.json",
    "voice_cast": "99_系统数据/配置/voice_cast.json",
    "synthesis_overrides": "99_系统数据/配置/synthesis_overrides.json",
    "synthesis_selections": "99_系统数据/配置/synthesis_selections.json",
    "quality_state": "99_系统数据/质检/quality_state.json",
    "task_db": "99_系统数据/任务/production_tasks.sqlite3",
    "segment_status_journal": "99_系统数据/配置/segment_status.journal.jsonl",
}

# ── v2 文件级 backward 表 ──
V2_FILES: Final[dict[str, str]] = {
    "project_meta": "project.json",
    "structured_script": "structured_script.json",
    "voice_bindings": "voice_bindings.json",
    "character_roster": "character_roster.json",
    "voice_cast": "voice_cast.json",
    "synthesis_overrides": "synthesis_overrides.json",
    "synthesis_selections": "synthesis_selections.json",
    "quality_state": "08_质检记录/quality_state.json",
    "task_db": "01_项目配置/production_tasks.sqlite3",
    "segment_status_journal": "01_项目配置/segment_status.journal.jsonl",
}

# ── v1 文件级 backward 表 ──
# 历史 v1 项目由旧代码把 config/quality 解析到中文 canonical 目录，保持现状。
V1_FILES: Final[dict[str, str]] = {
    "project_meta": "project.json",
    "structured_script": "structured_script.json",
    "voice_bindings": "voice_bindings.json",
    "character_roster": "character_roster.json",
    "voice_cast": "voice_cast.json",
    "synthesis_overrides": "synthesis_overrides.json",
    "synthesis_selections": "synthesis_selections.json",
    "quality_state": "08_质检记录/quality_state.json",
    "task_db": "01_项目配置/production_tasks.sqlite3",
    "segment_status_journal": "01_项目配置/segment_status.journal.jsonl",
}

# 兼容别名（旧代码 / 旧测试引用）。CANONICAL_DIRS 即 v2 表；LEGACY_DIRS 为 v1
# 英文目录子集（cache/logs 与 canonical 同名，无链接必要）。
CANONICAL_DIRS: Final[dict[str, str]] = V2_DIRS
LEGACY_DIRS: Final[dict[str, str]] = {
    "voices": "voices",
    "segments": "segments",
    "chapter_text": "chapters",
    "exports": "output",
    "cache": "cache",
    "logs": "logs",
}

# v3 项目上的旧 key 别名（旧调用仍可解析到 v3 目录）。
_V2_KEY_TO_V3_KEY: Final[dict[str, str]] = {
    "voices": "project_voices",
    "source": "source_book",
    "chapter_text": "chapter_data",
    "exports": "delivery_official",
}

# v2 项目上 v3 key 的 backward 别名。
_V3_TO_V2_KEY: Final[dict[str, str]] = {
    "source_root": "source",
    "source_book": "source",
    "project_voices": "voices",
    "supplement_audio": "cache",
    "delivery_root": "exports",
    "delivery_official": "exports",
    "delivery_supplement": "exports",
    "system_root": "",
    "config": "config",
    "chapter_data": "chapter_text",
    "quality": "quality",
    "tasks": "config",
    "cache": "cache",
    "logs": "logs",
    "temp": "cache",
}

# 旧相对路径前缀 → v3 相对路径前缀（resolve_relative 兜底映射）。
# 顺序敏感：长前缀优先（例如 production_tasks.sqlite3 例外必须先于 01_项目配置/）。
_LEGACY_TO_V3_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("01_项目配置/production_tasks.sqlite3", "99_系统数据/任务/production_tasks.sqlite3"),
    ("01_项目配置/", "99_系统数据/配置/"),
    ("08_质检记录/", "99_系统数据/质检/"),
    ("05_分段音频/", "02_生成音频/分段音频/"),
    ("04_角色与声音/", "01_原始资料/项目音色/"),
    ("03_章节文本/", "99_系统数据/章节数据/"),
    ("02_原始文件/", "01_原始资料/书稿/"),
    ("exports/", "03_导出成品/正式导出/"),
    ("output/", "03_导出成品/正式导出/"),
    ("segments/", "02_生成音频/分段音频/"),
    ("voices/", "01_原始资料/项目音色/"),
    ("chapters/", "99_系统数据/章节数据/"),
    ("cache/", "99_系统数据/缓存/"),
    ("project.json", "99_系统数据/配置/project.json"),
    ("structured_script.json", "99_系统数据/配置/structured_script.json"),
    ("voice_bindings.json", "99_系统数据/配置/voice_bindings.json"),
    ("character_roster.json", "99_系统数据/配置/character_roster.json"),
    ("voice_cast.json", "99_系统数据/配置/voice_cast.json"),
    ("quality_state.json", "99_系统数据/质检/quality_state.json"),
)

# v3 相对路径前缀 → v2 相对路径前缀（v3 前缀记录出现在旧项目时的反向兜底）。
_V3_TO_V2_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("01_原始资料/书稿/", "02_原始文件/"),
    ("01_原始资料/项目音色/", "04_角色与声音/"),
    ("02_生成音频/分段音频/", "05_分段音频/"),
    ("02_生成音频/章节音频/", "06_章节音频/"),
    ("02_生成音频/合并音频/", "07_合并音频/"),
    ("02_生成音频/补录音频/", "cache/supplement_tasks/"),
    ("03_导出成品/正式导出/", "09_导出文件/exports/"),
    ("03_导出成品/补录/", "09_导出文件/"),
    ("99_系统数据/配置/", "01_项目配置/"),
    ("99_系统数据/章节数据/", "03_章节文本/"),
    ("99_系统数据/质检/", "08_质检记录/"),
    ("99_系统数据/任务/", "01_项目配置/"),
    ("99_系统数据/缓存/", "cache/"),
    ("99_系统数据/日志/", "logs/"),
    ("99_系统数据/临时/", "cache/"),
)

# v3 相对路径前缀 → v1 相对路径前缀。
_V3_TO_V1_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("01_原始资料/书稿/", ""),
    ("01_原始资料/项目音色/", "voices/"),
    ("02_生成音频/分段音频/", "segments/"),
    ("02_生成音频/章节音频/", ""),
    ("02_生成音频/合并音频/", ""),
    ("02_生成音频/补录音频/", ""),
    ("03_导出成品/正式导出/", "output/"),
    ("03_导出成品/补录/", "output/"),
    ("99_系统数据/配置/", ""),
    ("99_系统数据/章节数据/", "chapters/"),
    ("99_系统数据/质检/", "08_质检记录/"),
    ("99_系统数据/任务/", ""),
    ("99_系统数据/缓存/", "cache/"),
    ("99_系统数据/日志/", "logs/"),
    ("99_系统数据/临时/", ""),
)

_V3_TOP_LEVEL_DIRS: Final[tuple[str, ...]] = tuple(
    sorted(
        {name.split("/", 1)[0] for name in V3_DIRS.values() if name},
        key=len,
        reverse=True,
    )
)

_FILE_TABLES: Final[dict[int, dict[str, str]]] = {
    1: V1_FILES,
    2: V2_FILES,
    3: V3_FILES,
}

_DIR_TABLES: Final[dict[int, dict[str, str]]] = {
    1: V1_DIRS,
    2: V2_DIRS,
    3: V3_DIRS,
}


def _manifest(project_dir: str) -> dict:
    """Read the authoritative project.json for version detection.

    v3 项目：authoritative manifest 位于 ``99_系统数据/配置/project.json``，
    **文件位置本身即声明 v3 布局**（即使内容暂时损坏也按 v3 判定，保证损坏
    的 v3 项目仍能被识别、由上层回退占位字段）。v1/v2 项目 manifest 在根目录。
    """
    v3_path = os.path.join(project_dir, "99_系统数据", "配置", "project.json")
    if os.path.isfile(v3_path):
        try:
            with open(v3_path, encoding="utf-8") as file:
                value = json.load(file)
            if isinstance(value, dict):
                value.setdefault("storage_version", 3)
                return value
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        return {"storage_version": 3}
    root_path = os.path.join(project_dir, "project.json")
    try:
        with open(root_path, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def detect_storage_version(project_dir: str) -> int:
    """Return 1 / 2 / 3 for a project directory.

    - ``project.json['storage_version'] >= 3`` → 3
    - ``== 2`` → 2
    - 缺失 / 0 / <2 → 1（legacy 英文布局，或尚未写 manifest 的过渡形态）
    """
    value = _manifest(project_dir).get("storage_version", 0)
    try:
        version = int(value)
    except (TypeError, ValueError):
        version = 0
    if version >= 3:
        return 3
    if version == 2:
        return 2
    return 1


def is_v2_project(project_dir: str) -> bool:
    """Compatibility alias: whether the project opts into a structured layout.

    历史调用方把「v2+」视为 canonical 布局；v3 继承该语义。
    """
    return detect_storage_version(project_dir) >= 2


def _resolve_version(
    project_dir: str,
    prefer_version: int | None,
    prefer_canonical: bool | None,
) -> int:
    """Resolve the effective storage version for path resolution."""
    if prefer_version is not None:
        return int(prefer_version)
    if prefer_canonical is True:
        return 3
    return detect_storage_version(project_dir)


def _join_relative(project_dir: str, relative: str) -> str:
    relative = str(relative or "")
    if not relative:
        return project_dir
    return os.path.join(project_dir, *relative.split("/"))


def directory_map(
    project_dir: str,
    *,
    prefer_version: int | None = None,
    prefer_canonical: bool | None = None,
) -> dict[str, str]:
    """Return ``{logical key: absolute path}``.

    ``prefer_version=None`` resolves by ``detect_storage_version``.  The map
    contains the version-specific keys plus alias keys so both v3 business
    modules and legacy callers resolve correctly on any project version.
    """
    version = _resolve_version(project_dir, prefer_version, prefer_canonical)
    table = _DIR_TABLES.get(version, V3_DIRS)
    result: dict[str, str] = {
        key: _join_relative(project_dir, relative)
        for key, relative in table.items()
    }
    if version >= 3:
        for legacy_key, v3_key in _V2_KEY_TO_V3_KEY.items():
            if legacy_key not in result and v3_key in result:
                result[legacy_key] = result[v3_key]
    elif version == 2:
        for v3_key, version_key in _V3_TO_V2_KEY.items():
            if v3_key in result or not version_key or version_key not in result:
                continue
            if v3_key == "supplement_audio":
                result[v3_key] = os.path.join(result["cache"], "supplement_tasks")
            else:
                result[v3_key] = result[version_key]
    elif version == 1:
        # v1 legacy 英文目录名别名：旧代码用 voices/chapters/output 直呼目录。
        for legacy_key, v3_key in (
            ("voices", "project_voices"),
            ("chapters", "chapter_data"),
            ("output", "delivery_official"),
            ("exports", "delivery_official"),
        ):
            if legacy_key not in result and v3_key in result:
                result[legacy_key] = result[v3_key]
    return result


def project_dir(
    project_dir: str,
    key: str,
    *,
    create: bool = False,
    prefer_version: int | None = None,
    prefer_canonical: bool | None = None,
) -> str:
    """Return one logical project directory, optionally creating it.

    ``key`` must be a known logical directory (v3 key or a legacy alias).
    """
    paths = directory_map(project_dir, prefer_version=prefer_version, prefer_canonical=prefer_canonical)
    if key not in paths:
        raise KeyError(f"未知项目目录类型: {key}")
    path = paths[key]
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def canonical_project_dirs(project_dir: str) -> dict[str, str]:
    """Return v3 layout paths regardless of the current manifest.

    旧签名语义（忽略 manifest 返回 canonical 表）在 v3 下即返回 v3 表。
    """
    return {key: _join_relative(project_dir, relative) for key, relative in V3_DIRS.items()}


def layout_manifest(project_dir: str) -> dict[str, str]:
    """Return the serializable v3 logical-to-relative directory mapping.

    Written into ``project.json['directories']``.  File-level paths are
    guaranteed by the resolver constants and never persisted in this map.
    """
    return dict(V3_DIRS)


def ensure_layout(
    project_dir: str,
    *,
    prefer_canonical: bool | None = None,
    prefer_version: int | None = None,
    compatibility: bool = False,
) -> dict[str, str]:
    """Create the v3 layout (or the requested version layout).

    ``compatibility`` defaults to ``False``: v3 projects never create legacy
    empty directories / junctions (``voices/ segments/ chapters/ output/``).
    For v2 layouts it keeps the old junction behavior when requested.
    """
    version = _resolve_version(project_dir, prefer_version, prefer_canonical)
    os.makedirs(project_dir, exist_ok=True)
    table = _DIR_TABLES.get(version, V3_DIRS)
    for key, relative in table.items():
        # ``migration_preserved`` 是迁移时按需创建的保留目录，不属于新项目基础布局。
        if key == "migration_preserved":
            continue
        if relative:
            os.makedirs(_join_relative(project_dir, relative), exist_ok=True)

    if compatibility and version < 3 and prefer_version is not None:
        for key, legacy_name in LEGACY_DIRS.items():
            canonical_name = V2_DIRS.get(key)
            if not canonical_name:
                continue
            canonical = os.path.join(project_dir, canonical_name)
            legacy = os.path.join(project_dir, legacy_name)
            if os.path.abspath(canonical) == os.path.abspath(legacy) or os.path.lexists(legacy):
                continue
            try:
                os.symlink(os.path.basename(canonical), legacy, target_is_directory=True)
            except OSError:
                os.makedirs(legacy, exist_ok=True)
    return directory_map(project_dir, prefer_version=version)


# ── 文件级 helper ──


def project_file(
    project_dir: str,
    key: str,
    *,
    create: bool = False,
    prefer_version: int | None = None,
    prefer_canonical: bool | None = None,
) -> str:
    """Return the absolute path of one project-local system file.

    ``create=True`` ensures the parent directory exists.  ``key`` must be in
    the version-specific file table (or a legacy alias table).
    """
    version = _resolve_version(project_dir, prefer_version, prefer_canonical)
    table = _FILE_TABLES.get(version, V3_FILES)
    if key not in table:
        raise KeyError(f"未知项目文件类型: {key}")
    path = _join_relative(project_dir, table[key])
    if create:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    return path


def project_meta(project_dir: str, *, create: bool = False) -> str:
    """→ 99_系统数据/配置/project.json（v2/v1 为根 project.json）。"""
    return project_file(project_dir, "project_meta", create=create)


def structured_script(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "structured_script", create=create)


def voice_bindings(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "voice_bindings", create=create)


def character_roster(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "character_roster", create=create)


def voice_cast(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "voice_cast", create=create)


def quality_state(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "quality_state", create=create)


def task_db(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "task_db", create=create)


def segment_status_journal(project_dir: str, *, create: bool = False) -> str:
    return project_file(project_dir, "segment_status_journal", create=create)


# ── relative path resolver ──


def _is_v3_relative(relative: str) -> bool:
    first = str(relative).split("/", 1)[0]
    return first in _V3_TOP_LEVEL_DIRS


def _map_prefix(relative: str, table: tuple[tuple[str, str], ...]) -> str | None:
    for old, new in table:
        if relative == old.rstrip("/") or relative.startswith(old):
            return new + relative[len(old):]
    return None


def _map_legacy_to_v3(relative: str) -> str | None:
    return _map_prefix(relative, _LEGACY_TO_V3_PREFIXES)


def _map_v3_to_version(relative: str, version: int) -> str | None:
    table = _V3_TO_V2_PREFIXES if version == 2 else _V3_TO_V1_PREFIXES
    mapped = _map_prefix(relative, table)
    if mapped is None:
        return None
    return mapped.lstrip("/") or relative


def resolve_relative(project_dir: str, relative_path: str) -> str:
    """Resolve a persisted project-relative path to an absolute path.

    Central legacy-relative resolver: every business module reading a persisted
    ``relative_path`` must call this function.

    - v3 项目：旧前缀（``exports/ output/ segments/ voices/ 05_分段音频/ …``）
      映射到 v3 路径；已是 v3 前缀的直接 join；未知前缀 → ``ValueError``。
    - v2/v1 项目：旧记录本身就是当前布局相对路径，直接 join；极少数 v3 前缀
      记录会反向映射回当前版本。
    - 绝对路径：必须在项目目录内，否则 ``ValueError``。
    - ``..`` 越界 → ``ValueError``。
    """
    raw = str(relative_path or "").strip()
    if not raw:
        raise ValueError("相对路径为空")
    raw = raw.replace("\\", "/")
    project_root = os.path.realpath(project_dir)

    if os.path.isabs(raw):
        absolute = os.path.normpath(raw)
        try:
            inside = os.path.commonpath([os.path.realpath(absolute), project_root]) == project_root
        except ValueError:
            inside = False
        if not inside:
            raise ValueError("相对路径越界（不在项目目录内）")
        return absolute

    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("相对路径越界")
    normalized = "/".join(parts)

    version = detect_storage_version(project_dir)
    if version >= 3:
        mapped = _map_legacy_to_v3(normalized)
        if mapped is not None:
            return _join_relative(project_dir, mapped)
        if _is_v3_relative(normalized):
            return _join_relative(project_dir, normalized)
        raise ValueError(f"未知相对路径前缀: {normalized}")

    if _is_v3_relative(normalized):
        mapped = _map_v3_to_version(normalized, version)
        if mapped is not None:
            return _join_relative(project_dir, mapped)
    return _join_relative(project_dir, normalized)


def make_relative(project_dir: str, path: str) -> str:
    """Convert an absolute path to the current-version relative path.

    v3 项目永远产出 v3 相对路径（业务模块写持久化 relative_path 时使用）。
    路径必须在项目目录内，否则 ``ValueError``。
    """
    project_root = os.path.realpath(project_dir)
    absolute = os.path.realpath(os.path.abspath(path))
    try:
        inside = os.path.commonpath([project_root, absolute]) == project_root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("质量记录只能引用项目目录内的音频")
    return os.path.relpath(absolute, project_root).replace(os.sep, "/")


__all__ = [
    "CANONICAL_DIRS",
    "LEGACY_DIRS",
    "STORAGE_VERSION",
    "V1_DIRS",
    "V1_FILES",
    "V2_DIRS",
    "V2_FILES",
    "V3_DIRS",
    "V3_FILES",
    "canonical_project_dirs",
    "character_roster",
    "detect_storage_version",
    "directory_map",
    "ensure_layout",
    "is_v2_project",
    "layout_manifest",
    "make_relative",
    "project_dir",
    "project_file",
    "project_meta",
    "quality_state",
    "resolve_relative",
    "segment_status_journal",
    "structured_script",
    "task_db",
    "voice_bindings",
    "voice_cast",
]
