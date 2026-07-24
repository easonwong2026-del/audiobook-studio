"""QA 独立边界/场景验证：角色单独补录 / 补合成导出（不初始化 IndexTTS2，用桩引擎）。

验证场景（对应 SOP 第 6 项）：
  A. 小 JSON role 未命中项目 voices -> 报错并列出可用角色
  B. 缺 lines / 缺 text -> 诊断
  C. 未开项目 -> 角色下拉灰显(interactive=False) + 合成报错不崩
  D. 合法流程：选角色 + 粘贴 2~3 句 -> 合成(桩) -> 导出 wav
     -> 经 _safe_path_for_file_component 得到可下载路径（白名单内原样返回）

不触碰 workspace/projects/；所有产物落在临时目录。
"""
from __future__ import annotations
import os
import sys
import json
import tempfile

import numpy as np
from scipy.io import wavfile
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import tts_engine, config
from services.supplement import SupplementService
from lib import audio_pipeline

# ── 桩引擎：写哑 wav，绝不初始化 IndexTTS2 ──
def _fake_synth(text, speaker_audio, emotion="neutral", emo_alpha=1.0,
                speech_rate=1.0, output_path="", **kw):
    sr = 16000
    wavfile.write(output_path, sr,
                  (np.sin(np.linspace(0, 8, sr, endpoint=False)) * 3000).astype(np.int16))
    return output_path

tts_engine.synthesize_segment = _fake_synth  # 模块级属性替换，app.py 调用时同样生效

_SCRIPT = {"meta": {"title": "测试书", "author": "甲"},
           "voices": {"旁白": {}, "小明": {}}, "chapters": []}

FAILURES = []


def _assert(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print(f"  [FAIL] {msg}")
    else:
        print(f"  [PASS] {msg}")


# ── 场景 A：role 未命中 ──
def scenario_a():
    print("== 场景 A: 小 JSON role 未命中项目 voices ==")
    bad = {"voices": {"幽灵": {}},
           "chapters": [{"id": 1, "segments": [{"id": "s1", "role": "幽灵", "text": "x"}]}]}
    errs = SupplementService.validate_small_json(bad, _SCRIPT)
    _assert(any("未在项目剧本 voices 中定义" in e for e in errs), "返回 role 未定义错误")
    _assert(any("可用角色" in e for e in errs), "错误列出可用角色")
    # parse_small_json 应抛 ValueError 带诊断
    try:
        SupplementService.parse_small_json(bad, _SCRIPT)
        _assert(False, "parse_small_json 应抛错")
    except ValueError as e:
        _assert("幽灵" in str(e), "ValueError 含未命中角色诊断")


# ── 场景 B：缺字段诊断 ──
def scenario_b():
    print("== 场景 B: 缺 lines / 缺 text 诊断 ==")
    # 缺 text 字段
    raw_no_text = {"voices": {"旁白": {}},
                   "chapters": [{"id": 1, "segments": [{"id": "s1", "role": "旁白"}]}]}
    errs = SupplementService.validate_small_json(raw_no_text, _SCRIPT)
    _assert(len(errs) > 0, "缺 text 给出诊断（非空错误列表）")
    # 缺 lines（空 segments）
    raw_empty = {"voices": {"旁白": {}},
                 "chapters": [{"id": 1, "segments": []}]}
    errs = SupplementService.validate_small_json(raw_empty, _SCRIPT)
    _assert(any("未包含任何段落" in e for e in errs), "空 segments 给出'未包含任何段落'诊断")


# ── 场景 C：未开项目 -> 下拉灰显 + 合成报错不崩 ──
def scenario_c():
    print("== 场景 C: 未开项目（ss=None / 无 project）==")
    import app  # gradio 可导入；仅取 handler 与 gr.update
    # 1) 角色下拉刷新：无项目应 interactive=False（gr.update 返回 dict）
    upd = app.refresh_supplement_roles(None)
    _assert(upd.get("interactive") is False, "refresh_supplement_roles(None) -> interactive=False")
    upd2 = app.refresh_supplement_roles(type("SS", (), {"project": None, "script": None, "bindings": {}})())
    _assert(upd2.get("interactive") is False, "无 project/script -> interactive=False")
    # 2) 合成 handler：未开项目应返回 ( [], 报错 ) 且不崩
    res = app.do_supplement_synth("旁白", "paste", "你好。世界。", "", [],
                                  None, 1.0, 1.0, 2, True, None, None)
    _assert(isinstance(res, tuple) and len(res) == 2, "do_supplement_synth 返回 2 元组")
    _assert(res[0] == [] and "请先打开项目" in res[1], "未开项目 -> ( [], '❌ 请先打开项目...' ) 不崩")
    # 3) 解析 JSON handler：未开项目也应安全返回
    rj = app.do_supplement_parse_json(None, None)
    _assert(isinstance(rj, tuple) and len(rj) == 4, "do_supplement_parse_json 返回 4 元组")
    _assert("请先打开项目" in rj[3], "未开项目解析 -> 报错不崩")


# ── 场景 D：合法流程 粘贴 -> 合成 -> 导出 wav -> safe path ──
def scenario_d():
    print("== 场景 D: 合法流程 粘贴2句 -> 合成(桩) -> 导出 wav -> safe path ==")
    import app
    tmp = tempfile.mkdtemp(prefix="qa_sup_")
    cache_dir = os.path.join(tmp, "supplement_cache")
    # 1) 逐句合成（桩引擎写哑 wav，落入 supplement_cache，不碰 segments/project.json）
    lines = ["今天天气真不错。", "我们出去走走吧！"]
    results = SupplementService.synthesize_lines(
        "旁白", lines, "fake_ref.wav", cache_dir=cache_dir)
    _assert(len(results) == 2, "合成返回 2 句结果")
    _assert(all(r["status"] == "ok" and os.path.isfile(r["wav_path"]) for r in results),
            "两句均 ok 且 wav 落地 supplement_cache")
    _assert(all("supplement_cache" in r["wav_path"] for r in results),
            "中间 wav 写入 supplement_cache（不写 segments/）")

    wavs = [r["wav_path"] for r in results]
    # 2) 导出拼接为一条 wav（format=wav 无需 ffmpeg），落临时 project 的 output/
    proj_dir = os.path.join(tmp, "proj")
    out_wav = SupplementService.build_output_path(proj_dir, "旁白", "wav")
    final = audio_pipeline.export_supplement(paths=wavs, out_path=out_wav, format="wav")
    rate, data = wavfile.read(final)
    _assert(os.path.isfile(final) and data.shape[0] > 0, "导出 wav 存在且非空")
    _assert("supplement_旁白_" in final and final.endswith(".wav"), "产物命名 supplement_{role}_{ts}.wav")

    # 3) 经 _safe_path_for_file_component 得到可下载路径
    #    临时 proj_dir 不在 data_dir 白名单内 -> 复制到 tempdir 副本（Gradio 默认可 serve）
    safe = app._safe_path_for_file_component(final)
    _assert(os.path.isfile(safe), "safe path 指向真实可下载文件")
    in_whitelist = (os.path.abspath(safe) == os.path.abspath(final))
    in_tempdir = os.path.abspath(safe).startswith(os.path.abspath(tempfile.gettempdir()))
    _assert(in_whitelist or in_tempdir,
            "safe path 为原路径(白名单内)或 tempdir 副本(可下载)")


def main():
    scenario_a()
    scenario_b()
    scenario_c()
    scenario_d()
    print("\n" + "=" * 50)
    if FAILURES:
        print(f"边界/场景验证：{len(FAILURES)} 项未通过（见上方 [FAIL]）")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("全部边界/场景验证通过 ✅")


if __name__ == "__main__":
    main()
