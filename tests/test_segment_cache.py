"""单元测试：lib/segment_cache.py（B7 缓存键改内容哈希）

验证：
  - 缓存键随 emotion / emo_alpha / speech_rate / pinyin_hints 变化而不同
  - 相同参数 → 稳定（幂等）
  - find_segment_wav 命中优先级：参数感知文件 > 旧版裸文件（兼容）
  - has_segment_wav 兼容 参数感知文件 / 旧版裸文件 / 任意参数变体（glob）
"""
import sys
import os
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.segment_cache as sc  # noqa: E402
import lib.queue as synth_queue  # noqa: E402


def test_cache_key_differs_by_emotion():
    a = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None)
    b = sc.segment_cache_key("1-001", "happy", 1.0, 1.0, None)
    assert a != b, "情感不同应产生不同缓存键"
    assert a.startswith("1-001_") and len(a) == len("1-001_") + 8


def test_cache_key_differs_by_emo_alpha():
    a = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None)
    b = sc.segment_cache_key("1-001", "neutral", 0.5, 1.0, None)
    assert a != b


def test_cache_key_differs_by_speech_rate():
    a = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None)
    b = sc.segment_cache_key("1-001", "neutral", 1.0, 1.3, None)
    assert a != b


def test_cache_key_differs_by_pinyin_hints():
    a = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None)
    b = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, {"了": "le"})
    assert a != b


def test_cache_key_stable_for_same_params():
    a = sc.segment_cache_key("1-001", "happy", 0.8, 0.9, {"了": "le"})
    b = sc.segment_cache_key("1-001", "happy", 0.8, 0.9, {"了": "le"})
    assert a == b, "相同参数应产生稳定（幂等）缓存键"


def test_cache_key_changes_with_speaker_fingerprint():
    first = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None, None, "voice-a")
    second = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None, None, "voice-b")
    assert first != second


def test_speaker_aware_lookup_does_not_fallback_to_legacy_speaker(tmp_path):
    seg_dir = str(tmp_path)
    legacy = os.path.join(seg_dir, "1-001.wav")
    open(legacy, "w").close()
    assert sc.find_segment_wav(
        seg_dir, "1-001", "text", "role", "neutral", speaker_fingerprint="voice-new"
    ) is None
    path = sc.segment_wav_path(
        seg_dir, "1-001", "neutral", speaker_fingerprint="voice-new"
    )
    open(path, "w").close()
    assert sc.find_segment_wav(
        seg_dir, "1-001", "text", "role", "neutral", speaker_fingerprint="voice-new"
    ) == path


def test_cache_key_empty_dict_equals_none():
    """B7 导出丢段回归：缺省 pinyin_hints 的 {} 与 None 必须等价。

    合成侧（script_loader 默认给 {}）与导出侧（raw JSON 默认 None）若产生
    不同缓存键，导出会查不到文件而丢段。归一化后两者一致。
    """
    a = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None)
    b = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, {})
    assert a == b, "缺省 pinyin_hints 的 {} 与 None 应产生同一缓存键（避免导出丢段）"
    # 空串也应与 None 等价
    c = sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, "")
    assert a == c, "空串 pinyin_hints 也应与 None 等价"


def test_segment_wav_path_shape():
    p = sc.segment_wav_path("/x/segments", "1-001", "neutral", 1.0, 1.0, None)
    assert p.endswith(".wav")
    assert os.path.basename(p).startswith("1-001_")


def test_find_segment_wav_param_aware_priority(tmp_path):
    seg_dir = str(tmp_path)
    # 旧版裸文件存在
    bare = os.path.join(seg_dir, "1-001.wav")
    open(bare, "w").close()
    # 参数感知文件（happy）也存在
    ck = sc.segment_wav_path(seg_dir, "1-001", "happy", 1.0, 1.0, None)
    open(ck, "w").close()
    # 查询 happy → 命中参数感知文件（优先于裸文件）
    fp = sc.find_segment_wav(seg_dir, "1-001", "text", "role", "happy")
    assert fp == ck, "应优先命中参数感知缓存键文件"


def test_find_segment_wav_legacy_fallback(tmp_path):
    seg_dir = str(tmp_path)
    bare = os.path.join(seg_dir, "1-001.wav")
    open(bare, "w").close()
    # 查询 neutral（无对应参数感知文件）→ 回退到旧版裸文件
    fp = sc.find_segment_wav(seg_dir, "1-001", "text", "role", "neutral")
    assert fp == bare, "参数感知文件缺失时应回退到旧版裸文件"


def test_find_segment_wav_missing(tmp_path):
    assert sc.find_segment_wav(str(tmp_path), "1-001", "t", "r", "neutral") is None


def test_has_segment_wav_variants(tmp_path):
    seg_dir = str(tmp_path)
    assert not sc.has_segment_wav(seg_dir, "1-001")
    # 1) 任意参数变体（glob）
    open(os.path.join(seg_dir, "1-001_happy12ab.wav"), "w").close()
    assert sc.has_segment_wav(seg_dir, "1-001")
    # 2) 旧版裸文件
    open(os.path.join(seg_dir, "1-002.wav"), "w").close()
    assert sc.has_segment_wav(seg_dir, "1-002")
    # 3) 给定参数的缓存键文件
    ck = sc.segment_wav_path(seg_dir, "1-003", "sad", 1.0, 1.0, None)
    open(ck, "w").close()
    assert sc.has_segment_wav(seg_dir, "1-003", "sad", 1.0, 1.0, None)


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 O2：effective_params（合成期情感 / 语速全局覆盖一致性）
# ─────────────────────────────────────────────────────────────────────────────

def _seg(**kw):
    """构造一个带任意属性的段落对象（兼容 ScriptSegment 的 getattr 访问）。"""
    return types.SimpleNamespace(**kw)


def test_effective_params_no_overrides_uses_seg_defaults():
    """无覆盖（None / {}）时沿用段落自身默认参数。"""
    seg = _seg(emotion="neutral", emo_alpha=1.0, speech_rate=1.0)
    assert sc.effective_params(seg, None) == ("neutral", 1.0, 1.0)
    assert sc.effective_params(seg, {}) == ("neutral", 1.0, 1.0)


def test_effective_params_override_emotion():
    """overrides 提供 emotion 时优先采用（override 开关仅影响 alpha/rate）。"""
    seg = _seg(emotion="neutral", emo_alpha=1.0, speech_rate=1.0)
    ov = {"emotion": "happy"}
    assert sc.effective_params(seg, ov) == ("happy", 1.0, 1.0)


def test_effective_params_emotion_none_means_script():
    """emotion=None 表示「按剧本」，应沿用段落自身 emotion。"""
    seg = _seg(emotion="neutral", emo_alpha=1.0, speech_rate=1.0)
    ov = {"emotion": None}
    assert sc.effective_params(seg, ov) == ("neutral", 1.0, 1.0)


def test_effective_params_seg_without_emotion_overrides_provides():
    """段落无 emotion 属性时，由 overrides 提供（O2 全局覆盖生效）。"""
    seg = _seg(emo_alpha=1.0, speech_rate=1.0)  # 无 emotion
    ov = {"emotion": "sad"}
    assert sc.effective_params(seg, ov) == ("sad", 1.0, 1.0)


def test_effective_params_override_true_applies_alpha_rate():
    """override=True 时采用全局 emo_alpha / speech_rate。"""
    seg = _seg(emotion="angry", emo_alpha=0.7, speech_rate=1.2)
    ov = {"emotion": "happy", "override": True, "emo_alpha": 0.5, "speech_rate": 1.3}
    assert sc.effective_params(seg, ov) == ("happy", 0.5, 1.3)


def test_effective_params_override_false_keeps_seg_defaults():
    """override=False 时即使 overrides 给了 alpha/rate，仍用段落自身值。"""
    seg = _seg(emotion="angry", emo_alpha=0.7, speech_rate=1.2)
    ov = {"override": False, "emo_alpha": 0.9, "speech_rate": 2.0}
    # emotion 未给(None) → 按剧本 = angry；alpha/rate 因 override=False → seg 自身
    assert sc.effective_params(seg, ov) == ("angry", 0.7, 1.2)


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 O2：queue._seg_cache_key 随有效参数变化（一致性根因）
# ─────────────────────────────────────────────────────────────────────────────

def test_seg_cache_key_varies_with_effective_params():
    """同一段、不同有效参数 → 不同缓存键；相同参数 → 幂等。"""
    seg = _seg(id="1-001", emotion="neutral", emo_alpha=1.0, speech_rate=1.0,
               pinyin_hints=None)
    base = synth_queue._seg_cache_key(seg)
    # 不同 emotion → 不同键
    assert synth_queue._seg_cache_key(seg, emotion="happy") != base
    # 不同 emo_alpha → 不同键
    assert synth_queue._seg_cache_key(seg, emotion=None, emo_alpha=0.5) != base
    # 不同 speech_rate → 不同键
    assert synth_queue._seg_cache_key(seg, emotion=None, emo_alpha=None,
                                      speech_rate=1.3) != base
    # 相同有效参数 → 幂等
    assert synth_queue._seg_cache_key(seg, "neutral", 1.0, 1.0) == base


# ─────────────────────────────────────────────────────────────────────────────
# 2.4 T-2：SpeakerEmbeddingLRU（有界 LRU 缓存容器）
# ─────────────────────────────────────────────────────────────────────────────

def test_lru_evicts_least_recently_used():
    """maxsize=2 时 put 3 个不同 key，最久未用（a）被淘汰。"""
    lru = sc.SpeakerEmbeddingLRU(maxsize=2)
    lru.put("a", 1)
    lru.put("b", 2)
    lru.put("c", 3)  # 超出 maxsize → 淘汰队首 a
    assert len(lru) == 2
    assert "a" not in lru
    assert lru.get("b") == 2
    assert lru.get("c") == 3
    assert lru.get("a") is None


def test_lru_get_updates_recency_order():
    """get 命中将条目移到最近使用，影响后续淘汰顺序。"""
    lru = sc.SpeakerEmbeddingLRU(maxsize=2)
    lru.put("a", 1)
    lru.put("b", 2)
    # 访问 a → 移到末尾，b 成为最久未用
    assert lru.get("a") == 1
    lru.put("c", 3)  # 淘汰 b
    assert "b" not in lru
    assert lru.get("a") == 1
    assert lru.get("c") == 3


def test_lru_maxsize_floored_to_one():
    """maxsize<=0 时强制为 1（避免空缓存 / 除零）。"""
    lru = sc.SpeakerEmbeddingLRU(maxsize=0)
    assert lru.maxsize == 1
    lru.put("x", 1)
    lru.put("y", 2)  # 淘汰 x
    assert "x" not in lru
    assert lru.get("y") == 2
