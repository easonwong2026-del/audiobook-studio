"""Integration and migration rules for AI-first V4 re-analysis."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domain.v4 import CharacterBibleDocument, ScriptDocument, Speaker, SpeakersDocument
from domain.v4.models import stable_speaker_id
from domain.v4.production import VoiceBindings
from repositories.v4_atomic import atomic_write_json


@dataclass(frozen=True)
class SpeakerReconciliation:
    speakers: SpeakersDocument
    speaker_id_map: dict[str, str]
    preserved_speaker_ids: set[str]


def reconcile_speakers(
    bible: CharacterBibleDocument,
    existing: SpeakersDocument,
    old_script: ScriptDocument,
) -> SpeakerReconciliation:
    """Create formal speakers from the bible, retaining manual/locked people."""
    existing_characters = [
        item for item in existing.speakers if item.speaker_type == "character"
    ]
    by_name = {
        name: item
        for item in existing_characters
        for name in [item.display_name, *item.aliases]
    }
    manual_ids = {
        segment.speaker_id
        for chapter in old_script.chapters
        for segment in chapter.segments
        if segment.speaker_source == "manual" and segment.speaker_id
    }
    preserved_ids = {
        item.speaker_id
        for item in existing_characters
        if item.locked or item.speaker_id in manual_ids
    }
    ordered: list[Speaker] = [
        Speaker(
            speaker_id="narrator",
            display_name="旁白",
            status="confirmed",
            speaker_type="narrator",
            locked=True,
        )
    ]
    id_map: dict[str, str] = {"narrator": "narrator"}
    matched_existing: set[str] = set()
    for character in bible.characters:
        names = [character.canonical_name, *character.aliases]
        previous = next((by_name[name] for name in names if name in by_name), None)
        if previous is not None:
            effective_id = previous.speaker_id
            matched_existing.add(previous.speaker_id)
            if previous.locked:
                speaker = previous
            else:
                speaker = replace(
                    previous,
                    display_name=character.canonical_name,
                    aliases=list(dict.fromkeys(character.aliases)),
                    status="confirmed",
                )
        else:
            effective_id = character.speaker_id or stable_speaker_id(character.canonical_name)
            speaker = Speaker(
                speaker_id=effective_id,
                display_name=character.canonical_name,
                aliases=list(dict.fromkeys(character.aliases)),
                status="confirmed",
                speaker_type="character",
            )
        ordered.append(speaker)
        for identifier in {character.character_id, character.speaker_id, effective_id}:
            if identifier:
                id_map[identifier] = effective_id

    # A manual or locked identity not present in the new bible remains usable;
    # the reviewer can decide whether it is mentioned by this edition.
    for item in existing_characters:
        if item.speaker_id in preserved_ids and item.speaker_id not in matched_existing:
            ordered.append(item)
            id_map[item.speaker_id] = item.speaker_id

    # Avoid duplicate IDs when a bible contains two observations that resolve
    # to one existing identity.
    unique: list[Speaker] = []
    seen: set[str] = set()
    for item in ordered:
        if item.speaker_id not in seen:
            unique.append(item)
            seen.add(item.speaker_id)
    return SpeakerReconciliation(
        speakers=SpeakersDocument(
            speakers=unique, revision=existing.revision + (unique != existing.speakers)
        ),
        speaker_id_map=id_map,
        preserved_speaker_ids=preserved_ids,
    )


def protect_manual_assignments(
    new_script: ScriptDocument,
    old_script: ScriptDocument,
    preserved_speaker_ids: set[str],
    speaker_id_map: dict[str, str],
) -> ScriptDocument:
    """Apply old manual/locked assignments over new AI output by coordinates."""
    protected: list[tuple[int, int, str, str]] = []
    for chapter in old_script.chapters:
        for segment in chapter.segments:
            if not segment.speaker_id:
                continue
            if segment.speaker_source == "manual" or segment.speaker_id in preserved_speaker_ids:
                protected.append((segment.start, segment.end, segment.speaker_id, segment.speaker_source))
    if not protected:
        return new_script
    chapters = []
    changed = False
    for chapter in new_script.chapters:
        segments = []
        for segment in chapter.segments:
            match = next(
                (
                    item for item in protected
                    if item[0] < segment.end and segment.start < item[1]
                    and item[2] in speaker_id_map
                ),
                None,
            )
            if match is None:
                segments.append(segment)
                continue
            speaker_id = speaker_id_map.get(match[2], match[2])
            updated = replace(
                segment,
                speaker_id=speaker_id,
                speaker_source="manual",
                status="confirmed",
            )
            changed = changed or updated != segment
            segments.append(updated)
        chapters.append(replace(chapter, segments=segments))
    return replace(new_script, chapters=chapters, revision=new_script.revision + (1 if changed else 0))


def remap_script_speakers(
    script: ScriptDocument, speaker_id_map: dict[str, str]
) -> ScriptDocument:
    chapters = []
    changed = False
    for chapter in script.chapters:
        segments = []
        for segment in chapter.segments:
            if not segment.speaker_id or segment.speaker_id not in speaker_id_map:
                segments.append(segment)
                continue
            mapped = speaker_id_map[segment.speaker_id]
            updated = replace(segment, speaker_id=mapped)
            changed = changed or updated != segment
            segments.append(updated)
        chapters.append(replace(chapter, segments=segments))
    return replace(script, chapters=chapters, revision=script.revision + (1 if changed else 0))


def snapshot_reanalysis(
    project_path: str | Path,
    *,
    script_data: dict[str, Any],
    speakers_data: dict[str, Any],
    voices_data: dict[str, Any] | None = None,
) -> Path:
    project = Path(project_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = project / "revisions" / f"ai-analysis-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    atomic_write_json(target / "script.json", script_data)
    atomic_write_json(target / "speakers.json", speakers_data)
    if voices_data is not None:
        atomic_write_json(target / "voices.json", voices_data)
    return target


def migrate_voice_bindings(
    project_path: str | Path,
    old_speakers: SpeakersDocument,
    new_speakers: SpeakersDocument,
    speaker_id_map: dict[str, str],
) -> list[dict[str, str]]:
    """Migrate safe bindings and record every ambiguous/unmatched binding."""
    project = Path(project_path)
    voices_path = project / "production/voices.json"
    try:
        voices = VoiceBindings.from_dict(json.loads(voices_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return []
    new_ids = {item.speaker_id for item in new_speakers.speakers}
    old_by_id = {item.speaker_id: item for item in old_speakers.speakers}
    new_by_name = {
        name: item.speaker_id
        for item in new_speakers.speakers
        for name in [item.display_name, *item.aliases]
    }
    bindings = dict(voices.bindings)
    pending: list[dict[str, str]] = []
    changed = False
    for old_id, binding in list(voices.bindings.items()):
        target = speaker_id_map.get(old_id)
        old_speaker = old_by_id.get(old_id)
        if target is None and old_speaker is not None:
            target = next(
                (
                    new_by_name[name]
                    for name in [old_speaker.display_name, *old_speaker.aliases]
                    if name in new_by_name
                ),
                None,
            )
        if target not in new_ids:
            pending.append({
                "old_speaker_id": old_id,
                "voice_id": binding.voice_id,
                "reason": "AI 结果中没有可安全匹配的角色",
            })
            continue
        if target != old_id:
            if target in bindings and target != old_id:
                pending.append({
                    "old_speaker_id": old_id,
                    "voice_id": binding.voice_id,
                    "reason": "目标角色已有另一条音色绑定，待人工确认",
                })
                continue
            bindings[target] = binding
            bindings.pop(old_id, None)
            changed = True
    if changed:
        VoiceBindings(bindings, revision=voices.revision + 1).validate()
        atomic_write_json(voices_path, VoiceBindings(bindings, revision=voices.revision + 1).to_dict())
    atomic_write_json(
        project / "runtime/pending_voice_migrations.json",
        {
            "schema_version": "v4-pending-voice-migrations-v1",
            "pending": pending,
        },
    )
    return pending
