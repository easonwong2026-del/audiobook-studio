"""Round 3D ownership and behavior contracts for Settings residual callbacks."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from services.session import SessionState
from ui import settings_handlers


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
HANDLERS_SOURCE = (ROOT / "ui" / "settings_handlers.py").read_text(encoding="utf-8")
HANDLERS_TREE = ast.parse(HANDLERS_SOURCE)
WIRING_SOURCE = (ROOT / "ui" / "wiring" / "settings_wiring.py").read_text(encoding="utf-8")
WIRING_TREE = ast.parse(WIRING_SOURCE)


def _functions(tree):
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


APP_FUNCTIONS = _functions(APP_TREE)
HANDLER_FUNCTIONS = _functions(HANDLERS_TREE)
WIRING_FUNCTIONS = _functions(WIRING_TREE)


def test_settings_callback_ownership_is_closed():
    assert "apply_data_dir" not in APP_FUNCTIONS
    assert "open_data_dir" not in APP_FUNCTIONS
    for name in ("apply_data_dir", "open_data_dir", "run_diagnostics_ui"):
        assert name in HANDLER_FUNCTIONS
    assert "run_diagnostics_ui" not in WIRING_FUNCTIONS
    assert "services.environment_diagnostics" not in WIRING_SOURCE
    assert "settings_handlers.apply_data_dir" in WIRING_SOURCE
    assert "settings_handlers.open_data_dir" in WIRING_SOURCE
    assert "settings_handlers.run_diagnostics_ui" in WIRING_SOURCE


def test_data_dir_chain_remains_composed_in_original_order():
    assert "data_dir_chain = page[\"s_data_apply\"].click(" in WIRING_SOURCE
    assert "data_dir_chain = data_dir_chain.then(fn, inputs, outputs)" in WIRING_SOURCE
    apply_index = WIRING_SOURCE.index("settings_handlers.apply_data_dir")
    catalog_index = WIRING_SOURCE.index("data_dir_chain = data_dir_chain.then")
    assert apply_index < catalog_index
    assert "merge_refresh" not in WIRING_SOURCE
    assert "assembly_refresh" not in WIRING_SOURCE


def test_settings_handlers_keep_repository_dependency_debt_deferred():
    for name in ("ConfigRepository", "ProjectRepository", "TaskRepository"):
        assert "from repositories" in HANDLERS_SOURCE and name in HANDLERS_SOURCE
    assert "SettingsService" not in HANDLERS_SOURCE
    assert "DiagnosticsService" not in HANDLERS_SOURCE


def test_apply_data_dir_empty_input_contract():
    assert settings_handlers.apply_data_dir("") == ("⚠ 请填写保存位置", "")
    assert settings_handlers.apply_data_dir("   ") == ("⚠ 请填写保存位置", "")


def test_apply_data_dir_success_resets_session_but_preserves_query(monkeypatch, tmp_path):
    target = tmp_path / "new-root"
    calls = []
    monkeypatch.setattr(
        settings_handlers.ProjectService,
        "set_data_dir",
        lambda value: calls.append(value) or str(target),
    )
    ss = SessionState(
        project="opened",
        script={"meta": {"title": "old"}},
        bindings={"旁白": "old.wav"},
    )
    ss.set_selected("selected")
    ss.set_snapshot(object())
    ss.synthesis = object()
    ss.set_catalog_query("abc")
    ss.begin_archive_confirmation()

    message, returned = settings_handlers.apply_data_dir(f"  {target}  ", ss)

    assert calls == [str(target)]
    assert returned == str(target)
    assert message == f"✅ 数据目录已设置为：{target}（本会话立即生效）"
    assert ss.selected_project is None
    assert ss.project is None
    assert ss.script is None
    assert ss.bindings == {}
    assert ss.project_snapshot is None
    assert ss.synthesis is None
    assert ss.catalog_query == "abc"
    assert ss._archive_confirmation_revision == -1


def test_apply_data_dir_without_session_keeps_compatibility(monkeypatch, tmp_path):
    target = tmp_path / "new-root"
    calls = []
    monkeypatch.setattr(
        settings_handlers.ProjectService,
        "set_data_dir",
        lambda value: calls.append(value) or str(target),
    )
    message, returned = settings_handlers.apply_data_dir(str(target))
    assert calls == [str(target)]
    assert returned == str(target)
    assert "（本会话立即生效）" in message


def test_apply_data_dir_exception_escapes_message_and_returns_empty_path(monkeypatch):
    def fail(_value):
        raise OSError("bad <root> & path")

    monkeypatch.setattr(settings_handlers.ProjectService, "set_data_dir", fail)
    message, returned = settings_handlers.apply_data_dir("/tmp/new")
    assert message == "❌ 设置失败：bad &lt;root&gt; &amp; path"
    assert returned == ""


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_open_data_dir_platform_commands(monkeypatch, tmp_path, platform):
    data_dir = str(tmp_path / "data")
    monkeypatch.setattr(settings_handlers.config, "get_data_dir", lambda: data_dir)
    monkeypatch.setattr(sys, "platform", platform)
    opened = []
    monkeypatch.setattr(settings_handlers.os, "startfile", lambda path: opened.append(("win", path)), raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda args: opened.append(("proc", args)))

    result = settings_handlers.open_data_dir()

    assert result == f"✅ 已打开数据目录：`{data_dir}`"
    if platform == "win32":
        assert opened == [("win", data_dir)]
    elif platform == "darwin":
        assert opened == [("proc", ["open", data_dir])]
    else:
        assert opened == [("proc", ["xdg-open", data_dir])]


def test_open_data_dir_exception_escapes_message(monkeypatch, tmp_path):
    data_dir = str(tmp_path / "data")
    monkeypatch.setattr(settings_handlers.config, "get_data_dir", lambda: data_dir)
    monkeypatch.setattr(sys, "platform", "darwin")

    def fail(_args):
        raise OSError("open <failed> & now")

    monkeypatch.setattr(subprocess, "Popen", fail)
    assert settings_handlers.open_data_dir() == "❌ 打开数据目录失败：open &lt;failed&gt; &amp; now"


@pytest.mark.parametrize(
    ("status", "symbol"),
    [("ok", "✅"), ("warning", "⚠️"), ("error", "❌"), ("unknown-value", "❓")],
)
def test_diagnostics_ui_preserves_status_mapping_and_output_order(monkeypatch, status, symbol):
    report = {"status": status, "checks": []}
    calls = []

    def table(value):
        calls.append(("table", value))
        return "TABLE"

    def markdown(value):
        calls.append(("markdown", value))
        return "MARKDOWN"

    monkeypatch.setattr(settings_handlers, "run_environment_diagnostics", lambda: report)
    monkeypatch.setattr(settings_handlers, "diagnostics_table", table)
    monkeypatch.setattr(settings_handlers, "diagnostics_to_markdown", markdown)

    result = settings_handlers.run_diagnostics_ui()

    assert result == (f"### {symbol} 总体状态：{status}", "TABLE", "MARKDOWN")
    assert calls == [("table", report), ("markdown", report)]
