from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from lib import config
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
