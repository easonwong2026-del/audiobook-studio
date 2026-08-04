"""Remote adapters for the strict one-chapter V4 analysis contract."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from domain.v4.chapter_analysis import (
    CHAPTER_ANALYSIS_REQUEST_SCHEMA,
    CHAPTER_ANALYSIS_RESPONSE_SCHEMA,
    ChapterAnalysisResponse,
)

from ai.providers import DeepSeekProvider, OpenAIProvider

RequestJson = Callable[..., dict[str, Any]]

_SYSTEM_PROMPT = f"""\
你是有声书的章节剧本分析器。当前请求只包含一章完整原文；不要推断或拆分全书，
不要索要更多章节，也不要把原文改写。只输出严格 JSON，不要 Markdown、解释、坐标、
delivery、pause 或其它字段。

输出 schema_version 必须是 {CHAPTER_ANALYSIS_RESPONSE_SCHEMA}，格式为：
{{
  "schema_version": "{CHAPTER_ANALYSIS_RESPONSE_SCHEMA}",
  "chapter_id": "原样复制请求中的 chapter_id",
  "character_updates": [{{
    "character_id": "已知角色的稳定 ID；新角色先填 null",
    "canonical_name": "角色名",
    "aliases": [],
    "is_new": false,
    "confidence": 0.0
  }}],
  "segments": [{{
    "index": 0,
    "segment_type": "narration|dialogue|inner_monologue|quotation|stage_direction",
    "speaker_id": "narrator、已知 ID、new:角色名 或 null",
    "speaker_name": "只有 speaker_id 为 new: 时填写角色名",
    "text": "原文连续片段，必须保持原字、标点和顺序",
    "emotion": "neutral|calm|happy|sad|angry|fearful|surprised|tense|excited|tender|urgent|cold|confident|hesitant",
    "confidence": 0.0
  }}]
}}

必须从第一个非空白字符覆盖到最后一个非空白字符；segments 不得重叠、遗漏或新增
原文。旁白和场景说明使用 narrator 或 null；不能确定说话人时使用 null，不能硬猜。
character_updates 只报告当前章节识别到的角色或对已有角色的别名更新；不要创建物品、
地点、动作、情绪、机构或章节标题角色。新角色在 character_updates 中 character_id=null，
并在对应 segment 使用 new:角色名，系统会在本地生成稳定 ID。
"""


def _json_payload(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RemoteChapterAnalysisAdapter:
    name: str
    model: str

    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def analyze_chapter(
        self,
        *,
        chapter_id: str,
        chapter_title: str,
        known_characters: list[dict[str, Any]],
        chapter_text: str,
        previous_response: dict[str, Any] | None = None,
        errors: list[str] | None = None,
    ) -> ChapterAnalysisResponse:
        request: dict[str, Any] = {
            "schema_version": CHAPTER_ANALYSIS_REQUEST_SCHEMA,
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "known_characters": known_characters,
            "chapter_text": chapter_text,
        }
        if previous_response is not None:
            request["repair"] = {
                "first_output": previous_response,
                "errors": [str(item)[:240] for item in (errors or [])[:8]],
                "instruction": "只修复这些结构、speaker_id、顺序或覆盖错误，重新输出完整本章结果。",
            }
        raw = self._request_json(
            _SYSTEM_PROMPT,
            _json_payload(request),
            task="chapter_analysis",
        )
        return ChapterAnalysisResponse.from_dict(raw)


def create_chapter_analysis_adapter(
    provider_name: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
    timeout: float = 180.0,
) -> RemoteChapterAnalysisAdapter:
    normalized = provider_name.strip().lower()
    provider_type = {"deepseek": DeepSeekProvider, "openai": OpenAIProvider}.get(normalized)
    if provider_type is None:
        raise ValueError(f"unsupported chapter analysis provider: {provider_name}")
    provider = provider_type(
        api_key=api_key,
        model=model or None,
        base_url=base_url or None,
        timeout=timeout,
    )
    # Deliberately expose the provider's single request primitive.  Retry
    # policy belongs to ChapterAnalysisService so the UI can show exactly
    # one normal request plus at most one repair request.
    return RemoteChapterAnalysisAdapter(provider.name, provider.model, provider._request_json)
