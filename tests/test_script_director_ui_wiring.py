"""AI 剧本导演 UI 静态接线测试。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
PROJECT_PAGE = (ROOT / "ui/pages/project_page.py").read_text(encoding="utf-8")
DIRECTOR_PAGE = (ROOT / "ui/pages/director_page.py").read_text(encoding="utf-8")
HANDLERS = (ROOT / "ui/director_handlers.py").read_text(encoding="utf-8")


def test_director_panel_is_embedded_in_project_stage():
    assert "create_director_panel()" in PROJECT_PAGE
    assert '"d_provider"' in DIRECTOR_PAGE
    assert '"deepseek"' in DIRECTOR_PAGE
    assert '"openai"' in DIRECTOR_PAGE
    assert 'file_types=[".txt", ".docx", ".epub"]' in DIRECTOR_PAGE
    assert '"d_editor"' in DIRECTOR_PAGE
    assert '"d_apply"' in DIRECTOR_PAGE
    assert '"d_undo"' in DIRECTOR_PAGE
    assert '"d_voice_role"' in DIRECTOR_PAGE
    assert '"d_recommend"' in DIRECTOR_PAGE
    assert '"d_audition"' in DIRECTOR_PAGE
    assert '"d_feedback"' in DIRECTOR_PAGE
    assert '"d_feedback_apply"' in DIRECTOR_PAGE
    assert '"d_edit_chapter"' in DIRECTOR_PAGE


def test_director_event_fills_generated_script_and_project_name():
    assert "def analyze_director_file(" in HANDLERS
    assert "d_analyze.click(" in APP
    assert "director_ui.apply_director_edits" in APP
    assert "director_ui.undo_director_edits" in APP
    assert "director_ui.recommend_director_voice" in APP
    assert "director_ui.audition_director_segment" in APP
    assert "director_ui.apply_director_audition_feedback" in APP
    assert "director_ui.refresh_director_editor" in APP
