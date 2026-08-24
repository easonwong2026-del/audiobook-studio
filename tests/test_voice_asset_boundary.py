"""Round 3C ownership and behavior contracts for low-risk Voice Asset UI."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from ui import voice_handlers
from ui.components import voice_binding


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
HANDLERS_SOURCE = (ROOT / "ui" / "voice_handlers.py").read_text(encoding="utf-8")
HANDLERS_TREE = ast.parse(HANDLERS_SOURCE)
VOICE_COMPONENT_SOURCE = (ROOT / "ui" / "components" / "voice_binding.py").read_text(encoding="utf-8")
VOICE_COMPONENT_TREE = ast.parse(VOICE_COMPONENT_SOURCE)
WIRING_SOURCE = (ROOT / "ui" / "wiring" / "voice_wiring.py").read_text(encoding="utf-8")

MOVED = (
    "refresh_role_list",
    "select_role_from_list",
    "play_lib_voice",
    "_save_category_choices",
    "save_to_lib",
    "filter_vlib_by_category",
    "refresh_voice_lib",
    "select_voice_from_browser",
    "refresh_categories",
    "refresh_voice_filters",
)


def _top_level_functions(tree):
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


APP_FUNCTIONS = _top_level_functions(APP_TREE)
HANDLER_FUNCTIONS = _top_level_functions(HANDLERS_TREE)
VOICE_COMPONENT_FUNCTIONS = _top_level_functions(VOICE_COMPONENT_TREE)


def _snapshot(script, bindings):
    return SimpleNamespace(script=script, bindings=bindings)


def _session(project="demo"):
    return SimpleNamespace(project=project)


def test_low_risk_callbacks_have_one_ui_owner():
    for name in MOVED:
        assert name not in APP_FUNCTIONS, f"app.py still owns {name}"
        assert name in HANDLER_FUNCTIONS, f"ui.voice_handlers missing {name}"
    assert "import app" not in HANDLERS_SOURCE
    for forbidden in ("ProductionRuntime", "RuntimeTTSService", "VoiceCastResolver"):
        assert forbidden not in HANDLERS_SOURCE


def test_frozen_cross_domain_handlers_and_library_state_remain_in_app():
    for name in (
        "bind_voice",
        "refresh_voice_cast_ui",
        "finalize_voice_cast_ui",
        "preview_bound_voice",
        "refresh_production_voice_choices",
        "_lib_path",
        "_lib_voices",
    ):
        assert name in APP_FUNCTIONS, f"frozen app contract moved unexpectedly: {name}"


def test_role_config_title_has_one_shared_presentation_owner():
    assert "_role_config_title" not in APP_FUNCTIONS
    assert "_role_config_title" not in HANDLER_FUNCTIONS
    assert "format_role_config_title" in VOICE_COMPONENT_FUNCTIONS
    assert "format_role_config_title(role, voice, dest)" in ast.unparse(APP_FUNCTIONS["bind_voice"])
    assert "format_role_config_title(role, voice, binding)" in ast.unparse(
        HANDLER_FUNCTIONS["select_role_from_list"]
    )
    for forbidden in ("gradio", "import app", "services", "VoiceCastResolver", "RuntimeTTSService"):
        assert forbidden not in VOICE_COMPONENT_SOURCE


@pytest.mark.parametrize(
    ("role", "voice", "binding", "expected"),
    [
        (None, None, None, "### 当前角色配置\n请从左侧角色列表选择角色。"),
        ("旁白", {"description": "沉稳男中音", "name": "Ignored"}, "/tmp/narrator.wav", "### 当前角色：旁白\n沉稳男中音\n✅ 已绑定"),
        ("妈妈", {"name": "温柔女声"}, None, "### 当前角色：妈妈\n温柔女声\n⚠ 待绑定"),
        ("配角", {}, "", "### 当前角色：配角\n⚠ 待绑定"),
        ("配角", {}, "/tmp/side.wav", "### 当前角色：配角\n✅ 已绑定"),
    ],
)
def test_shared_role_config_title_exact_behavior(role, voice, binding, expected):
    assert voice_binding.format_role_config_title(role, voice, binding) == expected


def test_live_voice_wiring_uses_module_qualified_low_risk_owners():
    expected = {
        "select_role_from_list": "voice_ui.select_role_from_list",
        "refresh_role_list": "voice_ui.refresh_role_list",
        "play_lib_voice": "voice_ui.play_lib_voice",
        "save_to_lib": "voice_ui.save_to_lib",
        "filter_vlib_by_category": "voice_ui.filter_vlib_by_category",
        "refresh_voice_lib": "voice_ui.refresh_voice_lib",
        "select_voice_from_browser": "voice_ui.select_voice_from_browser",
    }
    for key, owner in expected.items():
        assert f'"{key}": {owner}' in APP_SOURCE
    for key, owner in (
        ("bind_voice", "bind_voice"),
        ("refresh_role_summary", "refresh_role_summary"),
        ("refresh_voice_cast_ui", "refresh_voice_cast_ui"),
        ("finalize_voice_cast", "finalize_voice_cast_ui"),
        ("preview_bound_voice", "preview_bound_voice"),
    ):
        assert f'"{key}": {owner}' in APP_SOURCE
    for key in ("select_role_from_list", "refresh_role_list", "save_to_lib"):
        assert f'cb["{key}"]' in WIRING_SOURCE or key in WIRING_SOURCE


def test_live_chains_preserve_order_and_selected_opened_isolation():
    for chain_name in ("_open_chain_rest", "_post_archive_reconcile"):
        fn = APP_FUNCTIONS[chain_name]
        source = ast.unparse(fn)
        lib_index = source.index("voice_ui.refresh_voice_lib")
        categories_index = source.index("voice_ui.refresh_categories")
        assert lib_index < categories_index
        assert "selected_project" not in source
        assert "project_view_ui" not in source
        assert "refresh_project_storage" not in source
    assert "voice_ui.refresh_role_list" in APP_SOURCE
    assert "voice_ui.refresh_voice_filters" in APP_SOURCE
    assert "voice_ui.refresh_voice_lib" in APP_SOURCE
    assert 'page["v_role_search"].change(\n        cb["refresh_role_list"]' in WIRING_SOURCE


def test_role_list_fixture_covers_empty_snapshot_search_and_current_retention(monkeypatch):
    session = _session()
    script = {"voices": {
        "旁白": {"description": "沉稳男中音"},
        "妈妈": {"description": "温柔女声"},
    }}
    bindings = {"旁白": "/tmp/narrator.wav", "妈妈": None}
    snapshot = _snapshot(script, bindings)
    monkeypatch.setattr(voice_handlers, "_snapshot", lambda _session: snapshot)

    assert voice_handlers.refresh_role_list("", None, SimpleNamespace(project=None))["choices"] == []
    monkeypatch.setattr(voice_handlers, "_snapshot", lambda _session: None)
    assert voice_handlers.refresh_role_list("", None, session)["choices"] == []
    monkeypatch.setattr(voice_handlers, "_snapshot", lambda _session: snapshot)

    matching = voice_handlers.refresh_role_list("温柔", "妈妈", session)
    assert matching["value"] == "妈妈"
    assert matching["choices"] == [("妈妈\n温柔女声\n⚠ 待绑定", "妈妈")]
    assert voice_handlers.refresh_role_list("不存在", "妈妈", session)["value"] is None
    assert voice_handlers.refresh_role_list("旁白", "妈妈", session)["value"] is None


def test_select_role_fixture_preserves_seven_outputs_and_binding_copy(monkeypatch):
    snapshot = _snapshot(
        {"voices": {"旁白": {"description": "沉稳男中音"}, "妈妈": {"description": "温柔女声"}}},
        {"旁白": "/tmp/narrator.wav", "妈妈": None},
    )
    monkeypatch.setattr(voice_handlers, "_snapshot", lambda _session: snapshot)
    session = _session()

    assert len(voice_handlers.select_role_from_list(None, session)) == 7
    assert len(voice_handlers.select_role_from_list("不存在", session)) == 7
    unbound = voice_handlers.select_role_from_list("妈妈", session)
    assert unbound[0] == "妈妈"
    assert unbound[2]["value"] is None
    assert unbound[4] == "*当前绑定音频：未选择*"
    bound = voice_handlers.select_role_from_list("旁白", session)
    assert bound[0] == "旁白"
    assert bound[2]["value"] == "/tmp/narrator.wav"
    assert bound[4] == "*当前绑定音频：narrator.wav*"
    assert "沉稳男中音" in bound[1]


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        (None, ["未分类", "— 新建 —"]),
        ([], ["未分类", "— 新建 —"]),
        (["温柔", "低沉"], ["未分类", "温柔", "低沉", "— 新建 —"]),
        (["未分类", "温柔"], ["未分类", "温柔", "— 新建 —"]),
    ],
)
def test_category_choices_fixture(categories, expected):
    assert voice_handlers._save_category_choices(categories) == expected


def test_voice_library_refresh_fixture_preserves_rows_schema_and_category(monkeypatch):
    scanned = [{"name": "温柔_a.wav", "category": "温柔", "size_kb": 1.2, "path": "/tmp/温柔_a.wav"}]
    monkeypatch.setattr(voice_handlers.voice_lib, "scan_voice_library", lambda search, category: scanned)
    monkeypatch.setattr(voice_handlers.voice_lib, "list_categories", lambda: ["温柔"])
    captured = {}

    def style(rows, headers, status_col=None):
        captured.update(rows=rows, headers=headers, status_col=status_col)
        return "styled-table"

    monkeypatch.setattr(voice_handlers.df_style, "style_dataframe", style)
    table, category = voice_handlers.refresh_voice_lib("a", "温柔")
    assert table == "styled-table"
    assert captured == {
        "rows": [["温柔_a.wav", "温柔", 1.2, "/tmp/温柔_a.wav"]],
        "headers": ["名称", "分类", "大小(KB)", "试听"],
        "status_col": None,
    }
    assert category["choices"] == ["温柔"]
    assert category["value"] == "温柔"


def test_browser_and_play_fixture_distinguish_missing_and_existing_files(tmp_path, monkeypatch):
    existing = tmp_path / "温柔_a.wav"
    existing.write_bytes(b"wav")
    monkeypatch.setattr(voice_handlers.config, "get_voice_library", lambda: str(tmp_path))

    assert voice_handlers.play_lib_voice("") is None
    assert voice_handlers.play_lib_voice("missing.wav") is None
    assert voice_handlers.play_lib_voice(existing.name) == str(existing)
    empty_selection = voice_handlers.select_voice_from_browser([["missing.wav"]], None)
    assert empty_selection[0]["__type__"] == "update" and empty_selection[1] is None
    invalid = voice_handlers.select_voice_from_browser({"data": []}, SimpleNamespace(index=(0, 0)))
    assert invalid[1] is None
    selected = voice_handlers.select_voice_from_browser(
        {"data": [[existing.name, "温柔", 0.0, str(existing)]]},
        SimpleNamespace(index=(0, 0)),
    )
    assert selected[0]["value"] == existing.name
    assert selected[1] == str(existing)
    missing = voice_handlers.select_voice_from_browser(
        [["missing.wav", "未分类", 0.0, ""]], SimpleNamespace(index=(0, 0))
    )
    assert missing[0]["value"] == "missing.wav"
    assert missing[1] is None


def test_save_fixture_preserves_error_and_success_four_tuple(monkeypatch):
    monkeypatch.setattr(
        voice_handlers.ProjectService,
        "save_to_lib",
        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad voice"))),
    )
    error = voice_handlers.save_to_lib("rec.wav", None, "", "未分类", _session())
    assert error[0] == "bad voice"
    assert len(error) == 4

    monkeypatch.setattr(
        voice_handlers.ProjectService,
        "save_to_lib",
        staticmethod(lambda *args, **kwargs: "/tmp/lib/new.wav"),
    )
    monkeypatch.setattr(voice_handlers.voice_lib, "list_categories", lambda: ["温柔"])
    monkeypatch.setattr(voice_handlers.voice_lib, "voice_names", lambda: ["new.wav"])
    success = voice_handlers.save_to_lib("rec.wav", None, "new", "温柔", _session())
    assert len(success) == 4
    assert success[0] == "已保存至音色库: new.wav"
    assert success[1]["choices"] == ["new.wav"]
    assert success[2]["choices"] == ["new.wav"]
    assert success[3]["choices"] == ["未分类", "温柔", "— 新建 —"]
    assert success[3]["value"] == "温柔"


def test_category_refresh_and_filter_fixture(monkeypatch):
    monkeypatch.setattr(voice_handlers.voice_lib, "list_categories", lambda: ["低沉", "温柔"])
    monkeypatch.setattr(voice_handlers.voice_lib, "voice_names", lambda category=None: [category or "全部"])
    bind, lib, save = voice_handlers.refresh_voice_filters()
    assert bind["choices"] == ["低沉", "温柔"] and bind["value"] is None
    assert lib["choices"] == ["低沉", "温柔"] and lib["value"] is None
    assert save["value"] == "未分类" and "未分类" in save["choices"]
    assert voice_handlers.filter_vlib_by_category("温柔")["choices"] == ["温柔"]
