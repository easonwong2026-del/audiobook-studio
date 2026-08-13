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
from collections import OrderedDict
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
