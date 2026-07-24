"""数据结构定义"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Segment:
    id: str                # "1-001"
    role: str              # "旁白"
    emotion: str           # "neutral"
    text: str
    emo_alpha: float = 1.0 # 情绪强度 0.0~1.0
    speech_rate: float = 1.0
    pinyin_hints: dict = field(default_factory=dict)


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
    voice_bindings_path: str = "voice_bindings.json"
