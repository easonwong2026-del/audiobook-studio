"""导出服务：包 ``lib.audio_pipeline.export_book``，透传 R2 报错（禁止 import gradio）。

UI 层 ``app.do_export`` 调用本服务，捕获其抛出的 ``ExportError`` / ``RuntimeError``
后显式展示给用户（取代原先 ffmpeg 失败时「静默回退 WAV」导致 UI 收不到信号的行为）。
"""
from __future__ import annotations

import logging

from lib import audio_pipeline
from lib.exceptions import ExportError

logger = logging.getLogger(__name__)


class ExportService:
    """导出成品：委托 ``audio_pipeline.export_book``，错误直接上抛。"""

    @staticmethod
    def export(project_dir: str, fmt: str, bitrate: str = "192k",
               output_dir: str = "") -> str:
        """导出指定格式成品。

        Args:
            project_dir: 项目目录（含 ``structured_script.json`` 与 ``segments/``）。
            fmt: 导出格式 wav / mp3 / m4b。
            bitrate: mp3 / m4b 比特率，默认 192k。
            output_dir: 输出目录（留空用项目内 ``output/``）。

        Returns:
            导出文件绝对路径。

        Raises:
            ExportError: ffmpeg 缺失 / 转码失败（由 ``audio_pipeline`` 抛出，原样透传）。
            RuntimeError: 存在未合成段落（由 ``audio_pipeline`` 抛出）。
        """
        return audio_pipeline.export_book(
            project_dir, format=fmt, bitrate=bitrate, output_dir=output_dir
        )

    @staticmethod
    def export_subtitles(project_dir: str, formats=("srt", "lrc"),
                         output_dir: str = "") -> list:
        """生成字幕（srt / lrc），委托 ``lib.audio_pipeline.generate_subtitles``。

        Args:
            project_dir: 项目目录（含 ``structured_script.json`` 与 ``segments/``）。
            formats: 要生成的字幕格式集合，可含 ``"srt"`` / ``"lrc"``。
            output_dir: 输出目录（留空用项目内 ``output/``）。

        Returns:
            生成的字幕文件路径列表（按请求格式）。
        """
        return audio_pipeline.generate_subtitles(
            project_dir, formats=formats, output_dir=output_dir
        )
