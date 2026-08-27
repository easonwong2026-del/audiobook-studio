"""Project-local audio revision and artifact registration services."""
from __future__ import annotations

import hashlib
import os
import shutil
from typing import Any


from lib import project_paths, segment_cache
from lib.audio_validation import is_valid_wav_file
from lib.tts_profile import public_profile, resolve_profile
from repositories.project_repo import ProjectRepository
from repositories.quality_repo import QualityRepository


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _script_index(script: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(segment.get("id")): segment
        for chapter in script.get("chapters", [])
        if isinstance(chapter, dict)
        for segment in chapter.get("segments", [])
        if isinstance(segment, dict) and str(segment.get("id") or "").strip()
    }


class QualityService:
    """Project-local audio revision façade shared by Web and MCP adapters."""

    @staticmethod
    def _project_relative(project_name: str, path: str) -> str:
        project_dir = os.path.realpath(ProjectRepository.get_project_dir(project_name))
        return project_paths.make_relative(project_dir, path)

    @staticmethod
    def _absolute(project_name: str, relative_path: str) -> str:
        project_dir = os.path.realpath(ProjectRepository.get_project_dir(project_name))
        return project_paths.resolve_relative(project_dir, relative_path)

    @staticmethod
    def _segment(project_name: str, segment_id: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        meta, script, _bindings = ProjectRepository.load_project(project_name)
        segment = _script_index(script).get(str(segment_id))
        if segment is None:
            raise KeyError(f"段落不存在: {segment_id}")
        return meta, script, segment

    @staticmethod
    def _speaker_path(
        project_name: str,
        segment: dict[str, Any],
        bindings_document: dict[str, Any],
    ) -> str:
        project_dir = ProjectRepository.get_project_dir(project_name)
        role_bindings = (
            bindings_document.get("role_bindings", {})
            if isinstance(bindings_document, dict) else {}
        )
        binding = role_bindings.get(str(segment.get("role_id") or ""))
        if not isinstance(binding, dict):
            role_name = str(segment.get("role") or segment.get("speaker") or "")
            binding = next(
                (
                    item for item in role_bindings.values()
                    if isinstance(item, dict) and item.get("role_name") == role_name
                ),
                None,
            )
        path = ""
        if isinstance(binding, dict):
            path = str(binding.get("project_voice_path") or "")
        if not path and isinstance(bindings_document, dict):
            path = str(
                bindings_document.get("bindings", {}).get(
                    str(segment.get("role") or segment.get("speaker") or "")
                ) or ""
            )
        if path and not os.path.isabs(path):
            try:
                path = project_paths.resolve_relative(project_dir, path)
            except ValueError:
                path = os.path.join(project_dir, path)
        return path

    @classmethod
    def _identity(
        cls,
        project_name: str,
        segment: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
        speaker_override: str | None = None,
        bindings_document: dict[str, Any] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        if bindings_document is None:
            _meta, _script, bindings = ProjectRepository.load_project(project_name)
        else:
            bindings = bindings_document
        effective = {
            "emotion": segment.get("emotion", "neutral"),
            "emo_alpha": segment.get("emo_alpha", 1.0),
            "speech_rate": segment.get("speech_rate", 1.0),
            "pinyin_hints": segment.get("pinyin_hints"),
            "director_metadata": segment_cache.director_metadata_for(segment),
        }
        supplied_engine = (params or {}).get("engine_snapshot") if isinstance(params, dict) else None
        if supplied_engine:
            effective["engine_snapshot"] = public_profile(resolve_profile(supplied_engine))
        for key, value in (params or {}).items():
            if value is not None and key in effective:
                if key == "engine_snapshot":
                    effective[key] = public_profile(resolve_profile(value))
                else:
                    effective[key] = value
        speaker = str(speaker_override or "") or cls._speaker_path(
            project_name, segment, bindings
        )
        fingerprint = segment_cache.speaker_fingerprint_for_path(speaker) or ""
        cast_active = os.path.isfile(
            project_paths.project_file(
                ProjectRepository.get_project_dir(project_name), "voice_cast"
            )
        )
        cache_identity = segment_cache.segment_cache_key(
            str(segment.get("id")),
            str(effective["emotion"] or "neutral"),
            effective["emo_alpha"],
            effective["speech_rate"],
            effective["pinyin_hints"],
            effective["director_metadata"],
            fingerprint if cast_active or speaker_override else None,
            (effective.get("engine_snapshot") or {}).get("cache_identity")
            or (effective.get("engine_snapshot") or {}).get("engine_identity"),
        )
        return cache_identity, fingerprint, effective

    @classmethod
    def expected_audio_path(
        cls,
        project_name: str,
        segment_id: str,
        *,
        params: dict[str, Any] | None = None,
        speaker_override: str | None = None,
    ) -> tuple[str, str, str, dict[str, Any]]:
        _meta, _script, segment = cls._segment(project_name, segment_id)
        cache_identity, fingerprint, effective = cls._identity(
            project_name,
            segment,
            params=params,
            speaker_override=speaker_override,
        )
        project_dir = ProjectRepository.get_project_dir(project_name)
        segments_dir = project_paths.project_dir(project_dir, "segments", create=True)
        return (
            os.path.join(segments_dir, f"{cache_identity}.wav"),
            cache_identity,
            fingerprint,
            effective,
        )

    @classmethod
    def _find_script_audio(
        cls, project_name: str, segment: dict[str, Any]
    ) -> tuple[str | None, str, str, dict[str, Any]]:
        path, cache_identity, fingerprint, effective = cls.expected_audio_path(
            project_name, str(segment.get("id"))
        )
        if os.path.isfile(path):
            return path, cache_identity, fingerprint, effective
        project_dir = ProjectRepository.get_project_dir(project_name)
        cast_active = os.path.isfile(
            project_paths.project_file(project_dir, "voice_cast")
        )
        artifact = segment_cache.resolve_segment_artifact(
            segments_dir=project_paths.project_dir(project_dir, "segments"),
            seg_id=str(segment.get("id")),
            emotion=str(effective["emotion"] or "neutral"),
            emo_alpha=effective["emo_alpha"],
            speech_rate=effective["speech_rate"],
            pinyin_hints=effective["pinyin_hints"],
            director_metadata=effective["director_metadata"],
            # Legacy projects (no voice_cast.json) stay speaker-agnostic so the
            # resolver's non-strict fallback can still match bare/param files.
            speaker_fingerprint=(fingerprint or None) if cast_active else None,
            engine_snapshot=(effective.get("engine_snapshot") or None),
            project_name=project_name,
        )
        resolved = artifact.path if artifact.exists() else None
        if resolved is not None:
            # Record the actual engine provenance of the resolved file so the
            # revision's params/cache_identity match the real artifact instead
            # of a Settings-based guess (segment-level provenance upgrade).
            # Only engine-aware files carry provenance; pre-engine legacy
            # files (param/bare) keep their historical identity untouched.
            if (
                artifact.engine_provenance
                and artifact.matched_class == "engine_aware"
                and not effective.get("engine_snapshot")
            ):
                effective = dict(effective)
                effective["engine_snapshot"] = artifact.engine_provenance
                cache_identity = segment_cache.segment_cache_key(
                    str(segment.get("id")),
                    str(effective["emotion"] or "neutral"),
                    effective["emo_alpha"],
                    effective["speech_rate"],
                    effective["pinyin_hints"],
                    effective["director_metadata"],
                    fingerprint or None,
                    (
                        artifact.engine_provenance.get("cache_identity")
                        or artifact.engine_provenance.get("engine_identity")
                    ),
                )
        return resolved, cache_identity, fingerprint, effective

    @classmethod
    def _matching_pending_repair_revision(
        cls,
        project_name: str,
        segment_id: str,
        cache_identity: str,
        voice_fingerprint: str,
    ) -> dict[str, Any] | None:
        for revision in QualityRepository.list_revisions(project_name, segment_id):
            metadata = revision.get("metadata")
            if revision.get("status") != "regenerating" or not isinstance(metadata, dict):
                continue
            if not metadata.get("repair_id"):
                continue
            if str(revision.get("cache_identity") or "") != cache_identity:
                continue
            if str(revision.get("voice_fingerprint") or "") != voice_fingerprint:
                continue
            return revision
        return None

    @classmethod
    def ensure_active_revision(
        cls,
        project_name: str,
        segment_id: str,
        *,
        engine_snapshot: dict[str, Any] | None = None,
        source_path: str | None = None,
        params: dict[str, Any] | None = None,
        speaker_override: str | None = None,
    ) -> dict[str, Any] | None:
        active = QualityRepository.get_active_revision(project_name, segment_id)
        if active:
            try:
                active_path = cls._absolute(project_name, active.get("relative_path", ""))
                active_engine = (
                    active.get("params", {}).get("engine_snapshot")
                    if isinstance(active.get("params"), dict) else None
                )
                requested_identity = (
                    public_profile(resolve_profile(engine_snapshot)).get("cache_identity")
                    if engine_snapshot else None
                )
                active_identity = (
                    public_profile(resolve_profile(active_engine)).get("cache_identity")
                    if active_engine else None
                )
                source_matches = True
                if source_path:
                    source_matches = os.path.realpath(active_path) == os.path.realpath(
                        os.path.abspath(str(source_path))
                    )
                if os.path.isfile(active_path) and source_matches and (
                    not requested_identity or active_identity == requested_identity
                ):
                    return active
            except ValueError:
                pass
        _meta, _script, segment = cls._segment(project_name, segment_id)
        if source_path:
            path = os.path.abspath(str(source_path))
            project_dir = ProjectRepository.get_project_dir(project_name)
            try:
                if os.path.commonpath((os.path.realpath(path), os.path.realpath(project_dir))) != os.path.realpath(project_dir):
                    return None
            except (OSError, ValueError):
                return None
            identity, fingerprint, effective = cls._identity(
                project_name,
                segment,
                params=params or ({"engine_snapshot": engine_snapshot} if engine_snapshot else None),
                speaker_override=speaker_override,
            )
            params = effective
        elif engine_snapshot:
            path, identity, fingerprint, effective = cls.expected_audio_path(
                project_name,
                segment_id,
                params=params or {"engine_snapshot": engine_snapshot},
                speaker_override=speaker_override,
            )
            if not os.path.isfile(path):
                return None
        else:
            path, identity, fingerprint, effective = cls._find_script_audio(project_name, segment)
            params = effective
        if not path or not os.path.isfile(path):
            return None
        pending = cls._matching_pending_repair_revision(
            project_name, segment_id, identity, fingerprint
        )
        if pending:
            return cls.register_completed_revision(
                project_name,
                str(pending["revision_id"]),
                path,
            )
        return QualityRepository.create_revision(
            project_name,
            segment_id,
            relative_path=cls._project_relative(project_name, path),
            cache_identity=identity,
            voice_fingerprint=fingerprint,
            params=effective,
            status="ready",
            activate=True,
            metadata={"sha256": _sha256(path), "origin": "legacy_or_production"},
        )

    @classmethod
    def archive_active_revision(
        cls, project_name: str, segment_id: str
    ) -> dict[str, Any] | None:
        active = cls.ensure_active_revision(project_name, segment_id)
        if not active:
            return None
        source = cls._absolute(project_name, active.get("relative_path", ""))
        if not os.path.isfile(source):
            return active
        project_dir = ProjectRepository.get_project_dir(project_name)
        quality_dir = project_paths.project_dir(project_dir, "quality", create=True)
        archive_dir = os.path.join(quality_dir, "revisions", str(segment_id))
        os.makedirs(archive_dir, exist_ok=True)
        target = os.path.join(
            archive_dir,
            f"{active['revision_id']}_{os.path.basename(source)}",
        )
        if os.path.realpath(source) != os.path.realpath(target) and not os.path.isfile(target):
            shutil.copy2(source, target)
        if os.path.isfile(target):
            return QualityRepository.update_revision(
                project_name,
                active["revision_id"],
                relative_path=cls._project_relative(project_name, target),
                metadata={
                    **dict(active.get("metadata") or {}),
                    "sha256": _sha256(target),
                    "archived": True,
                },
            )
        return active

    @classmethod
    def register_completed_revision(
        cls,
        project_name: str,
        revision_id: str,
        audio_path: str,
        *,
        source_task_id: str = "",
    ) -> dict[str, Any]:
        revision = QualityRepository.get_revision(project_name, revision_id)
        if not revision:
            raise KeyError(f"音频 revision 不存在: {revision_id}")
        if not is_valid_wav_file(audio_path):
            raise ValueError("新 revision 音频不存在、为空或不是有效 WAV")
        project_dir = ProjectRepository.get_project_dir(project_name)
        quality_dir = project_paths.project_dir(project_dir, "quality", create=True)
        archive_dir = os.path.join(
            quality_dir, "revisions", str(revision.get("segment_id") or "")
        )
        os.makedirs(archive_dir, exist_ok=True)
        target = os.path.join(
            archive_dir,
            f"{revision_id}_{os.path.basename(audio_path)}",
        )
        if os.path.realpath(audio_path) != os.path.realpath(target):
            shutil.copy2(audio_path, target)
        return QualityRepository.update_revision(
            project_name,
            revision_id,
            activate=True,
            relative_path=cls._project_relative(project_name, target),
            source_task_id=source_task_id or revision.get("source_task_id", ""),
            status="ready",
            metadata={
                **dict(revision.get("metadata") or {}),
                "sha256": _sha256(target),
                "size": os.path.getsize(target),
            },
        )

    @classmethod
    def resolve_active_audio(
        cls, project_name: str, segment_id: str
    ) -> str | None:
        revision = cls.ensure_active_revision(project_name, segment_id)
        if not revision:
            return None
        try:
            path = cls._absolute(project_name, revision.get("relative_path", ""))
        except ValueError:
            return None
        return path if is_valid_wav_file(path) else None

    @staticmethod
    def _revision_snapshot(revision: dict[str, Any]) -> dict[str, Any]:
        return {
            key: revision.get(key)
            for key in (
                "revision_id",
                "audio_revision",
                "relative_path",
                "cache_identity",
                "voice_fingerprint",
                "params",
                "source_task_id",
                "status",
                "created_at",
                "metadata",
            )
        }

    @classmethod
    def get_active_revision_inventory(cls, project_name: str) -> dict[str, Any]:
        """Return active revision facts while bootstrapping legacy audio."""
        _meta, script, _bindings = ProjectRepository.load_project(project_name)
        segments = _script_index(script)
        state = QualityRepository.load(project_name)
        candidates: list[dict[str, Any]] = []

        for segment_id, segment in segments.items():
            if state["active_revisions"].get(segment_id):
                continue
            path, identity, fingerprint, effective = cls._find_script_audio(
                project_name, segment
            )
            if path and is_valid_wav_file(path):
                candidates.append({
                    "segment_id": segment_id,
                    "relative_path": cls._project_relative(project_name, path),
                    "cache_identity": identity,
                    "voice_fingerprint": fingerprint,
                    "params": effective,
                    "status": "ready",
                    "metadata": {
                        "sha256": _sha256(path),
                        "origin": "legacy_or_production",
                    },
                })
        if candidates:
            QualityRepository.bootstrap_revisions(project_name, candidates)
            state = QualityRepository.load(project_name)

        items: list[dict[str, Any]] = []
        for segment_id in segments:
            revision_id = state["active_revisions"].get(segment_id)
            revision = state["revisions"].get(revision_id)
            revision = revision if isinstance(revision, dict) else None
            relative_path = str(revision.get("relative_path") or "") if revision else ""
            audio_path: str | None = None
            if relative_path:
                try:
                    audio_path = cls._absolute(project_name, relative_path)
                except ValueError:
                    audio_path = None
            audio_exists = bool(audio_path and os.path.isfile(audio_path))
            audio_valid = is_valid_wav_file(audio_path) if audio_exists else False
            raw_params = revision.get("params") if revision else None
            raw_engine = (
                raw_params.get("engine_snapshot")
                if isinstance(raw_params, dict)
                else None
            )
            if isinstance(raw_engine, dict) and raw_engine:
                try:
                    engine_snapshot = public_profile(resolve_profile(raw_engine))
                except (TypeError, ValueError):
                    engine_snapshot = dict(raw_engine)
            else:
                engine_snapshot = {}
            metadata = revision.get("metadata") if revision else None
            metadata = metadata if isinstance(metadata, dict) else {}
            checksum = str(metadata.get("sha256") or "")
            if not checksum and audio_valid and audio_path:
                checksum = _sha256(audio_path)
            items.append({
                "segment_id": segment_id,
                "audio_revision": (
                    cls._revision_snapshot(revision) if revision else None
                ),
                "relative_path": relative_path,
                "audio_path": audio_path,
                "audio_exists": audio_exists,
                "audio_valid": audio_valid,
                "cache_identity": str(revision.get("cache_identity") or "")
                if revision else "",
                "voice_fingerprint": str(revision.get("voice_fingerprint") or "")
                if revision else "",
                "engine_snapshot": engine_snapshot,
                "engine_provenance": engine_snapshot,
                "checksum": checksum,
                "audio_status": (
                    "valid" if audio_valid
                    else "invalid" if audio_exists
                    else "missing"
                ),
            })

        return {
            "project": project_name,
            "segments": items,
            "summary": {
                "segments": len(items),
                "active_revisions": sum(
                    1 for item in items if item["audio_revision"]
                ),
                "valid_audio": sum(1 for item in items if item["audio_valid"]),
                "missing_revisions": sum(
                    1 for item in items if not item["audio_revision"]
                ),
                "invalid_audio": sum(
                    1
                    for item in items
                    if item["audio_exists"] and not item["audio_valid"]
                ),
            },
        }


__all__ = ["QualityService"]
