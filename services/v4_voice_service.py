"""音色绑定服务：校验参考音频、SHA 指纹去重、写 voices.json、触发局部失效。

V4 工作台与原五步「角色与声音」页共用本服务，禁止复制业务逻辑。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from domain.v4.production import VoiceBinding, VoiceBindings
from repositories.production_repository import ProductionRepository
from repositories.runtime_repository import RuntimeRepository
from services.audio_validation import validate_audio_file


class V4VoiceService:
    @staticmethod
    def bind_voice(
        project_path: str | Path,
        speaker_id: str,
        audio_file: str | Path,
        *,
        regenerate_plan: bool = True,
    ) -> tuple[bool, str]:
        """绑定参考音色到角色。

        Args:
            project_path: V4 项目路径。
            speaker_id: 目标角色 ID（必须存在于 speakers.json）。
            audio_file: 参考音频源路径。
            regenerate_plan: 绑定成功后是否自动重新生成合成计划（触发局部失效）。

        Returns:
            ``(ok, message)``；``ok=False`` 时 message 为可直接展示的用户可读错误。
        """
        project = Path(project_path)
        ok, error = validate_audio_file(audio_file)
        if not ok:
            return False, error
        production = ProductionRepository(project)
        voices, _performance, _pronunciation, _profile = production.load_inputs()
        if speaker_id not in {
            item.speaker_id
            for item in _speakers_document(project).speakers
        }:
            return False, f"角色不存在：{speaker_id}"
        source = Path(audio_file)
        raw = source.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        target = project / "assets/voices" / f"{fingerprint[:16]}{source.suffix}"
        if target.resolve() != source.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        bindings = dict(voices.bindings)
        bindings[speaker_id] = VoiceBinding(
            target.relative_to(project).as_posix(), fingerprint
        )
        production.save_document(
            "voices.json",
            VoiceBindings(bindings, revision=voices.revision + 1).to_dict(),
        )
        message = "✅ 音色已绑定"
        if regenerate_plan:
            try:
                changed = V4VoiceService._refresh_plan(project)
            except Exception as exc:  # noqa: BLE001 - 计划刷新失败不阻断绑定
                changed = 0
                message = f"✅ 音色已绑定；⚠ 计划刷新失败：{exc}"
            if changed:
                message += "；已重新生成计划并局部失效旧任务"
        return True, message

    @staticmethod
    def unbind_voice(
        project_path: str | Path,
        speaker_id: str,
        *,
        regenerate_plan: bool = True,
    ) -> tuple[bool, str]:
        """Remove a V4 binding and refresh only affected synthesis tasks."""
        project = Path(project_path)
        production = ProductionRepository(project)
        voices, _performance, _pronunciation, _profile = production.load_inputs()
        if speaker_id not in {
            item.speaker_id for item in _speakers_document(project).speakers
        }:
            return False, f"角色不存在：{speaker_id}"
        if speaker_id not in voices.bindings:
            return False, "该角色当前没有绑定音色"
        bindings = dict(voices.bindings)
        bindings.pop(speaker_id)
        production.save_document(
            "voices.json",
            VoiceBindings(bindings, revision=voices.revision + 1).to_dict(),
        )
        message = "✅ 已解除音色绑定"
        if regenerate_plan:
            try:
                changed = V4VoiceService._refresh_plan(project)
            except Exception as exc:  # noqa: BLE001 - keep binding change durable
                changed = 0
                message = f"✅ 已解除音色绑定；⚠ 计划刷新失败：{exc}"
            if changed:
                message += "；已重新生成计划并局部失效旧任务"
        return True, message

    @staticmethod
    def _refresh_plan(project: Path) -> int:
        """重新生成合成计划并同步 runtime（返回 stale 任务数；无旧计划返回 0）。"""
        from services.invalidation_service import InvalidationService
        from services.synthesis_planner import SynthesisPlanner
        from tts.text_measurement import CharacterMeasurer, ConservativeTokenMeasurer

        production = ProductionRepository(project)
        source = (project / "source/source.txt").read_text(encoding="utf-8")
        script = _script_document(project, source)
        speakers = _speakers_document(project)
        voices, performance, pronunciation, profile = production.load_inputs()
        previous = production.load_plan()
        if previous is None:
            return 0
        measurer = (
            ConservativeTokenMeasurer()
            if profile.limits.metric == "tokens"
            else CharacterMeasurer()
        )
        result = SynthesisPlanner(measurer).plan(
            source,
            script,
            speakers,
            voices,
            performance,
            pronunciation,
            profile,
            previous_plan=previous,
        )
        production.save_plan(result.plan)
        runtime = RuntimeRepository(project / "runtime/runtime.db")
        runtime.initialize()
        diff = InvalidationService.sync_runtime(runtime, previous, result.plan)
        return len(diff.stale_task_ids)


def _script_document(project: Path, source: str):
    import json

    from domain.v4 import ScriptDocument

    return ScriptDocument.from_dict(
        json.loads((project / "script/script.json").read_text(encoding="utf-8")),
        source,
    )


def _speakers_document(project: Path):
    import json

    from domain.v4 import SpeakersDocument

    return SpeakersDocument.from_dict(
        json.loads(
            (project / "script/speakers.json").read_text(encoding="utf-8")
        )
    )
