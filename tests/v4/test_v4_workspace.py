from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gradio as gr
import numpy as np
from scipy.io import wavfile

from domain.v4.models import source_sha256
from lib import config
from repositories.v4_analysis_repository import V4AnalysisRepository
from services.v4_export import V4ExportService
from ui import v4_workspace_handlers as handlers


def test_v4_is_default_source_creation_wiring():
    root = Path(__file__).resolve().parents[2]
    app = (root / "app.py").read_text(encoding="utf-8")
    page = (root / "ui/pages/create_project_page.py").read_text(encoding="utf-8")
    navigation = (root / "ui/navigation.py").read_text(encoding="utf-8")
    assert "cp_create.click(\n        v4_ui.create_v4_from_source" in app
    assert "创建 v4 项目" in page
    assert '"v4", "✨ v4 工作流"' in navigation
    assert "create_v4_workspace_page" in app


def test_workspace_create_open_and_plan_without_ai_or_tts(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    monkeypatch.setattr(config, "get_projects_root", lambda: str(projects))
    source = tmp_path / "book.txt"
    source.write_text("本地旁白。", encoding="utf-8")
    status, _message, update, _legacy = handlers.create_v4_from_source(
        "book", str(source), "作品", "作者"
    )
    assert status.startswith("✅")
    assert update["value"].startswith("book-")
    name = update["value"]
    opened = handlers.open_v4_project(name)
    assert "1 片段" in opened[0]
    assert opened[1] == []
    assert "引擎 `indextts2`" in opened[8]
    assert opened[9] == "章节 0/0 · Tasks 0 · 完成 0 · 缓存命中 0 · 失败 0 · stale 0"
    plan_rows, message, queue, queue_summary = handlers.generate_v4_plan(name)
    assert plan_rows == []
    assert "未绑定角色 1" in message
    assert queue == []
    assert queue_summary == (
        "章节 0/0 · Tasks 0 · 完成 0 · 缓存命中 0 · 失败 0 · stale 0"
    )


def test_v4_analysis_buttons_visibility_for_needs_attention():
    """needs_attention 下「继续 AI 分析 / 重新分析」均可见（PRD 待明确事项 6）。"""
    visible = handlers.v4_analysis_buttons_visibility({"status": "needs_attention"})
    assert visible["v_continue_analysis"]["visible"] is True
    assert visible["v_reanalyze"]["visible"] is True
    hidden = handlers.v4_analysis_buttons_visibility({"status": "completed"})
    assert hidden["v_continue_analysis"]["visible"] is True
    assert hidden["v_reanalyze"]["visible"] is False
    empty = handlers.v4_analysis_buttons_visibility(None)
    assert empty["v_reanalyze"]["visible"] is False


def test_v4_analysis_summary_text_never_prints_100_for_zero_dialogue():
    """0 对白时 _analysis_summary_text 不打印 100%，显示未知文案。"""
    text = handlers._analysis_summary_text(
        {
            "status": "completed",
            "summary": {
                "identified_characters": 0,
                "dialogue_total": 0,
                "dialogue_unresolved": 0,
                "dialogue_coverage": None,
            },
        }
    )
    assert "100%" not in text
    assert "未识别到对白" in text


def test_v4_analysis_summary_text_renders_reason_codes():
    """needs_attention + reason_codes 时展示用户可读原因。"""
    text = handlers._analysis_summary_text(
        {
            "status": "needs_attention",
            "validity": {"reason_codes": ["dialogue_signal_no_dialogue"]},
            "summary": {
                "identified_characters": 0,
                "dialogue_total": 0,
                "dialogue_unresolved": 0,
                "dialogue_coverage": None,
            },
        }
    )
    assert "原文存在明显对白信号" in text


# ── PR #22 实测反馈 R-1/R-2/R-3：progress 注入 + 持久化进展框 + 按钮反馈 ──


def test_handlers_use_gradio_progress_injection():
    """R-1：三个分析入口的 progress 参数用 gr.Progress() 标准注入（Gradio 5 才注入）。"""
    for name in ("create_v4_from_source", "continue_v4_analysis", "reanalyze_v4_project"):
        signature = inspect.signature(getattr(handlers, name))
        default = signature.parameters["progress"].default
        assert isinstance(default, gr.Progress), f"{name} 的 progress 未用 gr.Progress()"


def test_report_analysis_progress_calls_progress_with_stage_map():
    """_report_analysis_progress 把阶段消息映射到 (x, 6) 并带 desc。"""
    calls = []

    class RecordingProgress:
        def __call__(self, value, desc=""):
            calls.append((value, desc))

    progress = RecordingProgress()
    handlers._report_analysis_progress(progress, "正在分析章节剧本")
    assert calls == [((4, 6), "正在分析章节剧本")]
    calls.clear()
    # 未映射消息回退到第 1 步，不抛错
    handlers._report_analysis_progress(progress, "未知阶段消息")
    assert calls == [((1, 6), "未知阶段消息")]


def test_analysis_progress_text_renders_running_stage_with_elapsed():
    """R-2：running 状态渲染当前阶段 x/6 + 已耗时 + 完成阶段用时。"""
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    text = handlers._analysis_progress_text(
        {
            "status": "running",
            "current_stage": "script_director",
            "stages": {
                "book_understanding": {
                    "status": "completed",
                    "started_at": started,
                    "finished_at": started,
                    "duration_ms": 80000,
                },
                "script_director": {
                    "status": "running",
                    "started_at": started,
                    "finished_at": "",
                    "duration_ms": 0,
                },
            },
        }
    )
    assert "AI 分析进行中" in text
    assert "分析章节剧本" in text
    assert "第 2/6 步" in text
    assert "5 分钟" in text
    assert "1 分 20 秒" in text


def test_analysis_progress_text_renders_completed_state():
    """R-2：completed 状态展示完成头与全部 6 步。"""
    text = handlers._analysis_progress_text(
        {
            "status": "completed",
            "current_stage": "completed",
            "stages": {
                "book_understanding": {
                    "status": "completed",
                    "started_at": "",
                    "finished_at": "",
                    "duration_ms": 0,
                },
                "script_director": {
                    "status": "completed",
                    "started_at": "",
                    "finished_at": "",
                    "duration_ms": 0,
                },
                "script_review": {
                    "status": "completed",
                    "started_at": "",
                    "finished_at": "",
                    "duration_ms": 0,
                },
            },
        }
    )
    assert "AI 分析已完成" in text
    assert "1. 阅读全书（人物记忆）：✅ 完成" in text
    assert "6. 分析完成：✅ 完成" in text


def test_analysis_progress_text_reads_stages_from_disk(tmp_path, monkeypatch):
    """R-2：切换页面后从磁盘 analysis.json 渲染真实阶段与耗时。"""
    projects = tmp_path / "projects"
    monkeypatch.setattr(config, "get_projects_root", lambda: str(projects))
    source = tmp_path / "book.txt"
    source.write_text("第一章\n本地旁白。", encoding="utf-8")
    status, _message, update, _legacy = handlers.create_v4_from_source(
        "book", str(source), "作品", "作者"
    )
    assert status.startswith("✅")
    name = update["value"]
    project = projects / name
    source_text = (project / "source/source.txt").read_text(encoding="utf-8")
    sha = source_sha256(source_text)
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    V4AnalysisRepository(project).save(
        {
            "schema_version": "v4-analysis-state-v2",
            "source_sha256": sha,
            "status": "running",
            "current_stage": "book_understanding",
            "stages": {
                "book_understanding": {
                    "status": "running",
                    "started_at": started,
                    "finished_at": "",
                    "duration_ms": 0,
                }
            },
        }
    )
    text = handlers.analysis_progress_text(name)
    assert "AI 分析进行中" in text
    assert "阅读全书" in text
    assert "第 1/6 步" in text
    assert "5 分钟" in text


def test_analysis_result_text_includes_message_and_errors():
    """R-3：分析结果把 result.message + errors 里的明确错误展示给用户。"""

    class FakeResult:
        message = "⚠ 分析未完成，需要人工确认"
        errors = ["全书阅读有 1 个章节失败，可继续分析重试。"]

    text = handlers._analysis_result_text(FakeResult())
    assert "分析未完成" in text
    assert "章节失败" in text

    class FakeResultDedup:
        message = "⚠ 分析未完成：全书阅读有 1 个章节失败"
        errors = ["全书阅读有 1 个章节失败"]

    dedup = handlers._analysis_result_text(FakeResultDedup())
    assert dedup.count("章节失败") == 1


def test_voice_page_analysis_feedback_visible_and_progress_component():
    """R-3：v_analysis_msg 可见（不再 visible=False），并新增可见进展区。"""
    root = Path(__file__).resolve().parents[2]
    page = (root / "ui/pages/voice_page.py").read_text(encoding="utf-8")
    assert 'v_analysis_msg = gr.Markdown("' in page
    msg_line = page.split('v_analysis_msg = gr.Markdown(')[1].split("\n")[0]
    assert "visible=False" not in msg_line
    assert "v_analysis_progress = gr.Markdown(" in page
    assert '"v_analysis_progress"' in page


def test_app_analysis_chain_immediate_feedback_and_progress_outputs():
    """R-2/R-3：点击继续分析立即显示开始文案，输出包含消息 + 进展区。"""
    root = Path(__file__).resolve().parents[2]
    app = (root / "app.py").read_text(encoding="utf-8")
    assert 'lambda: "⏳ 开始分析…", None, [v_analysis_msg]' in app
    assert 'lambda: "⏳ 开始重新分析…", None, [v_analysis_msg]' in app
    assert "[v_analysis_msg, v_analysis_progress]" in app
    assert "v4_ui.analysis_progress_text, [p_sel], [v_analysis_progress]" in app


def test_v4_wav_export_uses_assembled_chapter_order(tmp_path):
    project = tmp_path / "book"
    (project / "script").mkdir(parents=True)
    (project / "audio/chapters").mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps({"title": "作品"}), encoding="utf-8"
    )
    (project / "script/script.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"chapter_id": "chapter_0001"},
                    {"chapter_id": "chapter_0002"},
                ]
            }
        ),
        encoding="utf-8",
    )
    wavfile.write(
        project / "audio/chapters/chapter_0001.wav",
        22050,
        np.ones(100, dtype=np.int16),
    )
    wavfile.write(
        project / "audio/chapters/chapter_0002.wav",
        44100,
        np.ones(200, dtype=np.int16),
    )
    output = V4ExportService.export(project, output_format="wav")
    rate, data = wavfile.read(output)
    assert rate == 22050
    assert len(data) > 100 + 100
