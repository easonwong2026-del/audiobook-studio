"""Copy-only, repeatable migration from structured_script v3 to v4."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.v4 import (
    ChapterScript,
    ProjectManifest,
    ScriptDocument,
    SemanticSegment,
    SourceMetadata,
    Speaker,
    SpeakersDocument,
)
from domain.v4.models import source_sha256, stable_speaker_id
from domain.v4.production import (
    PerformanceOverrides,
    TtsProfile,
    VoiceBinding,
    VoiceBindings,
)
from repositories.production_repository import ProductionRepository
from repositories.project_v4_repository import ProjectV4Repository
from repositories.v4_atomic import _short_tmp, atomic_write_json


@dataclass(frozen=True)
class MigrationResult:
    project_path: Path
    backup_path: Path
    segment_count: int
    speaker_count: int
    reused_existing: bool = False


class V3ToV4MigrationService:
    def migrate(
        self,
        source_project: str | Path,
        destination_root: str | Path,
        *,
        destination_name: str | None = None,
    ) -> MigrationResult:
        source = Path(source_project).resolve()
        root = Path(destination_root).resolve()
        name = destination_name or f"{source.name}-v4"
        target = root / name
        marker = target / "revisions/migration-v3.json"
        if marker.is_file():
            data = json.loads(marker.read_text(encoding="utf-8"))
            if data.get("source_project") == str(source):
                return MigrationResult(
                    target,
                    Path(data["backup_path"]),
                    int(data["segment_count"]),
                    int(data["speaker_count"]),
                    reused_existing=True,
                )
        if target.exists():
            raise FileExistsError(f"migration target already exists: {target}")
        script_path = source / "structured_script.json"
        bindings_path = source / "voice_bindings.json"
        if not script_path.is_file() or not bindings_path.is_file():
            raise ValueError("source is not a complete v3 project")
        script_v3 = json.loads(script_path.read_text(encoding="utf-8"))
        bindings_v3 = json.loads(bindings_path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).isoformat()
        source_text, script, speakers, performance = self._convert(script_v3)
        metadata = SourceMetadata(
            original_filename="structured_script.json",
            source_format="v3-reconstructed",
            encoding="utf-8",
            normalization="v3-segment-reconstruction-v1",
            char_count=len(source_text),
            sha256=source_sha256(source_text),
            imported_at=now,
            source_origin="reconstructed-from-v3",
            source_fidelity="segment-text",
        )
        manifest = ProjectManifest(
            project_id=f"project_{uuid.uuid4().hex}",
            name=name,
            title=str(script_v3.get("meta", {}).get("title") or name),
            author=str(script_v3.get("meta", {}).get("author") or ""),
            created_at=now,
            updated_at=now,
        )
        backup_root = root / ".v3-backups"
        backup = backup_root / (
            f"{source.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, backup)
        staging_root = root / f".tmp_v3_{_short_tmp()}"
        try:
            profile = self._profile()
            staged = ProjectV4Repository(staging_root).create(
                name,
                manifest,
                source_text,
                metadata,
                script,
                speakers,
                tts_profile=profile,
            )
            production = ProductionRepository(staged)
            voices = self._migrate_voices(
                source, staged, speakers, bindings_v3
            )
            production.save_document("voices.json", voices.to_dict())
            production.save_document("performance.json", performance.to_dict())
            marker_data = {
                "schema_version": "audiobook-v3-migration-v1",
                "source_project": str(source),
                "backup_path": str(backup),
                "source_origin": "reconstructed-from-v3",
                "source_fidelity": "segment-text",
                "segment_count": sum(
                    len(chapter.segments) for chapter in script.chapters
                ),
                "speaker_count": len(speakers.speakers),
                "migrated_at": now,
            }
            atomic_write_json(staged / "revisions/migration-v3.json", marker_data)
            root.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
        except Exception:
            if staging_root.exists():
                shutil.rmtree(staging_root)
            raise
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)
        return MigrationResult(
            target,
            backup,
            marker_data["segment_count"],
            marker_data["speaker_count"],
        )

    @staticmethod
    def _convert(
        script_v3: dict,
    ) -> tuple[str, ScriptDocument, SpeakersDocument, PerformanceOverrides]:
        source_parts: list[str] = []
        chapters: list[ChapterScript] = []
        names: dict[str, Speaker] = {
            "narrator": Speaker(
                "narrator", "旁白", "confirmed",
                speaker_type="narrator", locked=True,
            )
        }
        overrides: dict[str, dict] = {}
        cursor = 0
        sequence = 1
        for chapter_index, raw_chapter in enumerate(
            script_v3.get("chapters", []), start=1
        ):
            if chapters:
                source_parts.append("\n\n")
                cursor += 2
            chapter_start = cursor
            segments: list[SemanticSegment] = []
            for raw_segment in raw_chapter.get("segments", []):
                if segments:
                    source_parts.append("\n")
                    cursor += 1
                text = str(raw_segment.get("text") or "")
                if not text:
                    continue
                start = cursor
                source_parts.append(text)
                cursor += len(text)
                role = str(
                    raw_segment.get("role")
                    or raw_segment.get("speaker")
                    or "旁白"
                )
                speaker_id = (
                    "narrator" if role == "旁白" else stable_speaker_id(role)
                )
                if speaker_id not in names:
                    names[speaker_id] = Speaker(
                        speaker_id, role, "confirmed", speaker_type="character"
                    )
                segment_id = f"segment_{sequence:06d}"
                segments.append(
                    SemanticSegment(
                        segment_id,
                        f"chapter_{chapter_index:04d}",
                        start,
                        cursor,
                        "narration" if speaker_id == "narrator" else "dialogue",
                        speaker_id,
                        "manual",
                        "confirmed",
                    )
                )
                override = V3ToV4MigrationService._performance_override(raw_segment)
                if override:
                    overrides[segment_id] = override
                sequence += 1
            if segments:
                chapters.append(
                    ChapterScript(
                        f"chapter_{chapter_index:04d}",
                        str(raw_chapter.get("title") or f"第{chapter_index}章"),
                        chapter_start,
                        cursor,
                        segments,
                    )
                )
        source_text = "".join(source_parts)
        if not source_text:
            raise ValueError("v3 script contains no segment text")
        script = ScriptDocument(source_sha256(source_text), chapters)
        speakers = SpeakersDocument(list(names.values()))
        performance = PerformanceOverrides(overrides)
        script.validate(source_text)
        speakers.validate()
        performance.validate()
        return source_text, script, speakers, performance

    @staticmethod
    def _performance_override(segment: dict) -> dict | None:
        emotion = str(segment.get("emotion") or "neutral")
        delivery = segment.get("delivery") if isinstance(segment.get("delivery"), dict) else {}
        speed = float(delivery.get("speed", segment.get("speech_rate", 1.0)))
        strength = float(
            delivery.get(
                "intensity",
                segment.get("emotion_strength", segment.get("emo_alpha", 1.0)),
            )
        )
        if emotion == "neutral" and speed == 1.0 and strength == 1.0:
            return None
        return {
            "emotion_mode": "manual",
            "emotion": emotion,
            "emotion_strength": strength,
            "speech_rate": speed,
        }

    @staticmethod
    def _migrate_voices(
        source: Path,
        staged: Path,
        speakers: SpeakersDocument,
        bindings_v3: dict,
    ) -> VoiceBindings:
        by_name = {item.display_name: item.speaker_id for item in speakers.speakers}
        assets = staged / "assets/voices"
        migrated: dict[str, VoiceBinding] = {}
        for role, raw_path in bindings_v3.get("bindings", {}).items():
            if not raw_path or role not in by_name:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = source / path
            if not path.is_file():
                continue
            assets.mkdir(parents=True, exist_ok=True)
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            destination = assets / f"{fingerprint[:16]}{path.suffix or '.wav'}"
            shutil.copy2(path, destination)
            migrated[by_name[role]] = VoiceBinding(
                destination.relative_to(staged).as_posix(),
                fingerprint,
            )
        return VoiceBindings(migrated)

    @staticmethod
    def _profile() -> TtsProfile:
        path = (
            Path(__file__).resolve().parents[1]
            / "config/tts_profiles/indextts2-rtx5070ti-laptop-12gb-v1.json"
        )
        return TtsProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
