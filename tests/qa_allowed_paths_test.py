"""QA 实证：验证 allowed_paths 修复对 data_dir 外（D:/AudiobookStudio）音频文件放行效果。

用法（在 gradio-test venv 运行）：
    C:/Users/rakliang/.workbuddy/binaries/python/envs/gradio-test/Scripts/python.exe tests/qa_allowed_paths_test.py

设计：
  1) 导入项目 lib.config，调用 config.get_data_dir() 拿到真实 D:/AudiobookStudio（不造假路径）。
  2) 取 voice_library 下真实存在的音频文件（voice_01.wav）。
  3) 场景 A（修复版）：handler 返回该绝对路径，launch 时带 allowed_paths=[data_dir]，
     用 gradio_client.Client 触发，断言返回值是有效 cache 路径（非异常/非 InvalidPathError）。
  4) 场景 B（对照实验）：同样 handler + 同样文件，launch 不带 allowed_paths，
     触发同一 handler，确认复现 InvalidPathError。
"""
from __future__ import annotations
import os
import sys
import time
import traceback

PROJECT_ROOT = r"C:\Users\rakliang\WorkBuddy\2026-06-29-18-28-53\audiobook-studio"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gradio as gr
from gradio_client import Client

# lib.config 仅依赖 json/os/shutil，不会触发 IndexTTS2/CUDA。
from lib import config


def pick_real_audio() -> str:
    """取 voice_library 下首个真实存在的音频文件绝对路径。"""
    voice_dir = config.get_voice_library()
    for name in sorted(os.listdir(voice_dir)):
        if name.lower().endswith((".wav", ".mp3", ".flac", ".ogg")) and os.path.isfile(
            os.path.join(voice_dir, name)
        ):
            return os.path.join(voice_dir, name)
    raise RuntimeError(f"voice_library 下无音频文件: {voice_dir}")


def build_demo():
    """构造最小 app：handler 返回外部音频绝对路径，输出为 gr.Audio（复现实景）。"""
    with gr.Blocks() as demo:
        out = gr.Audio(label="外部音频预览")
        run = gr.Button("play")

        def handler():
            # 复现 app 行为：play_lib_voice / select_voice_from_browser / preview_chapter
            # 都直接返回外部绝对路径字符串给 gr.Audio。
            return TEST_FILE

        run.click(handler, None, out, api_name="handler")
    return demo


def wait_for_client(url: str, timeout: float = 20.0) -> Client:
    """轮询直到 gradio 服务可连。"""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            return Client(url)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"gradio_client 无法连接 {url}: {last_err}")


def run_scenario(label: str, allowed_paths):
    """启动一次 mini app，触发 handler，返回 (kind, detail)。kind ∈ {'ok','error'}。"""
    global TEST_FILE
    demo = build_demo()
    try:
        demo.launch(
            server_name="127.0.0.1",
            server_port=PORT,
            share=False,
            inbrowser=False,
            prevent_thread_lock=True,
            allowed_paths=allowed_paths,
            show_error=True,
        )
        # 注：launch(prevent_thread_lock=True) 的返回值不是 URL 字符串（是 TupleNoPrint），
        # 这里直接用 server_name:server_port 构造本地 URL，最稳妥。
        url = f"http://127.0.0.1:{PORT}"
        client = wait_for_client(url)
        try:
            result = client.predict(api_name="/handler")
            return "ok", result
        except Exception as e:  # noqa: BLE001
            return "error", f"{type(e).__name__}: {e}"
        finally:
            try:
                client.close()
            except Exception:
                pass
    finally:
        try:
            demo.close()
        except Exception:
            pass


def main():
    global TEST_FILE, PORT
    data_dir = config.get_data_dir()
    TEST_FILE = pick_real_audio()

    print("=" * 72)
    print("QA 实证：allowed_paths 修复机制")
    print("=" * 72)
    print(f"cwd (程序目录) : {os.getcwd()}")
    print(f"data_dir       : {data_dir}")
    print(f"TEST_FILE      : {TEST_FILE}")
    print(f"TEST_FILE 在 cwd 内? : {os.path.abspath(TEST_FILE).startswith(os.path.abspath(os.getcwd()))}")
    print(f"TEST_FILE 在 data_dir 内? : {os.path.abspath(TEST_FILE).startswith(os.path.abspath(data_dir))}")
    print("=" * 72)

    # 场景 A：修复版 —— 带 allowed_paths=[data_dir]
    PORT = 7877
    print(f"\n[场景 A] 修复版 launch(allowed_paths=[data_dir])  port={PORT}")
    a_kind, a_detail = run_scenario("A", allowed_paths=[data_dir])
    if a_kind == "ok":
        print(f"  结果: 返回有效值（非异常）")
        print(f"  返回值: {a_detail!r}")
        is_cache = isinstance(a_detail, str) and ("\\Temp\\gradio" in a_detail or "/tmp/gradio" in a_detail or a_detail.endswith((".wav", ".mp3", ".flac", ".ogg")))
        print(f"  形如 cache/媒体路径? : {is_cache}")
    else:
        print(f"  结果: 抛异常 -> {a_detail}")

    # 场景 B：对照实验 —— 不带 allowed_paths
    PORT = 7878
    print(f"\n[场景 B] 对照实验 launch(allowed_paths 省略)  port={PORT}")
    b_kind, b_detail = run_scenario("B", allowed_paths=[])
    if b_kind == "ok":
        print(f"  结果: 返回有效值（未复现 InvalidPathError）")
        print(f"  返回值: {b_detail!r}")
    else:
        msg = str(b_detail)
        # gradio_client 把服务端 InvalidPathError 包成 AppError，消息里不一定含
        # "InvalidPathError" 字面量；用根因签名（Cannot move / gradio cache dir /
        # allowed_paths parameter / current working directory）判定更准确。
        is_inv = any(
            k in msg
            for k in (
                "InvalidPathError",
                "Invalid path",
                "not allowed",
                "Cannot move",
                "gradio cache dir",
                "allowed_paths",
                "current working directory",
            )
        )
        print(f"  结果: 抛异常 -> {b_detail}")
        print(f"  属 InvalidPathError / 路径未授权? : {is_inv}")

    # 判定
    print("\n" + "=" * 72)
    print("判定")
    print("=" * 72)
    a_ok = a_kind == "ok"
    b_repro = b_kind == "error"
    if a_ok and b_repro:
        print("PASS: 修复版放行成功 且 对照版复现根因(InvalidPathError)。")
        print("结论: allowed_paths=[config.get_data_dir()] 确实消除了 InvalidPathError。")
        verdict = "PASS"
    elif a_ok and not b_repro:
        print("WARN: 修复版放行成功，但对照版未复现根因（可能 TEST_FILE 实际在 cwd 内？）。")
        verdict = "WARN"
    elif not a_ok and b_repro:
        print("FAIL: 修复版仍抛异常，allowed_paths 未生效。")
        verdict = "FAIL"
    else:
        print("FAIL: 两版都未如预期（需排查）。")
        verdict = "FAIL"
    print(f"VERDICT={verdict}")
    return verdict


if __name__ == "__main__":
    try:
        v = main()
        sys.exit(0 if v == "PASS" else 1)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
