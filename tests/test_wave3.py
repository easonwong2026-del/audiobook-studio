"""Wave 3 回归单测：B7（缓存键内容哈希）/ D4（试音完善，AST）/ D5（日志收敛文档）。

无需 GPU：纯逻辑 + AST 静态校验 + 伪造文件。

B7 验证：
  - ``lib.queue._seg_cache_key`` 对相同 seg 返同键；改 emotion/emo_alpha/
    speech_rate/pinyin_hints 后返不同键；且与 ``lib.segment_cache.segment_cache_key``
    公式一致（合成侧与导出侧同一缓存键，导出不丢段）。
  - ``lib.audio_pipeline._find_segment`` 在存在 ``{ck}.wav``（含全参数）时能找回；
    旧版裸 ``{seg_id}.wav`` 仍兼容；ck 优先于 legacy。

D5 验证：更新日志.txt 含 "Wave 3" 与 "下拉式试听"，且不含错误拼写 "lagacy"。

D4 验证（静态）：app.py 中 ``preview_bound_voice`` 已定义、已接线、使用
``_concat_wavs`` 拼接完整三句、不再只返回第一句。
"""
import sys
import os
import ast

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.queue as synth_queue          # noqa: E402
import lib.audio_pipeline as ap          # noqa: E402
import lib.segment_cache as sc           # noqa: E402

CHANGELOG = os.path.join(PROJECT_ROOT, "更新日志.txt")
APP_PATH = os.path.join(PROJECT_ROOT, "app.py")


class _FakeSeg:
    """模拟 script_loader 的 Segment 对象（含可选合成参数）。"""

    def __init__(self, seg_id, emotion, emo_alpha=1.0, speech_rate=1.0,
                 pinyin_hints=None, text="x", role="r"):
        self.id = seg_id
        self.emotion = emotion
        self.emo_alpha = emo_alpha
        self.speech_rate = speech_rate
        self.pinyin_hints = pinyin_hints
        self.text = text
        self.role = role


# ───────────────────────── B7 ─────────────────────────

def test_queue_seg_cache_key_param_sensitivity():
    """B7：缓存键对合成参数敏感；且与导出侧 segment_cache 公式一致。"""
    base = _FakeSeg("1-001", "neutral")
    k0 = synth_queue._seg_cache_key(base)

    # 相同 seg → 同键
    assert synth_queue._seg_cache_key(_FakeSeg("1-001", "neutral")) == k0

    # 任一参数变化 → 不同键
    assert synth_queue._seg_cache_key(_FakeSeg("1-001", "happy")) != k0
    assert synth_queue._seg_cache_key(_FakeSeg("1-001", "neutral", emo_alpha=0.5)) != k0
    assert synth_queue._seg_cache_key(_FakeSeg("1-001", "neutral", speech_rate=1.2)) != k0
    assert synth_queue._seg_cache_key(_FakeSeg("1-001", "neutral", pinyin_hints={"了": "le"})) != k0

    # 与导出侧（segment_cache）公式一致 → 导出链路不丢段
    assert k0 == sc.segment_cache_key("1-001", "neutral", 1.0, 1.0, None)


def test_find_segment_resolves_ck_and_legacy(tmp_path):
    """B7：导出侧查找能命中参数感知 ck 文件，也兼容旧版裸文件；ck 优先。"""
    seg_dir = str(tmp_path / "segments")
    os.makedirs(seg_dir)

    seg_id = "1-001"
    emotion, emo_alpha, speech_rate, pinyin_hints = "angry", 0.8, 1.1, {"了": "le"}
    ck = sc.segment_cache_key(seg_id, emotion, emo_alpha, speech_rate, pinyin_hints)
    ck_path = os.path.join(seg_dir, f"{ck}.wav")
    legacy_path = os.path.join(seg_dir, f"{seg_id}.wav")
    open(ck_path, "w").close()
    open(legacy_path, "w").close()

    # 1) 参数感知缓存键文件优先命中
    got = ap._find_segment(seg_dir, seg_id, "text", "role",
                           emotion, emo_alpha, speech_rate, pinyin_hints)
    assert got == ck_path, "应优先命中参数感知缓存键文件"

    # 2) 仅 legacy 裸文件存在时仍能找回（向后兼容旧工程）
    os.remove(ck_path)
    got2 = ap._find_segment(seg_dir, seg_id, "text", "role",
                            emotion, emo_alpha, speech_rate, pinyin_hints)
    assert got2 == legacy_path, "旧版裸文件应被兼容命中"

    # 3) 都不存在返回 None
    os.remove(legacy_path)
    assert ap._find_segment(seg_dir, seg_id, "text", "role", emotion) is None


# ───────────────────────── D5 ─────────────────────────

def test_changelog_wave3_section():
    """D5：更新日志含 Wave 3 / 下拉式试听，且不含错误拼写 lagacy。"""
    assert os.path.isfile(CHANGELOG), "更新日志.txt 缺失"
    text = open(CHANGELOG, encoding="utf-8").read()
    assert "Wave 3" in text, "更新日志应记录 Wave 3"
    assert "下拉式试听" in text, "更新日志应说明下拉式试听（D5 收敛）"
    assert "lagacy" not in text, "更新日志不应保留错误拼写 lagacy"


# ───────────────────────── D4（静态） ─────────────────────────

def test_preview_bound_voice_full_three_sentences():
    """D4 完善：preview_bound_voice 合成并拼接完整三句测试句，而非仅返回第一句。"""
    with open(APP_PATH, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "preview_bound_voice":
            fn = node
            break
    assert fn is not None, "app.py 未定义 preview_bound_voice（D4 缺失）"

    body = ast.unparse(fn)
    assert "test_voice" in body, "preview_bound_voice 应调用 test_voice 合成三句"
    assert "_concat_wavs" in body, "preview_bound_voice 应拼接完整三句"
    assert "test_voice(audio)[0]" not in body, "不应只返回第一句测试句"

    # 接线：v_preview_btn.click(preview_bound_voice, ...)
    wired = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "click"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "v_preview_btn"):
            if (node.args and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "preview_bound_voice"):
                wired = True
    assert wired, "v_preview_btn 未接线到 preview_bound_voice"
