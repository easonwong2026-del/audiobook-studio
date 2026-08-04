"""Remote adapters for the strict one-chapter V4 analysis contract."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai.providers import DeepSeekProvider, OpenAIProvider
from domain.v4.acting import (
    ACTING_REQUEST_SCHEMA,
    ACTING_RESPONSE_SCHEMA,
    ActingResponse,
)
from domain.v4.chapter_analysis import (
    CHAPTER_ANALYSIS_REQUEST_SCHEMA,
    CHAPTER_ANALYSIS_RESPONSE_SCHEMA,
    ChapterAnalysisResponse,
)
from services.ai_analysis_settings import CORE_PROMPT_VERSION

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
    "confidence": 0.0,
    "evidence": ["角色归属的原文短证据"],
    "uncertainty_reason": "低置信度时说明原因"
  }}],
  "segments": [{{
    "index": 0,
    "segment_type": "narration|dialogue|inner_monologue|quotation|stage_direction",
    "speaker_id": "narrator、已知 ID、new:角色名 或 null",
    "speaker_name": "只有 speaker_id 为 new: 时填写角色名",
    "text": "原文连续片段，必须保持原字、标点和顺序",
    "emotion": "neutral|calm|happy|sad|angry|fearful|surprised|tense|excited|tender|urgent|cold|confident|hesitant",
    "confidence": 0.0,
    "speaker_evidence": ["支持说话人判断的原文短证据"],
    "uncertainty_reason": "无法确认时说明原因"
  }}]
}}

必须从第一个非空白字符覆盖到最后一个非空白字符；segments 不得重叠、遗漏或新增
原文。旁白和场景说明使用 narrator 或 null；不能确定说话人时使用 null，不能硬猜。
character_updates 只报告当前章节识别到的角色或对已有角色的别名更新；不要创建物品、
地点、动作、情绪、机构或章节标题角色。新角色在 character_updates 中 character_id=null，
并在对应 segment 使用 new:角色名，系统会在本地生成稳定 ID。
confidence 低于确认阈值时仍要返回候选角色及证据；系统会把它保留为“需确认候选”，
不能把低置信度对白改成旁白，也不能为了覆盖率把未知说话人填成 narrator。
"""

_ACTING_SYSTEM_PROMPT = f"""\
你是章节的演绎导演，只负责已经通过本地校验的结构片段的表达参数。
不得改写、删减、合并、重排原文，也不得改变 speaker_id、segment_type 或角色确认状态。
只输出严格 JSON，不要 Markdown、解释或 reasoning_content。
schema_version 必须是 {ACTING_RESPONSE_SCHEMA}，每个 index 必须与请求一一对应。
可以设置 emotion_strength、speed、pitch、intensity、breath、pause_before、pause_after、
performance_note；不要返回任何 source text、坐标或其它字段。
"""


def _json_payload(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RemoteChapterAnalysisAdapter:
    name: str
    model: str

    def __init__(
        self,
        name: str,
        model: str,
        request_json: RequestJson,
        *,
        reasoning_mode: str | None = None,
        auto_upgrade_max: bool = True,
        prompt_supplement: str = "",
    ):
        self.name = name
        self.model = model
        self._request_json = request_json
        self.reasoning_mode = reasoning_mode
        self.auto_upgrade_max = auto_upgrade_max
        self.prompt_supplement = prompt_supplement.strip()
        self.prompt_version = CORE_PROMPT_VERSION

    def analyze_chapter(
        self,
        *,
        chapter_id: str,
        chapter_title: str,
        known_characters: list[dict[str, Any]],
        chapter_text: str,
        previous_response: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        reasoning_mode: str | None = None,
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
        prompt = _SYSTEM_PROMPT
        if self.prompt_supplement:
            prompt += f"\n\n【用户补充】\n{self.prompt_supplement}"
        raw = self._request_json(
            prompt,
            _json_payload(request),
            task="chapter_analysis_repair" if previous_response is not None else "chapter_analysis",
            reasoning_mode=(
                "max"
                if previous_response is not None
                and self.auto_upgrade_max
                else reasoning_mode or self.reasoning_mode
            ),
        )
        return ChapterAnalysisResponse.from_dict(raw)

    def act_chapter(
        self,
        *,
        chapter_id: str,
        segments: list[dict[str, Any]],
        reasoning_mode: str | None = None,
    ) -> ActingResponse:
        raw = self._request_json(
            _ACTING_SYSTEM_PROMPT,
            _json_payload(
                {
                    "schema_version": ACTING_REQUEST_SCHEMA,
                    "chapter_id": chapter_id,
                    "segments": segments,
                }
            ),
            task="chapter_acting",
            reasoning_mode=reasoning_mode or self.reasoning_mode,
        )
        response = ActingResponse.from_dict(raw)
        if response.chapter_id != chapter_id:
            raise ValueError("acting response chapter_id mismatch")
        return response


def create_chapter_analysis_adapter(
    provider_name: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
    timeout: float = 180.0,
    reasoning_mode: str | None = None,
    auto_upgrade_max: bool = True,
    prompt_supplement: str = "",
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
        reasoning_mode=reasoning_mode,
    )
    # Deliberately expose the provider's single request primitive.  Retry
    # policy belongs to ChapterAnalysisService so the UI can show exactly
    # one normal request plus at most one repair request.
    return RemoteChapterAnalysisAdapter(
        provider.name,
        provider.model,
        provider._request_json,
        reasoning_mode=reasoning_mode,
        auto_upgrade_max=auto_upgrade_max,
        prompt_supplement=prompt_supplement,
    )
