"""用 gradio_client 真实触发 demo 的事件，捕获组件返回值，定位'错误'根因。"""
import gradio as gr
import os
import tempfile
import numpy as np
from scipy.io import wavfile

sr = 22050
t = np.linspace(0, 0.5, int(sr * 0.5), False)
data = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)

test_dir = tempfile.mkdtemp(prefix="gradio_test_")
test_wav = os.path.join(test_dir, "test_audio.wav")
test_zh = os.path.join(test_dir, "（054）-测试.wav")
test_mp3 = os.path.join(test_dir, "（054）-测试.mp3")
wavfile.write(test_wav, sr, data)
wavfile.write(test_zh, sr, data)

# 用 ffmpeg 造一个 mp3（如果可用）
try:
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-i", test_wav, test_mp3], check=True,
                   capture_output=True)
except Exception as e:
    print("ffmpeg 不可用，跳过 mp3:", e)
    test_mp3 = test_wav

with gr.Blocks() as demo:
    v_audio = gr.Audio(label="上传/录制", type="filepath",
                       sources=["upload", "microphone"])
    v_preview_audio = gr.Audio(label="试听", type="filepath", interactive=False)
    btn1 = gr.Button("普通wav→v_audio")
    btn1.click(lambda: test_wav, None, v_audio)
    btn2 = gr.Button("中文wav→v_audio")
    btn2.click(lambda: test_zh, None, v_audio)
    btn3 = gr.Button("中文mp3→v_preview")
    btn3.click(lambda: test_mp3, None, v_preview_audio)


if __name__ == "__main__":
    demo.launch(server_port=7869, share=False, prevent_thread_lock=True,
                show_error=True)
    from gradio_client import Client
    import time
    time.sleep(3)
    client = Client("http://127.0.0.1:7869")
    for i, name in enumerate(["普通wav→v_audio", "中文wav→v_audio", "中文mp3→v_preview"]):
        try:
            res = client.predict(fn_index=i)
            print(f"[{name}] -> {res}")
        except Exception as e:
            print(f"[{name}] EXCEPTION: {type(e).__name__}: {e}")
    demo.close()
