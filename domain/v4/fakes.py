"""Deterministic test doubles for future v4 integration phases."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


class FakeSpeakerRouter:
    """Return caller-supplied ID-only assignments without inspecting source text."""

    def __init__(self, assignments: list[dict[str, str | None]] | None = None):
        self.assignments = deepcopy(assignments or [])
        self.calls: list[dict[str, list[str]]] = []

    def route(self, script: dict[str, Any], speakers: dict[str, Any]) -> dict[str, Any]:
        known_ids = {
            segment["id"]
            for chapter in script.get("chapters", [])
            for segment in chapter.get("segments", [])
        }
        speaker_ids = {speaker["id"] for speaker in speakers.get("speakers", [])}
        seen: set[str] = set()
        accepted: list[dict[str, str | None]] = []
        for assignment in self.assignments:
            segment_id = assignment.get("segment_id")
            speaker_id = assignment.get("speaker_id")
            if (
                not segment_id
                or segment_id in seen
                or segment_id not in known_ids
                or (speaker_id is not None and speaker_id not in speaker_ids)
            ):
                continue
            seen.add(segment_id)
            accepted.append(
                {"segment_id": segment_id, "speaker_id": speaker_id}
            )
        self.calls.append({"segment_ids": sorted(known_ids)})
        return {
            "schema_version": "speaker-routing-v1",
            "assignments": accepted,
        }


class FakeTtsAdapter:
    """Record metadata-only calls and return deterministic fake output paths."""

    def __init__(self):
        self.calls: list[dict[str, str]] = []

    def synthesize(self, task: dict[str, Any], profile: dict[str, Any]) -> str:
        task_id = str(task["task_id"])
        profile_id = str(profile["profile_id"])
        self.calls.append({"task_id": task_id, "profile_id": profile_id})
        return f"audio/chunks/{task_id}.wav"
