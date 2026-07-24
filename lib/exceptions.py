"""Lib 层自定义异常（禁止 import gradio）。

仅承载跨模块的错误信号，不依赖任何 UI / 框架，便于 service 层与 lib 层
在纯 Python 单测中直接使用。
"""
from __future__ import annotations


class ExportError(Exception):
    """导出失败（ffmpeg 缺失 / 转码失败）时抛出。

    ``lib.audio_pipeline.export_book`` 在 mp3 / m4b 且 ffmpeg 不可用
    （``FileNotFoundError``）或转码失败（``subprocess.CalledProcessError``）时抛出，
    错误信息包含：已生成的中间 WAV 绝对路径、ffmpeg 安装链接
    （https://ffmpeg.org/download.html）、以及「可改用 WAV 格式」的建议。

    ``services.export.ExportService`` 原样透传给上层，最终由
    ``app.do_export`` 的 ``except Exception`` 捕获并显式展示给用户，
    取代原先「静默回退 WAV」导致 UI 收不到信号的行为（R2）。
    """
    pass
