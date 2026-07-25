"""Launcher - activate venv and run app.py

5.7：去除任何个人电脑绝对路径硬编码。程序目录由本文件位置推导（仓库可整体移动），
python 解释器优先级：环境变量 AUDIOBOOK_STUDIO_PYTHON > 仓库同级 index-tts/.venv。
"""
import os
import shutil
import subprocess

# 程序目录：本文件所在目录（仓库可整体移动，不依赖绝对路径）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# python 解释器：环境变量优先，否则取仓库同级的 index-tts venv（相对路径，可移植）；
# 最后回退到系统 PATH 中的 python。
PYTHON = os.environ.get("AUDIOBOOK_STUDIO_PYTHON") or (
    os.path.join(BASE_DIR, "..", "index-tts", ".venv", "Scripts", "python.exe")
    if os.path.isfile(os.path.join(BASE_DIR, "..", "index-tts", ".venv", "Scripts", "python.exe"))
    else "python"
)


def main() -> None:
    """Entry point: prepare environment, run dependency check and start app."""
    os.chdir(BASE_DIR)

    # 双击后的首个中文即时反馈（由 Python 输出，��免 .bat 中文编码乱码）
    print("有声书工作台启动中，请稍后...")

    # 检查运行环境（依赖检查较慢，先给出提示，避免控制台空屏）
    print("正在检查运行环境，请稍候...")

    # Check dependency
    result = subprocess.run(
        [PYTHON, "-c", "import gradio"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        subprocess.run([PYTHON, "-m", "pip", "install", "gradio", "pydub"], check=True)

    # Check scientific / audio deps needed by the export post-processing chain
    # (numpy, scipy, pyloudnorm for LUFS-16; mutagen for ID3 / chapter tags).
    result = subprocess.run(
        [PYTHON, "-c", "import numpy, scipy, pyloudnorm, mutagen"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        subprocess.run(
            [PYTHON, "-m", "pip", "install", "numpy", "scipy", "pyloudnorm", "mutagen"],
            check=True,
        )

    # ffmpeg is a system binary (NOT a pip package). Exporting mp3/m4b needs it;
    # if it is missing we must warn loudly instead of silently degrading.
    # 5.8：缺失时显式报错（导出 mp3/m4b 会抛 ExportError，已生成的中间 WAV 仍保留），
    # 不再"静默回退 WAV"。
    if shutil.which("ffmpeg") is None:
        print()
        print("=" * 50)
        print("  ⚠ 警告：未检测到 ffmpeg！")
        print("  导出 mp3 / m4b 需要 ffmpeg（系统二进制，不通过 pip 安装）。")
        print("  缺失时导出会显式报错（已生成的中间 WAV 仍保留），")
        print("  请下载 ffmpeg 并加入 PATH，或改用 WAV 格式导出。")
        print("  下载地址：https://ffmpeg.org/download.html")
        print("=" * 50)
        print()

    # Start app
    print()
    print("=" * 50)
    print("       有声书合成工作台 | Audiobook Studio v3.1.0")
    print("=" * 50)
    print()
    print("  浏览器访问地址:")
    print("  -->  http://localhost:7862  <--")
    print()
    print("  首次加载模型需要等待 10-30 秒")
    print("  关闭此窗口即可停止服务")
    print()
    print("=" * 50)
    print()

    # 加载语音合成引擎（首次约 10-30 秒），先给出提示
    print("正在加载语音合成引擎，首次约 10-30 秒...")
    subprocess.run([PYTHON, "app.py"], check=True)


if __name__ == "__main__":
    main()
