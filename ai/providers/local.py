"""可离线运行的本地剧本导演 Provider。

它是第一阶段的确定性基线，不冒充大模型：主要用于本地预览、无 API Key 场景和
回归测试。后续远程 Provider 产物会经过同一套规范化与质量守卫。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .base import ScriptAnalysisProvider

_CHAPTER_RE = re.compile(
    r"^\s*(第[零一二三四五六七八九十百千万两\d]+[章节回卷部篇].*|"
    r"chapter\s+\d+.*)\s*$",
    re.IGNORECASE,
)
_SPEECH_RE = re.compile(
    r"(?P<speaker>[\u4e00-\u9fffA-Za-z0-9·]{1,12}?)"
    r"(?:低声|大声|冷冷地|平静地|突然)?"
    r"(?:说(?:道)?|问(?:道)?|喊(?:道)?|叫(?:道)?|答(?:道)?|回答|喃喃道)"
)
_QUOTE_RE = re.compile(r"[“「『\"](?P<speech>.+?)[”」』\"]", re.DOTALL)
_PURE_QUOTE_RE = re.compile(r"^\s*[“「『\"].+[”」』\"]\s*[。！？!?…]*\s*$", re.DOTALL)
_SPEECH_CUE_RE = re.compile(r"(?:说(?:道)?|问(?:道)?|喊(?:道)?|叫(?:道)?|回答)\s*[：:]$")


def _clean_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def _speaker_from(text: str) -> Optional[str]:
    if re.match(
        r"^\s*[他她它](?:看着|望着|盯着|转向|对着).{0,20}"
        r"(?:说|说道|问|问道|喊|叫|回答)",
        text,
    ):
        # 这是代词指代，不能把“看着”的宾语误判为说话人。
        return None
    matches = list(_SPEECH_RE.finditer(text))
    if not matches:
        return None
    speaker = matches[-1].group("speaker")
    return speaker or None


def _emotion_cue(text: str) -> Optional[str]:
    cues = (
        ("angry", ("愤怒", "怒道", "吼道", "咆哮", "混蛋", "住口")),
        ("confident", ("自信", "不屑", "冷笑", "还不够资格", "不够资格让我害怕")),
        ("fearful", ("害怕", "恐惧", "颤抖", "惊恐")),
        ("sad", ("悲伤", "哽咽", "哭", "眼泪", "绝望")),
        ("happy", ("笑道", "微笑", "开心", "高兴")),
        ("cold", ("冷冷", "冰冷", "淡淡地")),
        ("tense", ("紧张", "急促", "猛地", "突然")),
    )
    for emotion, words in cues:
        if any(word in text for word in words):
            return emotion
    if "？" in text or "?" in text:
        return "questioning"
    if "！" in text or "!" in text:
        return "excited"
    if "……" in text or "..." in text:
        return "hesitant"
    return None


def _delivery(emotion: str, text: str) -> Dict[str, Any]:
    speed_by_emotion = {
        "tense": 1.05,
        "excited": 1.08,
        "angry": 1.08,
        "sad": 0.92,
        "cold": 0.94,
        "confident": 0.95,
        "hesitant": 0.9,
        "fearful": 1.03,
        "questioning": 0.98,
    }
    intensity_by_emotion = {
        "angry": 0.8,
        "excited": 0.7,
        "fearful": 0.7,
        "confident": 0.7,
        "tense": 0.65,
        "sad": 0.6,
        "cold": 0.55,
        "hesitant": 0.45,
        "questioning": 0.5,
        "neutral": 0.4,
    }
    breath = "normal" if len(text) >= 70 else "light"
    if emotion in {"angry", "excited", "fearful"}:
        breath = "heavy"
    return {
        "speed": speed_by_emotion.get(emotion, 1.0),
        "pitch": 0,
        "intensity": intensity_by_emotion.get(emotion, 0.4),
        "breath": breath,
    }


def _pause_markers(text: str) -> List[Dict[str, Any]]:
    """长句只增加内部停顿标记，不为了停顿切碎 segment。"""
    if len(text) < 55:
        return []
    markers: List[Dict[str, Any]] = []
    for match in re.finditer(r"[，；：、]", text):
        if match.start() < 18 or len(text) - match.start() < 18:
            continue
        markers.append({
            "position": match.end(),
            "duration": 420 if match.group() in "，、" else 650,
            "type": "pause_short",
        })
        if len(markers) >= 3:
            break
    return markers


class LocalDirectorProvider(ScriptAnalysisProvider):
    """以规则实现的离线导演基线。"""

    name = "local"

    def extract_characters(self, text: str) -> List[str]:
        seen = {"旁白"}
        ordered = ["旁白"]
        for match in _SPEECH_RE.finditer(text):
            name = _speaker_from(match.group(0))
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    def generate_segments(
        self,
        text: str,
        characters: List[str],
    ) -> List[Dict[str, Any]]:
        chapters = self._chapter_blocks(text)
        all_segments: List[Dict[str, Any]] = []
        for _, blocks in chapters:
            all_segments.extend(self._direct_blocks(blocks))
        return all_segments

    def analyze_script(
        self,
        text: str,
        *,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        clean = _clean_text(text)
        characters = self.extract_characters(clean)
        chapters = []
        for index, (chapter_title, blocks) in enumerate(self._chapter_blocks(clean), 1):
            chapters.append({
                "id": index,
                "title": chapter_title or f"第{index}章",
                "segments": self._direct_blocks(blocks),
            })
        return {
            "version": "3.0",
            "provider": self.name,
            "meta": {"title": title or "未命名作品", "author": author},
            "voices": {
                name: {
                    "description": (
                        "叙事旁白，由用户绑定音色"
                        if name == "旁白"
                        else "故事角色，由用户绑定音色"
                    )
                }
                for name in characters
            },
            "chapters": chapters,
        }

    @staticmethod
    def _chapter_blocks(text: str) -> List[Tuple[str, List[str]]]:
        clean = _clean_text(text)
        result: List[Tuple[str, List[str]]] = []
        current_title = ""
        current_lines: List[str] = []

        def flush() -> None:
            nonlocal current_lines
            blocks = LocalDirectorProvider._merge_lines(current_lines)
            if blocks or not result:
                result.append((current_title, blocks))
            current_lines = []

        for raw_line in clean.split("\n"):
            line = raw_line.strip()
            if _CHAPTER_RE.match(line):
                if current_lines or current_title:
                    flush()
                current_title = line
                continue
            current_lines.append(line)
        flush()
        return result

    @staticmethod
    def _merge_lines(lines: List[str]) -> List[str]:
        """合并说话提示和紧随其后的引语，避免一个讲话动作被拆开。"""
        blocks: List[str] = []
        pending: List[str] = []
        for line in lines:
            if not line:
                if pending:
                    blocks.append("".join(pending).strip())
                    pending = []
                continue
            if pending and _PURE_QUOTE_RE.match(line):
                pending.append(line)
                blocks.append("".join(pending).strip())
                pending = []
                continue
            if pending:
                blocks.append("".join(pending).strip())
                pending = []
            if (
                line.endswith(("：", ":"))
                and (_speaker_from(line) or _SPEECH_CUE_RE.search(line))
            ):
                pending = [line]
            else:
                blocks.append(line)
        if pending:
            blocks.append("".join(pending).strip())
        return [block for block in blocks if block]

    @staticmethod
    def _direct_blocks(blocks: List[str]) -> List[Dict[str, Any]]:
        segments: List[Dict[str, Any]] = []
        last_emotion: Dict[str, str] = {}
        last_dialogue_speaker: Optional[str] = None
        last_named_actor: Optional[str] = None

        for block in blocks:
            explicit_speaker = _speaker_from(block)
            has_quote = bool(_QUOTE_RE.search(block))
            if (
                explicit_speaker is None
                and last_named_actor
                and re.match(r"^\s*[他她它]", block)
                and has_quote
            ):
                speaker = last_named_actor
                last_dialogue_speaker = speaker
            elif explicit_speaker:
                speaker = explicit_speaker
                last_named_actor = speaker
                last_dialogue_speaker = speaker
            elif _PURE_QUOTE_RE.match(block) and last_dialogue_speaker:
                speaker = last_dialogue_speaker
            else:
                speaker = "旁白"

            cue = _emotion_cue(block)
            # 单独的问号、感叹号或省略号不足以推翻已建立的表演状态。
            if (
                speaker in last_emotion
                and cue in {"questioning", "excited", "hesitant"}
            ):
                cue = None
            if cue is None and speaker != "旁白":
                emotion = last_emotion.get(speaker, "neutral")
            else:
                emotion = cue or "neutral"
            last_emotion[speaker] = emotion

            if speaker == "旁白":
                actor_match = re.match(
                    r"^\s*(?P<name>[\u4e00-\u9fffA-Za-z0-9·]{2,12}?)"
                    r"(?:走|看|望|站|坐|起身|转身|抬头|低头|来到|冲|推|拉)",
                    block,
                )
                if actor_match:
                    candidate = actor_match.group("name")
                    if candidate not in {"男人", "女人", "老人", "孩子", "众人"}:
                        last_named_actor = candidate

            segment = {
                "speaker": speaker,
                "text": block,
                "emotion": emotion,
                "emotion_strength": _delivery(emotion, block)["intensity"],
                "delivery": _delivery(emotion, block),
                "pause_before": 300 if has_quote else 0,
                "pause_after": 900 if has_quote else 600,
                "pauses": _pause_markers(block),
            }

            # 同一角色、同一情绪、连续的短对白属于同一自然讲话动作。
            if (
                segments
                and _PURE_QUOTE_RE.match(block)
                and segments[-1]["speaker"] == speaker
                and segments[-1]["emotion"] == emotion
                and len(segments[-1]["text"]) + len(block) <= 240
            ):
                previous = segments[-1]
                join_at = len(previous["text"])
                previous["text"] += block
                previous.setdefault("pauses", []).append({
                    "position": join_at,
                    "duration": 650,
                    "type": "pause_short",
                })
                previous["pause_after"] = segment["pause_after"]
                previous["delivery"]["breath"] = (
                    "normal" if len(previous["text"]) >= 70 else previous["delivery"]["breath"]
                )
                continue
            segments.append(segment)
        return segments
