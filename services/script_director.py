"""AI 剧本导演服务：TXT → structured_script.json v3。"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from ai.providers import LocalDirectorProvider, ScriptAnalysisProvider
from lib import script_loader
from lib.text_importer import load_text

_BREATHS = {"none", "light", "normal", "heavy"}
EDITOR_COLUMNS = (
    "id",
    "speaker",
    "text",
    "emotion",
    "speed",
    "intensity",
    "breath",
    "pause_before",
    "pause_after",
)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    return max(low, min(high, _number(value, default)))


class ScriptDirectorService:
    """编排 Provider，并对所有模型输出应用统一质量守卫。"""

    def __init__(self, provider: Optional[ScriptAnalysisProvider] = None):
        self.provider = provider or LocalDirectorProvider()

    def analyze_text(
        self,
        text: str,
        *,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("TXT 内容为空，无法进行剧本分析")
        raw = self.provider.analyze_script(text, title=title, author=author)
        if isinstance(raw, dict):
            raw.setdefault("provider", self.provider.name)
        return self.normalize_script(raw, title=title, author=author)

    def analyze_txt(
        self,
        txt_path: str,
        *,
        output_path: Optional[str] = None,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        if Path(txt_path).suffix.lower() != ".txt":
            raise ValueError("analyze_txt 仅接受 .txt；其他格式请使用 analyze_file")
        return self.analyze_file(
            txt_path,
            output_path=output_path,
            title=title,
            author=author,
        )

    def analyze_file(
        self,
        input_path: str,
        *,
        output_path: Optional[str] = None,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        path = Path(input_path)
        text = load_text(str(path))
        script = self.analyze_text(
            text,
            title=title or path.stem,
            author=author,
        )
        if output_path:
            self.save_script(script, output_path)
        return script

    @staticmethod
    def save_script(script: Dict[str, Any], output_path: str) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temp_path = file.name
                json.dump(script, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, target)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return str(target)

    @staticmethod
    def chapter_choices(script: Dict[str, Any]) -> list[tuple[str, str]]:
        """返回人工导演表的章节选择项。"""
        return [
            (
                f"第{chapter.get('id')}章 · {chapter.get('title') or '未命名'}"
                f" · {len(chapter.get('segments', []))} 段",
                str(chapter.get("id")),
            )
            for chapter in script.get("chapters", [])
        ]

    @staticmethod
    def editor_rows(
        script: Dict[str, Any],
        chapter_id: Optional[str] = None,
    ) -> list[list[Any]]:
        """把 v3 剧本按章节转换为人工导演表格，避免全书一次传给浏览器。"""
        rows = []
        for chapter in script.get("chapters", []):
            if chapter_id is not None and str(chapter.get("id")) != str(chapter_id):
                continue
            for segment in chapter.get("segments", []):
                delivery = (
                    segment.get("delivery")
                    if isinstance(segment.get("delivery"), dict)
                    else {}
                )
                rows.append([
                    str(segment.get("id") or ""),
                    str(segment.get("speaker") or segment.get("role") or "旁白"),
                    str(segment.get("text") or ""),
                    str(segment.get("emotion") or "neutral"),
                    delivery.get("speed", segment.get("speech_rate", 1.0)),
                    delivery.get(
                        "intensity",
                        segment.get("emotion_strength", segment.get("emo_alpha", 0.4)),
                    ),
                    str(delivery.get("breath") or "none"),
                    segment.get("pause_before", 0),
                    segment.get("pause_after", 600),
                ])
        return rows

    @classmethod
    def apply_segment_edits(
        cls,
        script: Dict[str, Any],
        rows: Any,
    ) -> tuple[Dict[str, Any], int]:
        """应用人工编辑，并重新执行 v3 质量守卫。"""
        records = cls._editor_records(rows)
        if not records:
            raise ValueError("人工导演表格为空")

        # 先规范化为独立输出对象，再原地应用当前章节编辑。避免长篇剧本出现
        # raw + deepcopy + normalize 三份完整对象同时驻留。
        updated = cls.normalize_script(script)
        segments = {
            str(segment.get("id")): segment
            for chapter in updated.get("chapters", [])
            for segment in chapter.get("segments", [])
            if isinstance(segment, dict) and segment.get("id") is not None
        }
        seen = set()
        changed = 0
        for record in records:
            segment_id = str(record.get("id") or "").strip()
            if not segment_id:
                raise ValueError("人工导演表格存在空 segment id")
            if segment_id in seen:
                raise ValueError(f"人工导演表格存在重复 segment id：{segment_id}")
            seen.add(segment_id)
            if segment_id not in segments:
                raise ValueError(f"人工导演表格包含未知 segment id：{segment_id}")

            segment = segments[segment_id]
            before = json.dumps(segment, ensure_ascii=False, sort_keys=True)
            speaker = str(record.get("speaker") or "").strip()
            text = str(record.get("text") or "").strip()
            emotion = str(record.get("emotion") or "").strip()
            breath = str(record.get("breath") or "none").strip()
            if not speaker:
                raise ValueError(f"段落 {segment_id} 的角色不能为空")
            if not text:
                raise ValueError(f"段落 {segment_id} 的文本不能为空")
            if not emotion:
                raise ValueError(f"段落 {segment_id} 的情绪不能为空")
            if breath not in _BREATHS:
                raise ValueError(
                    f"段落 {segment_id} 的 breath 无效：{breath}；"
                    "可选 none/light/normal/heavy"
                )

            segment["speaker"] = speaker
            segment["role"] = speaker
            segment["text"] = text
            segment["emotion"] = emotion
            segment["emotion_strength"] = _clamp(
                record.get("intensity"), 0.0, 1.0, 0.4
            )
            segment["emo_alpha"] = segment["emotion_strength"]
            delivery = (
                segment.get("delivery")
                if isinstance(segment.get("delivery"), dict)
                else {}
            )
            delivery.update({
                "speed": _clamp(record.get("speed"), 0.85, 1.15, 1.0),
                "intensity": segment["emotion_strength"],
                "breath": breath,
            })
            segment["delivery"] = delivery
            segment["speech_rate"] = delivery["speed"]
            segment["pause_before"] = int(
                _clamp(record.get("pause_before"), 0, 3000, 0)
            )
            segment["pause_after"] = int(
                _clamp(record.get("pause_after"), 0, 3000, 600)
            )
            after = json.dumps(segment, ensure_ascii=False, sort_keys=True)
            if before != after:
                changed += 1

        cls._smooth_speeds_in_place(updated)
        used_speakers = {
            str(segment.get("speaker") or segment.get("role") or "旁白")
            for chapter in updated.get("chapters", [])
            for segment in chapter.get("segments", [])
        }
        for speaker in used_speakers:
            updated["voices"].setdefault(
                speaker,
                {"description": "由人工导演调整，待绑定音色"},
            )
        updated["voices"] = {
            name: info
            for name, info in updated.get("voices", {}).items()
            if name in used_speakers
        }
        return updated, changed

    @classmethod
    def save_segment_edits(
        cls,
        script_path: str,
        rows: Any,
    ) -> tuple[Dict[str, Any], str, int]:
        """保存人工编辑，并创建可撤销历史快照。"""
        target = Path(script_path)
        if not target.is_file():
            raise FileNotFoundError(f"找不到待编辑剧本：{target}")
        with target.open(encoding="utf-8") as file:
            raw = json.load(file)
        updated, changed = cls.apply_segment_edits(raw, rows)
        backup = cls._save_revision(target, raw, updated)
        return updated, backup, changed

    @classmethod
    def apply_audition_feedback(
        cls,
        script_path: str,
        segment_id: str,
        feedback: str,
    ) -> tuple[Dict[str, Any], str, str]:
        """把试听反馈转换为可撤销的小步导演参数调整。"""
        target = Path(script_path)
        if not target.is_file():
            raise FileNotFoundError(f"找不到待调整剧本：{target}")
        with target.open(encoding="utf-8") as file:
            raw = json.load(file)
        source_segment = next(
            (
                item
                for chapter in raw.get("chapters", [])
                for item in chapter.get("segments", [])
                if str(item.get("id")) == str(segment_id)
            ),
            None,
        )
        if source_segment is None:
            raise ValueError(f"剧本中不存在 segment：{segment_id}")

        source_delivery = (
            source_segment.get("delivery")
            if isinstance(source_segment.get("delivery"), dict)
            else {}
        )
        speed = _clamp(
            source_delivery.get(
                "speed",
                source_segment.get("speech_rate", 1.0),
            ),
            0.85,
            1.15,
            1.0,
        )
        intensity = _clamp(
            source_delivery.get(
                "intensity",
                source_segment.get(
                    "emotion_strength",
                    source_segment.get("emo_alpha", 0.4),
                ),
            ),
            0.0,
            1.0,
            0.4,
        )
        breath = str(source_delivery.get("breath") or "none")

        updated = cls.normalize_script(raw)
        segment = next(
            item
            for chapter in updated.get("chapters", [])
            for item in chapter.get("segments", [])
            if str(item.get("id")) == str(segment_id)
        )
        delivery = segment["delivery"]
        summary = ""

        if feedback == "slower":
            new_speed = round(max(0.85, speed - 0.05), 3)
            if new_speed == speed:
                raise ValueError("当前语速已达到最慢边界 0.85")
            delivery["speed"] = new_speed
            segment["speech_rate"] = new_speed
            summary = f"语速 {speed:.2f} → {new_speed:.2f}"
        elif feedback == "faster":
            new_speed = round(min(1.15, speed + 0.05), 3)
            if new_speed == speed:
                raise ValueError("当前语速已达到最快边界 1.15")
            delivery["speed"] = new_speed
            segment["speech_rate"] = new_speed
            summary = f"语速 {speed:.2f} → {new_speed:.2f}"
        elif feedback == "stronger":
            new_intensity = round(min(1.0, intensity + 0.1), 3)
            if new_intensity == intensity:
                raise ValueError("当前强度已达到上限 1.0")
            delivery["intensity"] = new_intensity
            segment["emotion_strength"] = new_intensity
            segment["emo_alpha"] = new_intensity
            summary = f"强度 {intensity:.2f} → {new_intensity:.2f}"
        elif feedback == "softer":
            new_intensity = round(max(0.0, intensity - 0.1), 3)
            if new_intensity == intensity:
                raise ValueError("当前强度已达到下限 0.0")
            delivery["intensity"] = new_intensity
            segment["emotion_strength"] = new_intensity
            segment["emo_alpha"] = new_intensity
            summary = f"强度 {intensity:.2f} → {new_intensity:.2f}"
        elif feedback in {"longer_pauses", "shorter_pauses"}:
            factor = 1.25 if feedback == "longer_pauses" else 0.8

            def scale(value: Any) -> int:
                original = int(_clamp(value, 0, 3000, 0))
                if not original:
                    return 0
                return int(max(100, min(3000, round(original * factor / 50) * 50)))

            before = int(segment.get("pause_before") or 0)
            after = int(segment.get("pause_after") or 0)
            segment["pause_before"] = scale(before)
            segment["pause_after"] = scale(after)
            for pause in segment.get("pauses", []):
                if isinstance(pause, dict):
                    pause["duration"] = scale(pause.get("duration"))
            direction = "延长 25%" if factor > 1 else "缩短 20%"
            summary = f"前后与内部停顿已{direction}"
        elif feedback in {"more_breath", "less_breath"}:
            levels = ["none", "light", "normal", "heavy"]
            current = breath if breath in levels else "none"
            index = levels.index(current)
            new_index = (
                min(len(levels) - 1, index + 1)
                if feedback == "more_breath"
                else max(0, index - 1)
            )
            if new_index == index:
                boundary = "最强" if feedback == "more_breath" else "最弱"
                raise ValueError(f"当前呼吸感已达到{boundary}等级")
            delivery["breath"] = levels[new_index]
            summary = f"呼吸 {current} → {levels[new_index]}"
        else:
            raise ValueError(f"不支持的试听反馈：{feedback}")

        segment["delivery"] = delivery
        cls._smooth_speeds_in_place(updated)
        backup = cls._save_revision(target, raw, updated)
        return updated, backup, summary

    @classmethod
    def _save_revision(
        cls,
        target: Path,
        previous: Dict[str, Any],
        updated: Dict[str, Any],
    ) -> str:
        """保存前态快照并原子写入新剧本，返回快照路径。"""
        history_dir = target.parent / ".director_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        backup = history_dir / (
            f"{target.stem}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
        )
        cls.save_script(previous, str(backup))
        cls.save_script(updated, str(target))
        return str(backup)

    @classmethod
    def undo_segment_edits(
        cls,
        script_path: str,
        backup_path: str,
    ) -> Dict[str, Any]:
        """从最近一次 UI 保存返回的历史快照恢复剧本。"""
        target = Path(script_path).resolve()
        backup = Path(backup_path).resolve()
        expected_history = (target.parent / ".director_history").resolve()
        if backup.parent != expected_history:
            raise ValueError("撤销快照不属于当前剧本")
        if not backup.is_file():
            raise FileNotFoundError("找不到可撤销的导演历史快照")
        with backup.open(encoding="utf-8") as file:
            raw = json.load(file)
        errors = script_loader.validate_script(script_loader.from_dict(raw))
        if errors:
            raise ValueError("导演历史快照无效：" + "；".join(errors))
        cls.save_script(raw, str(target))
        return raw

    @staticmethod
    def _editor_records(rows: Any) -> list[dict[str, Any]]:
        if hasattr(rows, "to_dict"):
            records = rows.to_dict(orient="records")
            return [
                {str(key): value for key, value in record.items()}
                for record in records
            ]
        if not isinstance(rows, list):
            raise ValueError("人工导演表格格式无效")
        records = []
        for row in rows:
            if isinstance(row, dict):
                records.append({str(key): value for key, value in row.items()})
            elif isinstance(row, (list, tuple)):
                records.append({
                    column: row[index] if index < len(row) else None
                    for index, column in enumerate(EDITOR_COLUMNS)
                })
            else:
                raise ValueError("人工导演表格包含无法识别的行")
        return records

    @staticmethod
    def _smooth_speeds_in_place(script: Dict[str, Any]) -> None:
        """原地收敛相邻语速，供局部编辑与试听反馈复用。"""
        previous = 1.0
        for chapter in script.get("chapters", []):
            for segment in chapter.get("segments", []):
                delivery = (
                    segment.get("delivery")
                    if isinstance(segment.get("delivery"), dict)
                    else {}
                )
                speed = _clamp(
                    delivery.get("speed", segment.get("speech_rate", 1.0)),
                    0.85,
                    1.15,
                    1.0,
                )
                speed = round(max(previous - 0.1, min(previous + 0.1, speed)), 3)
                delivery["speed"] = speed
                segment["delivery"] = delivery
                segment["speech_rate"] = speed
                previous = speed

    @classmethod
    def normalize_script(
        cls,
        raw: Dict[str, Any],
        *,
        title: str = "",
        author: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("AI Provider 返回值必须是 JSON 对象")
        # 本函数只读取 raw，并为 meta / voices / chapters / segments 构造新对象；
        # 无需对整本书先做 deepcopy。长篇小说可避免一次完整对象图的峰值复制。
        data = raw
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        meta = {
            **meta,
            "title": meta.get("title") or title or "未命名作品",
            "author": meta.get("author") or author,
            "director_provider": meta.get("director_provider") or data.get("provider") or "unknown",
        }

        chapters = data.get("chapters")
        if not isinstance(chapters, list):
            flat_segments = data.get("segments")
            chapters = [{
                "id": 1,
                "title": data.get("chapter") or "第一章",
                "segments": flat_segments if isinstance(flat_segments, list) else [],
            }]

        normalized_chapters = []
        voices = data.get("voices") if isinstance(data.get("voices"), dict) else {}
        if not voices and isinstance(data.get("characters"), dict):
            voices = data["characters"]
        voices = deepcopy(voices)
        previous_speed = 1.0
        total_segments = 0

        for chapter_index, chapter_raw in enumerate(chapters, 1):
            chapter = chapter_raw if isinstance(chapter_raw, dict) else {}
            segments = chapter.get("segments") if isinstance(chapter.get("segments"), list) else []
            normalized_segments = []
            for segment_index, segment_raw in enumerate(segments, 1):
                if not isinstance(segment_raw, dict):
                    continue
                text = str(segment_raw.get("text") or "").strip()
                if not text:
                    continue
                speaker = str(
                    segment_raw.get("speaker")
                    or segment_raw.get("role")
                    or "旁白"
                ).strip()
                emotion = str(segment_raw.get("emotion") or "neutral").strip()
                delivery_raw = (
                    segment_raw.get("delivery")
                    if isinstance(segment_raw.get("delivery"), dict)
                    else {}
                )
                requested_speed = delivery_raw.get(
                    "speed",
                    segment_raw.get("speech_rate", segment_raw.get("speed", 1.0)),
                )
                speed = _clamp(requested_speed, 0.85, 1.15, 1.0)
                # 相邻段速度最大跳变 0.1，避免 TTS 音色和节奏突变。
                speed = max(previous_speed - 0.1, min(previous_speed + 0.1, speed))
                speed = round(speed, 3)
                previous_speed = speed

                intensity = _clamp(
                    delivery_raw.get(
                        "intensity",
                        segment_raw.get("emotion_strength", segment_raw.get("emo_alpha", 0.4)),
                    ),
                    0.0,
                    1.0,
                    0.4,
                )
                breath = str(delivery_raw.get("breath") or segment_raw.get("breath") or "none")
                if breath not in _BREATHS:
                    breath = "none"
                pauses = segment_raw.get("pauses")
                pauses = cls._normalize_pauses(pauses, len(text))

                segment = {
                    "id": str(segment_raw.get("id") or f"{chapter_index}-{segment_index:03d}"),
                    "speaker": speaker,
                    # v2 兼容字段：现有 TTS / UI 仍直接读取 role。
                    "role": speaker,
                    "text": text,
                    "emotion": emotion,
                    "emotion_strength": round(intensity, 3),
                    # v2 兼容字段：现有 TTS 引擎读取 emo_alpha / speech_rate。
                    "emo_alpha": round(intensity, 3),
                    "speech_rate": speed,
                    "delivery": {
                        "speed": speed,
                        "pitch": round(_clamp(delivery_raw.get("pitch", 0), -12, 12, 0), 3),
                        "intensity": round(intensity, 3),
                        "breath": breath,
                    },
                    "pause_before": int(_clamp(segment_raw.get("pause_before", 0), 0, 3000, 0)),
                    "pause_after": int(_clamp(segment_raw.get("pause_after", 600), 0, 3000, 600)),
                    "pauses": pauses,
                }
                if isinstance(segment_raw.get("pinyin_hints"), dict):
                    segment["pinyin_hints"] = deepcopy(segment_raw["pinyin_hints"])
                voices.setdefault(speaker, {"description": "由 AI 剧本导演识别，待绑定音色"})
                normalized_segments.append(segment)
                total_segments += 1

            if normalized_segments:
                normalized_chapters.append({
                    "id": chapter.get("id") or chapter_index,
                    "title": str(chapter.get("title") or f"第{chapter_index}章"),
                    "segments": normalized_segments,
                })

        if not normalized_chapters:
            raise ValueError("AI Provider 未生成任何有效 segment")
        meta["total_segments"] = total_segments
        script = {
            "version": "3.0",
            "meta": meta,
            "voices": voices,
            "chapters": normalized_chapters,
        }
        errors = script_loader.validate_script(script_loader.from_dict(script))
        if errors:
            raise ValueError("structured_script v3 校验失败：" + "；".join(errors))
        return script

    @staticmethod
    def _normalize_pauses(pauses: Any, text_length: int) -> list:
        if not isinstance(pauses, list):
            return []
        normalized = []
        for pause in pauses:
            if not isinstance(pause, dict):
                continue
            position = int(_clamp(pause.get("position", 0), 0, text_length, 0))
            duration = int(_clamp(pause.get("duration", 400), 100, 3000, 400))
            pause_type = str(pause.get("type") or "pause_short")
            if pause_type not in {"pause_short", "pause_think", "pause_drama"}:
                pause_type = "pause_short"
            normalized.append({
                "position": position,
                "duration": duration,
                "type": pause_type,
            })
        return normalized
