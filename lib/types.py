"""数据结构定义"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Segment:
    id: str                # "1-001"
    role: str              # "旁白"
    emotion: str           # "neutral"
    text: str
    emo_alpha: float = 1.0 # 情绪强度 0.0~1.0
    speech_rate: float = 1.0
    pinyin_hints: dict = field(default_factory=dict)
    pitch: float = 0.0
    breath: str = "none"
    pause_before: int = 0
    pause_after: int = 0
    pauses: list[dict] = field(default_factory=list)
    # Phase-2 stable Character Roster key.  Appended with a default so older
    # callers constructing Segment positionally remain compatible.
    role_id: str | None = None


@dataclass
class Chapter:
    id: int
    title: str
    segments: list[Segment]


@dataclass
class VoiceInfo:
    name: str              # "旁白"
    description: str       # "沉稳男中音，偏纪录片风格"
    suggested_audio: str | None = None


@dataclass
class Script:
    meta: dict             # title, author, total_segments...
    voices: dict[str, VoiceInfo]
    chapters: list[Chapter]
    # The import/diagnostics path keeps the original object so that validation
    # can report JSON paths without creating a second schema parser.
    raw: object = field(default=None, repr=False, compare=False)


@dataclass
class ProjectMeta:
    project_name: str
    created_at: str
    updated_at: str
    total_chapters: int = 0
    total_segments: int = 0
    completed_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    segments_status: dict[str, str] = field(default_factory=dict)
    # v2 起 voice_bindings.json 已固定为根/配置目录名；该字段保留为历史兼容值，
    # v3 项目绑定文件一律经 lib.project_paths 解析，不再依赖本字段。
    voice_bindings_path: str = "voice_bindings.json"
    # Storage metadata was added in V3.3.3.  Defaults keep old project.json
    # files loadable without a migration step.  v3 项目为 3；打开项目不自动迁移，
    # 版本判定唯一入口为 lib.project_paths.detect_storage_version。
    storage_version: int = 1
    directories: dict[str, str] = field(default_factory=dict)
    source_file: str = ""


@dataclass(frozen=True)
class ProjectSummary:
    """统一轻量项目摘要（一次解析，首页书架 / Dropdown / 搜索共用）。

    title/author 来自 ``structured_script.json`` 的 ``meta``（坏文件回退
    project_name / "未填写"）；``failed/status/progress`` 为书架展示派生字段，
    全部来自同一次 ``project.json`` 解析，不引入额外磁盘读。
    """

    project_name: str          # 目录名（scan 名称）
    title: str                 # structured_script.json meta.title，缺省回退 project_name
    author: str                # structured_script.json meta.author，缺省 "未填写"
    chapters: int              # project.json total_chapters
    segments: int              # project.json total_segments
    completed: int             # project.json completed_count
    modified_at: str | None    # 目录最近修改时间（ISO 8601，本地时间秒级）
    # 展示派生字段（同一解析内算好，避免二次解析；沿用 _project_status 语义）
    failed: int = 0
    status: str = "⚪未开始"    # ✅完成 / 🟢进行中 / 🟡部分 / ⚪未开始 / 🔴有失败
    progress: float = 0.0
