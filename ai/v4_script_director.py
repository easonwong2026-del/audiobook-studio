"""Remote adapters for the V4 AI-first book reader and script director.

The adapters reuse the existing OpenAI-compatible transport and strict JSON
parsing, but use V4-specific source coordinates and stable speaker IDs.  No
adapter logs the source or the model response.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai.providers import DeepSeekProvider, OpenAIProvider
from ai.providers.exceptions import ProviderOutputTruncatedError
from domain.v4.ai_first import (
    CHARACTER_BIBLE_CHAPTER_SCHEMA,
    SCRIPT_DIRECTOR_SCHEMA,
    SCRIPT_REVIEW_SCHEMA,
    CharacterBibleDocument,
    ScriptDirectorBatch,
    ScriptReviewResponse,
)

RequestJson = Callable[..., dict[str, Any]]

_BIBLE_SYSTEM_PROMPT = f"""\
你是有声书项目的全书人物导演。你正在顺序阅读一个章节原文，并维护一份
可修正的人物圣经。AI 是唯一的语义判断者：只保留真实人物或长期稳定的拟人化角色，
不要把动作、语气、情绪、物品、地点、机构、章节名、字段名、代词、书名、术语或
临时描述当人物。后续章节可以推翻前面判断。每个角色必须引用当前原文中的准确证据。
只输出严格 JSON，不要 Markdown、解释或原文之外的内容。

输出 schema_version 必须是 {CHARACTER_BIBLE_CHAPTER_SCHEMA}，字段为：
{{
  "schema_version": "{CHARACTER_BIBLE_CHAPTER_SCHEMA}",
  "source_sha256": "请求中的 source_sha256",
  "chapter_id": "请求中的 chapter_id",
  "characters": [{{
    "character_id": "稳定 ID；优先复用 memory 中的 ID",
    "canonical_name": "本名或最稳定称呼",
    "aliases": [],
    "description": "身份和长期特征",
    "importance": "major 或 minor 或 unknown",
    "relationships": [{{"character_id":"已知 ID","relation":"关系"}}],
    "first_appearance_chapter": "chapter_id",
    "evidence": [{{"chapter_id":"当前章节","text":"原文中的准确片段","source_start":0,"source_end":1}}],
    "confidence": 0.0,
    "speaker_id": "与 character_id 稳定对应的 speaker_id"
  }}],
  "uncertain_entities": []
}}
characters 必须是截至当前章节的完整人物记忆，而不是只列新增人物。
"""

_SCRIPT_SYSTEM_PROMPT = f"""\
你是 AI 剧本导演。人物圣经是唯一允许使用的机器人物来源；不要从原文临时造角色。
直接阅读给定原文批次，同时判断旁白、对白、内心独白、引用、场景说明和演绎参数。
segment 必须保持原文顺序，不重叠，不遗漏非空白内容，不改写字符；坐标是 source 中的
绝对 Python 切片坐标，segment.text 必须严格等于 source[source_start:source_end]。
说话人只能是 narrator、允许的 speaker_id 或 null；不确定时返回 null，不要强行归属。
只输出严格 JSON，不要 Markdown 或解释。

schema_version 必须是 {SCRIPT_DIRECTOR_SCHEMA}，输出字段：
{{"schema_version":"{SCRIPT_DIRECTOR_SCHEMA}","chapter_id":"原样复制",
"source_start":0,"source_end":1,"segments":[{{
"source_start":0,"source_end":1,"segment_type":"narration|dialogue|inner_monologue|quotation|stage_direction",
"speaker_id":"narrator/允许的 ID/null","text":"精确原文","confidence":0.0,
"emotion":"neutral","emotion_strength":0.4,"delivery":{{}},
"pause_before":0,"pause_after":600,"pauses":[]}}]}}
"""

_REVIEW_SYSTEM_PROMPT = f"""\
你是有声书全书审稿导演。复查给定章节的 AI 剧本与人物圣经，找出虚假人物、错误合并、
连续对白无理由跳人、首次出场前说话、内心独白/引用误判、旁白误判和别名问题。
只返回安全的最小修正补丁；不要改写原文，也不要覆盖人工锁定或人工指派的结果。
不确定就使用 unresolve。speaker_id 只能来自允许列表。只输出严格 JSON，不要解释。

schema_version 必须是 {SCRIPT_REVIEW_SCHEMA}，格式：
{{"schema_version":"{SCRIPT_REVIEW_SCHEMA}","patches":[{{
"segment_id":"已有 segment id","action":"reassign|unresolve|reclassify",
"speaker_id":"允许 ID/null","segment_type":"允许类型/null",
"confidence":0.0,"reason":"简短原因"}}]}}
"""


def _json_payload(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RemoteBookUnderstandingAdapter:
    name: str
    model: str

    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def read_chapter(
        self,
        *,
        source_sha256: str,
        chapter_id: str,
        chapter_title: str,
        source_start: int,
        source_end: int,
        text: str,
        memory: dict[str, Any],
    ) -> CharacterBibleDocument:
        raw = self._request_json(
            _BIBLE_SYSTEM_PROMPT,
            _json_payload({
                "schema_version": "character-bible-chapter-request-v1",
                "source_sha256": source_sha256,
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "source_start": source_start,
                "source_end": source_end,
                "memory": memory,
                "chapter_text": text,
            }),
            task="character_bible",
        )
        return CharacterBibleDocument.from_dict(raw)

    def finalize(
        self, *, source_sha256: str, memory: dict[str, Any]
    ) -> CharacterBibleDocument:
        raw = self._request_json(
            _BIBLE_SYSTEM_PROMPT,
            _json_payload({
                "schema_version": "character-bible-final-request-v1",
                "source_sha256": source_sha256,
                "memory": memory,
                "instruction": "执行全书最终整理，保留 uncertain_entities 并修正别名和关系。",
            }),
            task="character_bible",
        )
        return CharacterBibleDocument.from_dict(raw)


class RemoteScriptDirectorV4Adapter:
    name: str
    model: str

    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def analyze_batch(
        self,
        *,
        source_sha256: str,
        chapter_id: str,
        source_start: int,
        source_end: int,
        text: str,
        bible: dict[str, Any],
        context_before: str = "",
    ) -> ScriptDirectorBatch:
        allowed = [
            {
                "speaker_id": item.get("speaker_id"),
                "name": item.get("canonical_name"),
                "aliases": item.get("aliases", []),
            }
            for item in bible.get("characters", [])
            if isinstance(item, dict) and item.get("speaker_id")
        ]
        raw = self._request_json(
            _SCRIPT_SYSTEM_PROMPT,
            _json_payload({
                "schema_version": "ai-script-director-v4-request-v1",
                "source_sha256": source_sha256,
                "chapter_id": chapter_id,
                "source_start": source_start,
                "source_end": source_end,
                "allowed_speakers": [{"speaker_id": "narrator", "name": "旁白"}, *allowed],
                "context_before": context_before,
                "source_text": text,
            }),
            task="script_director",
        )
        return ScriptDirectorBatch.from_dict(raw)


class RemoteScriptReviewV4Adapter:
    name: str
    model: str

    def __init__(self, name: str, model: str, request_json: RequestJson):
        self.name = name
        self.model = model
        self._request_json = request_json

    def review_chapter(
        self,
        *,
        source_sha256: str,
        chapter_id: str,
        source_text: str,
        script: dict[str, Any],
        bible: dict[str, Any],
        allowed_speakers: list[dict[str, Any]],
    ) -> ScriptReviewResponse:
        raw = self._request_json(
            _REVIEW_SYSTEM_PROMPT,
            _json_payload({
                "schema_version": "ai-script-review-request-v1",
                "source_sha256": source_sha256,
                "chapter_id": chapter_id,
                "source_text": source_text,
                "script": script,
                "character_bible": bible,
                "allowed_speakers": allowed_speakers,
            }),
            task="script_review",
        )
        return ScriptReviewResponse.from_dict(raw)


def _provider_request(provider: Any) -> RequestJson:
    def request(
        system: str,
        user: str,
        *,
        task: str = "script_director",
        reasoning_mode: str | None = None,
    ):
        last_error = None
        for _attempt in range(2):
            try:
                return provider._request_json(
                    system,
                    user,
                    task=task,
                    reasoning_mode=reasoning_mode,
                )
            except ProviderOutputTruncatedError as exc:
                last_error = exc
        raise ProviderOutputTruncatedError(
            "AI 输出截断，重试后仍未完成"
        ) from last_error

    return request


def create_ai_first_adapters(
    provider_name: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
    timeout: float = 180.0,
) -> tuple[
    RemoteBookUnderstandingAdapter,
    RemoteScriptDirectorV4Adapter,
    RemoteScriptReviewV4Adapter,
]:
    normalized = provider_name.strip().lower()
    provider_type = {"deepseek": DeepSeekProvider, "openai": OpenAIProvider}.get(normalized)
    if provider_type is None:
        raise ValueError(f"unsupported AI-first provider: {provider_name}")
    provider = provider_type(
        api_key=api_key,
        model=model or None,
        base_url=base_url or None,
        timeout=timeout,
    )
    request = _provider_request(provider)
    return (
        RemoteBookUnderstandingAdapter(provider.name, provider.model, request),
        RemoteScriptDirectorV4Adapter(provider.name, provider.model, request),
        RemoteScriptReviewV4Adapter(provider.name, provider.model, request),
    )
