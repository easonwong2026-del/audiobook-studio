"""角色声音推荐与 AI 导演单段试听。"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from lib import config, directed_synthesis, voice_lib

_TRAITS = {
    "female": ("女", "少女", "姐姐", "妈妈", "母亲", "清亮", "甜", "柔"),
    "male": ("男", "少年", "哥哥", "爸爸", "父亲", "大叔", "低沉", "浑厚"),
    "young": ("儿童", "孩子", "少年", "少女", "年轻", "青春", "活泼"),
    "mature": ("成熟", "中年", "大叔", "沉稳", "稳重", "厚重"),
    "elder": ("老人", "老年", "苍老", "爷爷", "奶奶"),
    "bright": ("清亮", "明亮", "活泼", "阳光", "甜美", "轻快"),
    "deep": ("低沉", "浑厚", "厚重", "磁性", "沉稳"),
    "gentle": ("温柔", "柔和", "亲切", "细腻", "舒缓"),
    "forceful": ("有力", "威严", "霸气", "激昂", "强势"),
    "cold": ("冷", "克制", "疏离", "淡漠"),
    "narration": ("旁白", "播音", "纪录片", "叙事", "讲述"),
}

_EMOTION_TRAITS = {
    "cold": {"cold", "deep"},
    "confident": {"forceful", "deep"},
    "angry": {"forceful"},
    "sad": {"gentle", "deep"},
    "fearful": {"young"},
    "happy": {"bright"},
    "excited": {"bright", "forceful"},
    "tense": {"forceful"},
    "hesitant": {"gentle"},
}


def _trait_set(text: str) -> set[str]:
    lowered = text.casefold()
    return {
        trait
        for trait, words in _TRAITS.items()
        if any(word.casefold() in lowered for word in words)
    }


class VoiceDirectorService:
    """基于角色描述、表演状态和音色资产标签给出可解释推荐。"""

    @staticmethod
    def role_choices(script: dict) -> list[tuple[str, str]]:
        return [
            (
                f"{role} · {str(info.get('description') or '暂无描述')}",
                role,
            )
            for role, info in (script.get("voices") or {}).items()
        ]

    @staticmethod
    def segment_choices(script: dict, role: str = "") -> list[tuple[str, str]]:
        choices = []
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                speaker = str(segment.get("speaker") or segment.get("role") or "旁白")
                if role and speaker != role:
                    continue
                text = str(segment.get("text") or "")
                choices.append((
                    f"{segment.get('id')} · {speaker} · {text[:42]}",
                    str(segment.get("id")),
                ))
        return choices

    @staticmethod
    def recommend(
        script: dict,
        role: str,
        *,
        top_k: int = 5,
        assets: Optional[list[dict]] = None,
    ) -> list[dict]:
        voices = script.get("voices") or {}
        if role not in voices:
            raise ValueError(f"剧本中不存在角色：{role}")
        assets = list(assets) if assets is not None else voice_lib.scan_voice_library()
        if not assets:
            return []

        description = str((voices.get(role) or {}).get("description") or "")
        role_traits = _trait_set(f"{role} {description}")
        if role == "旁白":
            role_traits.add("narration")
        emotions = Counter(
            str(segment.get("emotion") or "neutral")
            for chapter in script.get("chapters", [])
            for segment in chapter.get("segments", [])
            if str(segment.get("speaker") or segment.get("role") or "旁白") == role
        )
        for emotion, _ in emotions.most_common(3):
            role_traits.update(_EMOTION_TRAITS.get(emotion, set()))

        recommendations = []
        for asset in assets:
            label = (
                f"{asset.get('name', '')} {asset.get('category', '')}"
            )
            asset_traits = _trait_set(label)
            matches = sorted(role_traits & asset_traits)
            contradictions = []
            if "female" in role_traits and "male" in asset_traits:
                contradictions.append("角色偏女声但资产标签偏男声")
            if "male" in role_traits and "female" in asset_traits:
                contradictions.append("角色偏男声但资产标签偏女声")
            score = len(matches) * 2.0 - len(contradictions) * 3.0
            if role == "旁白" and "narration" in asset_traits:
                score += 2.0
            reasons = [f"匹配标签：{trait}" for trait in matches]
            reasons.extend(contradictions)
            if not reasons:
                reasons.append("音色资产缺少可匹配标签，作为备选")
            recommendations.append({
                "voice_name": str(asset.get("name") or ""),
                "path": str(asset.get("path") or ""),
                "category": str(asset.get("category") or "未分类"),
                "score": round(score, 2),
                "reasons": "；".join(reasons),
            })
        recommendations.sort(
            key=lambda item: (-item["score"], item["voice_name"].casefold())
        )
        return recommendations[:max(1, int(top_k))]


class DirectorAuditionService:
    """按 structured_script v3 参数合成一个带停顿的导演试听。"""

    @staticmethod
    def synthesize(
        script_path: str,
        segment_id: str,
        voice_name: str,
        *,
        engine=None,
        assets: Optional[list[dict]] = None,
    ) -> tuple[str, bool]:
        target = Path(script_path)
        if not target.is_file():
            raise FileNotFoundError(f"找不到导演剧本：{target}")
        script = json.loads(target.read_text(encoding="utf-8"))
        segment = DirectorAuditionService._find_segment(script, segment_id)
        assets = list(assets) if assets is not None else voice_lib.scan_voice_library()
        asset = next(
            (item for item in assets if item.get("name") == voice_name),
            None,
        )
        if not asset or not os.path.isfile(str(asset.get("path") or "")):
            raise ValueError(f"音色库中找不到声音：{voice_name}")
        voice_path = str(asset["path"])
        cache_key = DirectorAuditionService._cache_key(segment, voice_path)
        output_dir = Path(config.get_preview_dir()) / "script_director" / "auditions"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{cache_key}.wav"
        if output_path.is_file():
            return str(output_path), True

        if engine is None:
            from lib import tts_engine as engine
        engine.init_engine()
        delivery = (
            segment.get("delivery")
            if isinstance(segment.get("delivery"), dict)
            else {}
        )
        directed_synthesis.synthesize(
            segment=segment,
            speaker_audio=voice_path,
            emotion=str(segment.get("emotion") or "neutral"),
            emo_alpha=float(
                delivery.get(
                    "intensity",
                    segment.get("emotion_strength", segment.get("emo_alpha", 0.4)),
                )
            ),
            speech_rate=float(
                delivery.get("speed", segment.get("speech_rate", 1.0))
            ),
            pinyin_hints=segment.get("pinyin_hints"),
            output_path=str(output_path),
            num_beams=2,
            engine=engine,
        )
        return str(output_path), False

    @staticmethod
    def _find_segment(script: dict, segment_id: str) -> dict:
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                if str(segment.get("id")) == str(segment_id):
                    return segment
        raise ValueError(f"剧本中不存在 segment：{segment_id}")

    @staticmethod
    def _cache_key(segment: dict, voice_path: str) -> str:
        stat = os.stat(voice_path)
        payload: dict[str, Any] = {
            "segment": segment,
            "voice_path": os.path.abspath(voice_path),
            "voice_size": stat.st_size,
            "voice_mtime_ns": stat.st_mtime_ns,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
