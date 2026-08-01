"""Declarative production models and synthesis-plan records."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ValidationError


@dataclass(frozen=True)
class VoiceBinding:
    voice_id: str
    fingerprint: str


@dataclass(frozen=True)
class VoiceBindings:
    bindings: dict[str, VoiceBinding]
    revision: int = 1
    schema_version: str = "audiobook-voices-v1"

    def validate(self) -> None:
        if self.schema_version != "audiobook-voices-v1" or self.revision < 1:
            raise ValidationError("invalid voices document")
        for speaker_id, binding in self.bindings.items():
            if not speaker_id or not binding.voice_id or not binding.fingerprint:
                raise ValidationError("invalid voice binding")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "bindings": {
                key: asdict(value) for key, value in sorted(self.bindings.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceBindings:
        try:
            value = cls(
                bindings={
                    key: VoiceBinding(**item)
                    for key, item in data["bindings"].items()
                },
                revision=data["revision"],
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValidationError(f"invalid voices document: {exc}") from exc
        value.validate()
        return value


@dataclass(frozen=True)
class PerformanceOverrides:
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    revision: int = 1
    schema_version: str = "audiobook-performance-v1"

    def validate(self) -> None:
        if self.schema_version != "audiobook-performance-v1" or self.revision < 1:
            raise ValidationError("invalid performance document")
        for segment_id, override in self.overrides.items():
            if not segment_id or override.get("emotion_mode") != "manual":
                raise ValidationError("performance overrides must be explicit manual values")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceOverrides:
        try:
            value = cls(
                overrides=data["overrides"],
                revision=data["revision"],
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid performance document: {exc}") from exc
        value.validate()
        return value


@dataclass(frozen=True)
class PronunciationRules:
    global_rules: dict[str, str] = field(default_factory=dict)
    segments: dict[str, dict[str, str]] = field(default_factory=dict)
    revision: int = 1
    schema_version: str = "audiobook-pronunciation-v1"

    def validate(self) -> None:
        if self.schema_version != "audiobook-pronunciation-v1" or self.revision < 1:
            raise ValidationError("invalid pronunciation document")
        if any(not source or not target for source, target in self.global_rules.items()):
            raise ValidationError("empty global pronunciation rule")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "global": self.global_rules,
            "segments": self.segments,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PronunciationRules:
        try:
            value = cls(
                global_rules=data["global"],
                segments=data["segments"],
                revision=data["revision"],
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid pronunciation document: {exc}") from exc
        value.validate()
        return value


@dataclass(frozen=True)
class TextLimits:
    preferred: int
    maximum: int
    absolute: int
    minimum: int
    metric: str = "tokens"
    fallback_chars: int = 70
    max_fallback_chars: int = 100

    def validate(self) -> None:
        if self.metric not in {"characters", "tokens", "phonemes", "estimated_seconds"}:
            raise ValidationError("unsupported text measurement metric")
        if not 0 < self.minimum <= self.preferred <= self.maximum <= self.absolute:
            raise ValidationError("invalid text limits")


@dataclass(frozen=True)
class TtsProfile:
    profile_id: str
    engine: str
    limits: TextLimits
    revision: int = 1
    status: str = "provisional-unbenchmarked"
    model_version: str = ""
    hardware_profile: str = "auto"
    hardware: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    emotion: dict[str, Any] = field(default_factory=dict)
    runtime_options: dict[str, Any] = field(default_factory=dict)
    allow_merge: bool = True
    split_priority: list[str] = field(
        default_factory=lambda: [
            "paragraph", "sentence", "semicolon", "comma", "colon", "safe_character"
        ]
    )
    max_retry_split_depth: int = 3
    concurrency: int = 1
    oom_retry: bool = True
    clear_cache_after_oom: bool = True
    schema_version: str = "audiobook-tts-profile-v1"

    def validate(self) -> None:
        if self.schema_version != "audiobook-tts-profile-v1" or self.revision < 1:
            raise ValidationError("invalid TTS profile")
        if not self.profile_id or not self.engine or self.concurrency != 1:
            raise ValidationError("invalid TTS engine profile")
        self.limits.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "profile_id": self.profile_id,
            "status": self.status,
            "engine": self.engine,
            "model_version": self.model_version,
            "hardware_profile": self.hardware_profile,
            "hardware": self.hardware,
            "options": self.options,
            "limits": asdict(self.limits),
            "splitting": {
                "allow_merge": self.allow_merge,
                "split_priority": self.split_priority,
                "max_retry_split_depth": self.max_retry_split_depth,
            },
            "emotion": self.emotion,
            "runtime": {
                **self.runtime_options,
                "concurrency": self.concurrency,
                "oom_retry": self.oom_retry,
                "clear_cuda_cache_after_oom": self.clear_cache_after_oom,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TtsProfile:
        try:
            limits = data["limits"]
            splitting = data["splitting"]
            runtime = data["runtime"]
            value = cls(
                profile_id=data["profile_id"],
                engine=data["engine"],
                limits=TextLimits(**limits),
                revision=data["revision"],
                status=data.get("status", "provisional-unbenchmarked"),
                model_version=data.get("model_version", ""),
                hardware_profile=data.get("hardware_profile", "auto"),
                hardware=data.get("hardware", {}),
                options=data.get("options", {}),
                emotion=data.get("emotion", {}),
                runtime_options={
                    key: value
                    for key, value in runtime.items()
                    if key
                    not in {
                        "concurrency",
                        "oom_retry",
                        "clear_cache_after_oom",
                        "clear_cuda_cache_after_oom",
                    }
                },
                allow_merge=splitting["allow_merge"],
                split_priority=list(splitting["split_priority"]),
                max_retry_split_depth=splitting["max_retry_split_depth"],
                concurrency=runtime["concurrency"],
                oom_retry=runtime["oom_retry"],
                clear_cache_after_oom=runtime.get(
                    "clear_cuda_cache_after_oom",
                    runtime.get("clear_cache_after_oom", True),
                ),
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid TTS profile: {exc}") from exc
        value.validate()
        return value


@dataclass(frozen=True)
class PlanPart:
    segment_id: str
    source_start: int
    source_end: int


@dataclass(frozen=True)
class PlanTask:
    task_id: str
    chapter_id: str
    speaker_id: str
    voice_id: str
    source_segments: list[str]
    parts: list[PlanPart]
    actual_text: str
    text_length: int
    continuation: bool
    pause_before_ms: int
    pause_after_ms: int
    crossfade_ms: int
    input_fingerprint: str
    merge_group_fingerprint: str
    merge_allowed: bool


@dataclass(frozen=True)
class PlanDependencies:
    source_sha256: str
    script_revision: int
    voices_revision: int
    performance_revision: int
    pronunciation_revision: int
    tts_profile_revision: int


@dataclass(frozen=True)
class SynthesisPlan:
    revision: int
    dependencies: PlanDependencies
    tasks: list[PlanTask]
    schema_version: str = "audiobook-synthesis-plan-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynthesisPlan:
        try:
            tasks = [
                PlanTask(
                    **{
                        **item,
                        "parts": [PlanPart(**part) for part in item["parts"]],
                    }
                )
                for item in data["tasks"]
            ]
            value = cls(
                revision=data["revision"],
                dependencies=PlanDependencies(**data["dependencies"]),
                tasks=tasks,
                schema_version=data["schema_version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"invalid synthesis plan: {exc}") from exc
        if value.schema_version != "audiobook-synthesis-plan-v1" or value.revision < 1:
            raise ValidationError("invalid synthesis plan")
        return value
