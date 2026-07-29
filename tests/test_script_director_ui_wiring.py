"""AI 剧本导演与角色声音 UI 静态接线测试。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
PROJECT_PAGE = (ROOT / "ui/pages/project_page.py").read_text(encoding="utf-8")
CREATE_PAGE = (ROOT / "ui/pages/create_project_page.py").read_text(encoding="utf-8")
VOICE_PAGE = (ROOT / "ui/pages/voice_page.py").read_text(encoding="utf-8")
HANDLERS = (ROOT / "ui/director_handlers.py").read_text(encoding="utf-8")
VOICE_HANDLERS = (ROOT / "ui/voice_handlers.py").read_text(encoding="utf-8")


def test_director_editor_is_in_project_stage():
    """高级剧本校正以折叠形式存在于项目管理页面。"""
    assert '"d_editor"' in PROJECT_PAGE
    assert '"d_apply"' in PROJECT_PAGE
    assert '"d_undo"' in PROJECT_PAGE
    assert '"d_edit_chapter"' in PROJECT_PAGE


def test_director_not_in_create_project_page():
    """新建项目页面不再包含 AI Provider 选择/模型字段/声音推荐。"""
    assert "d_analyze" not in CREATE_PAGE
    assert "d_voice_role" not in CREATE_PAGE
    assert "d_recommend" not in CREATE_PAGE
    assert "d_audition" not in CREATE_PAGE
    assert "d_audio" not in CREATE_PAGE


def test_recommendation_remains_but_voice_page_has_no_audition():
    """AI 声音推荐保留，角色页导演试听与反馈完整删除。"""
    assert '"v_recommend"' in VOICE_PAGE
    for name in ("v_audition", "v_audition_audio", "v_audition_status", "v_feedback", "v_feedback_apply"):
        assert name not in VOICE_PAGE
    assert "试听确认" not in VOICE_PAGE
    assert "② 确认绑定" in VOICE_PAGE


def test_director_editor_wired_in_app():
    """项目管理的人工校正事件使用 project-based handlers。"""
    assert "director_ui.refresh_director_editor_for_project" in APP
    assert "director_ui.apply_director_edits_for_project" in APP
    assert "director_ui.undo_director_edits_for_project" in APP
    assert "old analyze_director_file not in APP"
    assert "director_ui.analyze_director_file" not in APP
    assert "director_ui.recommend_director_voice" not in APP


def test_voice_handlers_wired():
    """角色声音页面由独立 wiring 注册，导演试听不再有入口。"""
    wiring = (ROOT / "ui/wiring/voice_wiring.py").read_text(encoding="utf-8")
    assert "wire_voice_page" in APP
    assert "voice_handlers.recommend_voice" in wiring
    for name in ("audition_director_segment", "apply_feedback", "v_audition", "v_feedback_apply"):
        assert name not in APP
        assert name not in wiring


def test_settings_handlers_exist():
    """设置页面相关回调已添加到 director_handlers。"""
    assert "def update_provider_config_fields" in HANDLERS
    assert "def save_ai_settings" in HANDLERS
    assert "def test_ai_connection" in HANDLERS
    assert "def apply_data_dir" in HANDLERS
    assert "def open_data_dir" in HANDLERS
