"""段缓存键推导（B7：缓存键改内容哈希）。

把「段标识 + 合成参数（emotion / emo_alpha / speech_rate / pinyin_hints /
director_metadata）+ speaker fingerprint」
组合哈希，得到 ``segments/`` 目录下唯一的 wav 文件名。

设计要点：
- 参数一变 → 缓存键变 → 文件名变 → 旧文件天然不被命中，触发重新合成
  （解决 REVIEW B7：批量模式下改情感 / 语速 / 多音字后重跑不重合成的问题）。
- 无需维护额外元数据文件，文件名即缓存键。
- 读取侧（``find_segment_wav`` / ``has_segment_wav``）在找不到参数感知文件时，
  对未启用 speaker fingerprint 的旧项目回退到旧版裸 ``{seg_id}.wav``；
  Voice Cast 项目使用严格 speaker-aware lookup，避免换声后误用旧音频。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional


def segment_cache_key(
    seg_id: str,
    emotion: str,
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    pinyin_hints: Any = None,
    director_metadata: Any = None,
    speaker_fingerprint: str | None = None,
    engine_identity: str | None = None,
) -> str:
    """由段标识 + 合成参数派生稳定缓存键（不含扩展名）。

    Args:
        seg_id: 段唯一标识（如 ``"1-001"``）。
        emotion: 情感标签。
        emo_alpha: 情绪强度，默认 1.0。
        speech_rate: 语速，默认 1.0。
        pinyin_hints: 多音字提示 dict，默认 None。
        director_metadata: v3 停顿、呼吸和音高 metadata；v2 缺省为 None，
            保持旧缓存键完全不变。
        speaker_fingerprint: Voice Cast 音色内容哈希；缺省时保持 legacy
            speaker-agnostic cache contract。

    Returns:
        ``{seg_id}_{md5前8位}`` 形式的缓存键。

    Note:
        ``pinyin_hints`` 做归一化——空 dict ``{}`` / ``None`` / 空串等 falsy 值
        等价（统一视为 ``None``）。原因：``script_loader`` 对缺省字段默认给 ``{}``，
        而导出侧（raw JSON）缺省为 ``None``，若不复用同一表征会让两侧缓存键不同，
        导致导出查不到文件而丢段（B7 导出丢段根因修复）。
    """
    # 归一化：缺省 pinyin_hints 的 {} 与 None 必须等价
    if not pinyin_hints:
        pinyin_hints = None
    if not director_metadata:
        director_metadata = None
    elif not isinstance(director_metadata, str):
        director_metadata = json.dumps(
            director_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    params = f"{emotion}|{emo_alpha}|{speech_rate}|{pinyin_hints}"
    if director_metadata is not None:
        params += f"|director={director_metadata}"
    # Voice Cast projects must never reuse a segment rendered with another
    # speaker.  Keep this optional so pre-Phase-2 projects retain their exact
    # historical cache names and fallback behavior.
    if speaker_fingerprint:
        params += f"|speaker={str(speaker_fingerprint).strip()}"
    if engine_identity:
        params += f"|engine={str(engine_identity).strip()}"
    digest = hashlib.md5(params.encode("utf-8")).hexdigest()[:8]
    return f"{seg_id}_{digest}"


def segment_wav_path(
    segments_dir: str,
    seg_id: str,
    emotion: str,
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    pinyin_hints: Any = None,
    director_metadata: Any = None,
    speaker_fingerprint: str | None = None,
    engine_identity: str | None = None,
) -> str:
    """返回参数感知的 wav 绝对路径（缓存键命名）。"""
    key = segment_cache_key(
        seg_id, emotion, emo_alpha, speech_rate, pinyin_hints, director_metadata,
        speaker_fingerprint, engine_identity,
    )
    return os.path.join(segments_dir, f"{key}.wav")


def find_segment_wav(
    segments_dir: str,
    seg_id: str,
    text: str,
    role: str,
    emotion: str,
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    pinyin_hints: Any = None,
    director_metadata: Any = None,
    speaker_fingerprint: str | None = None,
    allow_legacy_fallback: bool | None = None,
    engine_identity: str | None = None,
) -> Optional[str]:
    """查找某段已合成的 wav。

    命中优先级：
    1. 参数感知的缓存键文件（含参数哈希）—— 升级后新写入，参数变即失效旧文件。
    2. 旧版裸 ``{seg_id}.wav`` —— 兼容升级前的历史项目。

    Returns:
        wav 路径；都未命中时返回 None。
    """
    # 1) 参数感知的缓存键文件
    ck = segment_cache_key(
        seg_id, emotion, emo_alpha, speech_rate, pinyin_hints, director_metadata,
        speaker_fingerprint, engine_identity,
    )
    fp = os.path.join(segments_dir, f"{ck}.wav")
    if os.path.isfile(fp):
        return fp
    # A speaker fingerprint opts into strict lookup.  Falling through to a
    # speaker-agnostic file here would silently play the previous actor after a
    # cast change.  Callers that explicitly want a compatibility lookup can
    # pass allow_legacy_fallback=True.
    if allow_legacy_fallback is None:
        allow_legacy_fallback = speaker_fingerprint is None and not engine_identity
    if not allow_legacy_fallback:
        return None

    # 2) 旧版裸文件（未升级前的命名），用于兼容历史项目
    legacy = os.path.join(segments_dir, f"{seg_id}.wav")
    if os.path.isfile(legacy):
        return legacy
    return None


def has_segment_wav(
    segments_dir: str,
    seg_id: str,
    emotion: str = "neutral",
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    pinyin_hints: Any = None,
    director_metadata: Any = None,
    speaker_fingerprint: str | None = None,
    engine_identity: str | None = None,
) -> bool:
    """某段是否已存在对应 wav（参数感知文件 / 旧版裸文件 / 任意参数变体均可）。

    用于 ``project_manager`` 状态机判断「标记 done 的段是否真的有音频文件」，
    因状态表层只存 ``seg_id``、不存参数，故同时匹配：
    1) 给定参数的缓存键文件；
    2) 旧版裸 ``{seg_id}.wav``；
    3) 任意 ``{seg_id}_*.wav`` 变体（参数未知时也能识别）。
    """
    ck = segment_cache_key(
        seg_id, emotion, emo_alpha, speech_rate, pinyin_hints, director_metadata,
        speaker_fingerprint, engine_identity,
    )
    if os.path.isfile(os.path.join(segments_dir, f"{ck}.wav")):
        return True
    if speaker_fingerprint or engine_identity:
        return False
    if os.path.isfile(os.path.join(segments_dir, f"{seg_id}.wav")):
        return True
    return any(
        name.startswith(f"{seg_id}_") and name.endswith(".wav")
        for name in os.listdir(segments_dir)
    ) if os.path.isdir(segments_dir) else False


def speaker_fingerprint_for_path(path: str | None) -> str | None:
    """Return the content fingerprint used by Voice Cast-aware caches.

    The full SHA-256 is intentionally used instead of mtime or an absolute
    path.  Project snapshots therefore keep the same cache identity after a
    restart or a global voice-library move, while replacing the audio creates
    a new identity.
    """
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


class SpeakerEmbeddingLRU:
    """有界 LRU 缓存：键=参考音频路径（或内容哈希），值=embedding（任意对象，通常为 tensor）。

    满足 2.4 T-2「embedding LRU 上限」：超出 ``maxsize`` 时自动淘汰最久未用，
    防止 speaker embedding 随角色数线性膨胀占用显存 / 内存。

    使用标准库 ``collections.OrderedDict`` 自实现淘汰策略，不引入第三方依赖。
    """

    def __init__(self, maxsize: int = 16) -> None:
        """初始化有界 LRU 容器。

        Args:
            maxsize: 最大缓存条目数，至少 1。
        """
        self.maxsize = max(1, int(maxsize))
        self._store: "OrderedDict[str, object]" = OrderedDict()

    def get(self, key: str):
        """取缓存；命中则标记为最近使用（移到末尾）并返回值，未命中返回 None。"""
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: str, emb) -> None:
        """写入缓存；若已存在则刷新为最近使用，随后触发淘汰检查。"""
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = emb
        self.evict_if_needed()

    def evict_if_needed(self) -> None:
        """超出 maxsize 时淘汰队首（最久未用）条目。"""
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def pop(self, key: str, default=None):
        """Remove one cached value without changing the bounded-cache API."""
        return self._store.pop(key, default)

    def clear(self) -> None:
        """Drop all cached values (used when a cast is forcibly replaced)."""
        self._store.clear()


def effective_params(seg, overrides: dict) -> tuple:
    """根据全局覆盖 + 段落自身默认值推导有效合成参数三元组。

    用于 2.3 O2「合成期情感 / 语速全局覆盖」：保证批量合成（queue）、
    preview、export 使用同一套有效参数，从而派生一致的缓存键（§9.6）。

    Args:
        seg: 段落对象，至少含 ``emotion``，可选 ``emo_alpha`` / ``speech_rate`` /
            ``pinyin_hints``（通常来自 ``script_loader`` 解析的 ``ScriptSegment``，
            也兼容普通命名空间 / dict-like 对象）。
        overrides: 全局覆盖 dict（可由 ``project_manager.get_synthesis_overrides``
            读取），键：
            - ``emotion``: str 或 None（None=按剧本每段自身值）。
            - ``override``: bool，是否用全局 ``emo_alpha`` / ``speech_rate`` 覆盖。
            - ``emo_alpha`` / ``speech_rate``: float。

    Returns:
        ``(emotion, emo_alpha, speech_rate)`` 有效三元组。
    """
    overrides = overrides or {}
    # 情感：覆盖非 None 时优先（None 表示「按剧本」），否则沿用段落自身
    emotion = overrides.get("emotion") or getattr(seg, "emotion", "neutral")
    # alpha / rate：仅当 override=True 时采用全局值，否则用段落自身默认值
    if overrides.get("override"):
        emo_alpha = overrides.get("emo_alpha", getattr(seg, "emo_alpha", 1.0))
        speech_rate = overrides.get("speech_rate", getattr(seg, "speech_rate", 1.0))
    else:
        emo_alpha = getattr(seg, "emo_alpha", 1.0)
        speech_rate = getattr(seg, "speech_rate", 1.0)
    return emotion, emo_alpha, speech_rate


def director_metadata_for(seg) -> dict | None:
    """返回会影响 v3 音频结果、但不在旧缓存键中的导演字段。"""
    if isinstance(seg, dict):
        get = seg.get
        delivery = seg.get("delivery") if isinstance(seg.get("delivery"), dict) else {}
    else:
        get = lambda name, default=None: getattr(seg, name, default)
        delivery = {}
    metadata = {
        "pitch": delivery.get("pitch", get("pitch", 0.0)),
        "breath": delivery.get("breath", get("breath", "none")),
        "pause_before": get("pause_before", 0),
        "pause_after": get("pause_after", 0),
        "pauses": get("pauses", []) or [],
    }
    if (
        not metadata["pitch"]
        and metadata["breath"] in (None, "", "none")
        and not metadata["pause_before"]
        and not metadata["pause_after"]
        and not metadata["pauses"]
    ):
        return None
    return metadata


# ---------------------------------------------------------------------------
# Unified Segment Artifact resolution (双引擎正确性收口)
#
# 背景：生产端自双引擎起把 engine_identity/cache_identity 加入 segment cache
# key（文件名形如 ``{seg_id}_{md5(参数|speaker|engine)}.wav``），但读取链
# （Review / Chapter Preview / Export / QA / Repair）长期只按 speaker 查找，
# 导致 "音频已生成但试听/导出找不到"。本模块是唯一 artifact 解析入口：
#
#   - 权威 engine provenance 优先：调用方显式 engine_snapshot >
#     segment 级 active revision provenance > task 级 provenance >
#     Settings 当前默认（仅最后兜底，绝不覆盖历史真实 provenance）。
#   - 兼容 4 类历史项目：
#       A. 旧裸文件 ``001.wav``
#       B. 老参数缓存 ``001_abcd1234.wav``
#       C. Voice Cast speaker-aware ``001_<speaker>.wav``
#       D. 双引擎 engine-aware ``001_<speaker+engine>.wav``
#   - 解析结果带 resolution metadata，供测试与诊断核对。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentArtifact:
    """Resolved segment audio plus resolution metadata."""

    path: str | None
    seg_id: str
    matched_key: str = ""
    matched_class: str = ""  # engine_aware|speaker_aware|param_aware|legacy_bare|any_variant
    engine_provenance: dict[str, Any] = field(default_factory=dict)
    engine_source: str = "none"  # explicit|revision|task|settings_default|none
    speaker_fingerprint: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def exists(self) -> bool:
        return bool(self.path and os.path.isfile(self.path))


def _public_engine_snapshot(value: Any) -> dict[str, Any]:
    """Normalize a stored engine_snapshot into path-free identity fields."""
    if not isinstance(value, dict) or not value:
        return {}
    try:
        from .tts_profile import public_profile, resolve_profile

        return public_profile(resolve_profile(value))
    except (OSError, TypeError, ValueError, RuntimeError):
        return {}


def _task_engine_provenance(project_name: str) -> dict[str, Any]:
    """Return the newest production task's frozen engine snapshot (task level).

    A short TTL cache keeps per-segment review loops from re-reading the
    SQLite task table for every segment.  Tasks are durable and engine
    snapshots are frozen at creation, so a 10s cache cannot serve stale data
    that changes the correctness of a review/export lookup.  The cache key
    includes the data dir so parallel test workspaces never collide.
    """
    key = str(project_name or "").strip()
    if not key:
        return {}
    from . import config as _cfg

    cache_key = f"{_cfg.get_data_dir()}|{key}"
    now = time.monotonic()
    cached = _TASK_ENGINE_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _TASK_ENGINE_CACHE_TTL:
        return cached[1]
    try:
        from repositories.task_repo import TaskRepository

        records = TaskRepository.list_tasks(
            project=key or None,
            task_type="synthesis",
        )
    except Exception:
        return {}
    def _created_at(record: Any) -> str:
        return str(getattr(record, "created_at", "") or "")
    records.sort(key=_created_at, reverse=True)
    result: dict[str, Any] = {}
    for record in records:
        options = getattr(record, "options", None)
        if isinstance(options, dict) and isinstance(options.get("engine_snapshot"), dict):
            snapshot = _public_engine_snapshot(options["engine_snapshot"])
            if snapshot.get("cache_identity"):
                result = snapshot
                break
    _TASK_ENGINE_CACHE[cache_key] = (now, result)
    if len(_TASK_ENGINE_CACHE) > 128:
        for stale_key in [k for k, v in _TASK_ENGINE_CACHE.items() if now - v[0] >= _TASK_ENGINE_CACHE_TTL]:
            _TASK_ENGINE_CACHE.pop(stale_key, None)
    return result


# TTL cache for task-level engine provenance (see _task_engine_provenance).
_TASK_ENGINE_CACHE_TTL = 10.0
_TASK_ENGINE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def project_task_engine_snapshot(project_name: str) -> dict[str, Any]:
    """Public wrapper: newest production task's frozen engine snapshot."""
    return _task_engine_provenance(project_name)


def _revision_engine_provenance(project_name: str, seg_id: str) -> dict[str, Any]:
    """Return the segment's active revision engine snapshot (segment level)."""
    try:
        from repositories.quality_repo import QualityRepository

        revision = QualityRepository.get_active_revision(
            str(project_name or "").strip(), str(seg_id or "").strip()
        )
    except Exception:
        return {}
    if not isinstance(revision, dict):
        return {}
    params = revision.get("params")
    engine = params.get("engine_snapshot") if isinstance(params, dict) else None
    snapshot = _public_engine_snapshot(engine)
    if snapshot.get("cache_identity"):
        return snapshot
    return {}


def _engine_candidates(
    *,
    explicit_snapshot: dict[str, Any] | None,
    project_name: str | None,
    seg_id: str,
    include_settings_default: bool = True,
) -> list[tuple[dict[str, Any], str]]:
    """Return (engine_snapshot, source) candidates in priority order.

    An explicit snapshot is authoritative: when present it short-circuits the
    provenance queries so plan/start never mix a caller's frozen engine with
    task/revision history.  Without an explicit snapshot the resolver consults
    segment revision provenance, then task provenance, then Settings default.
    """
    from .tts_profile import resolve_profile

    candidates: list[tuple[dict[str, Any], str]] = []
    if explicit_snapshot and isinstance(explicit_snapshot, dict):
        profile = resolve_profile(explicit_snapshot)
        if profile.get("cache_identity"):
            candidates.append((profile, "explicit"))
        return candidates
    if project_name:
        revision_profile = _revision_engine_provenance(project_name, seg_id)
        if revision_profile.get("cache_identity"):
            candidates.append((revision_profile, "revision"))
        task_profile = _task_engine_provenance(project_name)
        if task_profile.get("cache_identity") and not any(
            item[0].get("cache_identity") == task_profile["cache_identity"]
            for item in candidates
        ):
            candidates.append((task_profile, "task"))
    if include_settings_default:
        settings_profile = resolve_profile({})
        if settings_profile.get("cache_identity") and not any(
            item[0].get("cache_identity") == settings_profile["cache_identity"]
            for item in candidates
        ):
            candidates.append((settings_profile, "settings_default"))
    return candidates


def resolve_segment_artifact(
    *,
    segments_dir: str,
    seg_id: str,
    emotion: str = "neutral",
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    pinyin_hints: Any = None,
    director_metadata: Any = None,
    speaker_fingerprint: str | None = None,
    engine_snapshot: dict[str, Any] | None = None,
    project_name: str | None = None,
    allow_legacy_fallback: bool | None = None,
    allow_any_variant: bool | None = None,
    include_settings_default: bool = True,
) -> SegmentArtifact:
    """Resolve the segment WAV using unified provenance-aware lookup.

    Engine provenance priority: explicit engine_snapshot > segment revision
    provenance > latest production task provenance > Settings default.  The
    Settings default is only ever a *candidate* and never overrides a real
    historical provenance, so yesterday's IndexTTS2 audio remains playable
    even after Settings switches to IndexTTS 2.5.

    Compatibility classes tried in order:
      1. engine-aware ``{seg_id}_{md5(...,speaker,engine)}.wav``
      2. speaker-aware ``{seg_id}_{md5(...,speaker)}.wav``
      3. param-aware ``{seg_id}_{md5(...)}.wav``
      4. legacy bare ``{seg_id}.wav``
      5. any ``{seg_id}_*.wav`` variant (only when explicitly allowed)

    ``allow_legacy_fallback`` gates the legacy bare file (defaults to enabled
    for non-strict lookups).  ``allow_any_variant`` independently gates the
    catch-all glob; plan accounting leaves it off so a file produced by a
    *different* engine is reported as remaining instead of completed.
    """
    candidates_tried: list[dict[str, Any]] = []
    strict = bool(speaker_fingerprint or engine_snapshot)
    if allow_legacy_fallback is None:
        allow_legacy_fallback = not strict
    if allow_any_variant is None:
        allow_any_variant = allow_legacy_fallback

    engine_candidates = _engine_candidates(
        explicit_snapshot=engine_snapshot,
        project_name=project_name,
        seg_id=seg_id,
        include_settings_default=include_settings_default,
    )

    def _try(path: str, key: str, matched_class: str, provenance: dict[str, Any], source: str) -> SegmentArtifact | None:
        candidates_tried.append({
            "path": path,
            "key": key,
            "matched_class": matched_class,
            "engine_source": source,
            "exists": os.path.isfile(path),
        })
        if os.path.isfile(path):
            return SegmentArtifact(
                path=path,
                seg_id=seg_id,
                matched_key=key,
                matched_class=matched_class,
                engine_provenance=provenance,
                engine_source=source,
                speaker_fingerprint=speaker_fingerprint,
                candidates=list(candidates_tried),
            )
        return None

    # 1) engine-aware keys in provenance priority order
    for profile, source in engine_candidates:
        engine_identity = str(profile.get("cache_identity") or "").strip()
        if not engine_identity:
            continue
        ck = segment_cache_key(
            seg_id, emotion, emo_alpha, speech_rate, pinyin_hints,
            director_metadata, speaker_fingerprint, engine_identity,
        )
        found = _try(
            os.path.join(segments_dir, f"{ck}.wav"),
            ck, "engine_aware", profile, source,
        )
        if found is not None:
            return found

    # 2) speaker-aware key (no engine identity)
    ck = segment_cache_key(
        seg_id, emotion, emo_alpha, speech_rate, pinyin_hints,
        director_metadata, speaker_fingerprint, None,
    )
    found = _try(
        os.path.join(segments_dir, f"{ck}.wav"),
        ck, "speaker_aware", {}, "none",
    )
    if found is not None:
        return found

    # 3) param-aware key (no speaker, no engine)
    ck = segment_cache_key(
        seg_id, emotion, emo_alpha, speech_rate, pinyin_hints,
        director_metadata, None, None,
    )
    found = _try(
        os.path.join(segments_dir, f"{ck}.wav"),
        ck, "param_aware", {}, "none",
    )
    if found is not None:
        return found

    # 4) legacy bare file.  Tried for legacy/no-strict lookups, and for strict
    # lookups only when the caller explicitly opts in (a speaker/engine-aware
    # project must never silently play an old actor's bare file after a rebind).
    if allow_legacy_fallback or not strict:
        legacy = os.path.join(segments_dir, f"{seg_id}.wav")
        found = _try(legacy, seg_id, "legacy_bare", {}, "none")
        if found is not None:
            return found

    # 5) any variant (explicit opt-in; never default for Voice Cast projects
    # and never used by plan accounting, which must distinguish engines)
    if allow_any_variant:
        try:
            if os.path.isdir(segments_dir):
                for name in sorted(os.listdir(segments_dir)):
                    if name.startswith(f"{seg_id}_") and name.endswith(".wav"):
                        found = _try(
                            os.path.join(segments_dir, name),
                            os.path.splitext(name)[0], "any_variant", {}, "none",
                        )
                        if found is not None:
                            return found
        except OSError:
            pass

    return SegmentArtifact(
        path=None,
        seg_id=seg_id,
        engine_provenance=(engine_candidates[0][0] if engine_candidates else {}),
        engine_source=(engine_candidates[0][1] if engine_candidates else "none"),
        speaker_fingerprint=speaker_fingerprint,
        candidates=list(candidates_tried),
    )


def has_segment_audio(
    *,
    segments_dir: str,
    seg_id: str,
    emotion: str = "neutral",
    emo_alpha: float = 1.0,
    speech_rate: float = 1.0,
    pinyin_hints: Any = None,
    director_metadata: Any = None,
    speaker_fingerprint: str | None = None,
    engine_snapshot: dict[str, Any] | None = None,
    project_name: str | None = None,
    allow_legacy_fallback: bool = False,
    allow_any_variant: bool = False,
) -> bool:
    """Return whether the segment has a resolvable audio artifact.

    ``allow_legacy_fallback`` permits matching the pre-cache-key bare
    ``{seg_id}.wav`` (used by plan accounting so old completed books stay
    completed).  ``allow_any_variant`` matches any ``{seg_id}_*.wav`` glob and
    is intended only for legacy projects that carry no engine provenance;
    plan deliberately leaves it off so a file produced by a *different* engine
    is treated as remaining and re-synthesized under the current task engine.
    """
    artifact = resolve_segment_artifact(
        segments_dir=segments_dir,
        seg_id=seg_id,
        emotion=emotion,
        emo_alpha=emo_alpha,
        speech_rate=speech_rate,
        pinyin_hints=pinyin_hints,
        director_metadata=director_metadata,
        speaker_fingerprint=speaker_fingerprint,
        engine_snapshot=engine_snapshot,
        project_name=project_name,
        allow_legacy_fallback=allow_legacy_fallback or allow_any_variant,
        allow_any_variant=allow_any_variant,
    )
    if artifact.exists():
        return True
    if allow_any_variant:
        try:
            if os.path.isdir(segments_dir):
                return any(
                    name.startswith(f"{seg_id}_") and name.endswith(".wav")
                    for name in os.listdir(segments_dir)
                )
        except OSError:
            return False
    return False
