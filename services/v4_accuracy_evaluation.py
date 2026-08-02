"""Small, fixture-friendly accuracy evaluator for V4 analysis results."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from domain.v4 import ScriptDocument, SpeakersDocument


@dataclass(frozen=True)
class V4AccuracyMetrics:
    correct_roles: int
    auto_confirmed_roles: int
    correct_dialogue: int
    auto_assigned_dialogue: int
    total_dialogue: int
    role_accuracy: float
    dialogue_accuracy: float
    auto_coverage: float
    error_categories: dict[str, int]
    true_role_count: int = 0
    false_positive_roles: int = 0
    missed_roles: int = 0
    alias_merge_correct: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "correct_roles": self.correct_roles,
            "auto_confirmed_roles": self.auto_confirmed_roles,
            "correct_dialogue": self.correct_dialogue,
            "auto_assigned_dialogue": self.auto_assigned_dialogue,
            "total_dialogue": self.total_dialogue,
            "role_accuracy": self.role_accuracy,
            "dialogue_accuracy": self.dialogue_accuracy,
            "auto_coverage": self.auto_coverage,
            "error_categories": dict(self.error_categories),
            "true_role_count": self.true_role_count,
            "false_positive_roles": self.false_positive_roles,
            "missed_roles": self.missed_roles,
            "alias_merge_correct": self.alias_merge_correct,
        }


def evaluate_v4_accuracy(
    speakers: SpeakersDocument,
    script: ScriptDocument,
    ground_truth: dict[str, Any],
    *,
    auto_confirmed_speaker_ids: set[str] | None = None,
) -> V4AccuracyMetrics:
    """Compare persisted results with a tiny local ground-truth fixture.

    Fixture shape::

        {"characters": ["周建国"],
         "dialogue": {"segment_000001": "speaker_id-or-name"}}

    ``auto_confirmed_speaker_ids`` may be supplied by a pipeline report.  When
    omitted, all confirmed character speakers are treated as the measured set.
    """
    speakers.validate()
    by_id = {item.speaker_id: item for item in speakers.speakers}
    by_name = {
        value: item
        for item in speakers.speakers
        for value in [item.display_name, *item.aliases]
    }
    role_specs: list[tuple[str, set[str]]] = []
    for item in (ground_truth.get("characters") or []):
        if isinstance(item, dict):
            canonical = str(
                item.get("canonical_name") or item.get("name") or ""
            ).strip()
            aliases = {
                str(value).strip()
                for value in (item.get("aliases") or [])
                if str(value).strip()
            }
        else:
            canonical = str(item).strip()
            aliases = set()
        if canonical:
            role_specs.append((canonical, {canonical, *aliases}))
    measured_ids = auto_confirmed_speaker_ids
    if measured_ids is None:
        measured_ids = {
            item.speaker_id
            for item in speakers.speakers
            if item.speaker_type == "character" and item.status == "confirmed"
        }
    measured_roles = [by_id[item] for item in measured_ids if item in by_id]
    unmatched_expected = set(range(len(role_specs)))
    correct_roles = 0
    alias_merge_correct = 0
    for item in measured_roles:
        names = {item.display_name, *item.aliases}
        match = next(
            (
                index
                for index in sorted(unmatched_expected)
                if names & role_specs[index][1]
            ),
            None,
        )
        if match is None:
            continue
        unmatched_expected.remove(match)
        correct_roles += 1
        expected_name, expected_names = role_specs[match]
        if len(expected_names) > 1 and names & (expected_names - {expected_name}):
            alias_merge_correct += 1
    true_role_count = len(role_specs)
    false_positive_roles = len(measured_roles) - correct_roles
    missed_roles = len(unmatched_expected)

    expected_dialogue = ground_truth.get("dialogue") or {}
    if not isinstance(expected_dialogue, dict):
        raise TypeError("ground_truth.dialogue must be an object")
    segment_lookup = {
        segment.segment_id: segment
        for chapter in script.chapters
        for segment in chapter.segments
    }
    correct_dialogue = 0
    auto_assigned = 0
    errors: Counter[str] = Counter()
    if false_positive_roles:
        errors["false_positive_role"] = false_positive_roles
    if missed_roles:
        errors["missed_role"] = missed_roles
    for segment_id, expected in expected_dialogue.items():
        segment = segment_lookup.get(str(segment_id))
        if segment is None:
            errors["missing_segment"] += 1
            continue
        if segment.status != "confirmed" or segment.speaker_id is None:
            errors["unresolved"] += 1
            continue
        if segment.speaker_source not in {"ai", "rule", "router"}:
            errors["manual_assignment"] += 1
            continue
        auto_assigned += 1
        expected_id = by_id.get(str(expected))
        if expected_id is None:
            expected_id = by_name.get(str(expected).strip())
        if expected_id and segment.speaker_id == expected_id.speaker_id:
            correct_dialogue += 1
        else:
            errors["wrong_speaker"] += 1

    auto_confirmed_roles = len(measured_roles)
    total_dialogue = len(expected_dialogue)
    return V4AccuracyMetrics(
        correct_roles=correct_roles,
        auto_confirmed_roles=auto_confirmed_roles,
        correct_dialogue=correct_dialogue,
        auto_assigned_dialogue=auto_assigned,
        total_dialogue=total_dialogue,
        role_accuracy=(correct_roles / auto_confirmed_roles) if auto_confirmed_roles else 0.0,
        dialogue_accuracy=(correct_dialogue / auto_assigned) if auto_assigned else 0.0,
        auto_coverage=(auto_assigned / total_dialogue) if total_dialogue else 0.0,
        error_categories=dict(errors),
        true_role_count=true_role_count,
        false_positive_roles=false_positive_roles,
        missed_roles=missed_roles,
        alias_merge_correct=alias_merge_correct,
    )
