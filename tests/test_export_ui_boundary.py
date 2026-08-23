"""Round 3F ownership and safe-file adapter contracts."""
from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path

from ui import file_component_paths


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
EXPORT_SOURCE = (ROOT / "ui" / "export_handlers.py").read_text(encoding="utf-8")
EXPORT_TREE = ast.parse(EXPORT_SOURCE)
PATH_SOURCE = (ROOT / "ui" / "file_component_paths.py").read_text(encoding="utf-8")
PATH_TREE = ast.parse(PATH_SOURCE)
EXPORT_UX_TEST_SOURCE = (ROOT / "tests" / "test_export_ux.py").read_text(encoding="utf-8")


def _functions(tree):
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


APP_FUNCTIONS = _functions(APP_TREE)
EXPORT_FUNCTIONS = _functions(EXPORT_TREE)
PATH_FUNCTIONS = _functions(PATH_TREE)

EXPORT_SYMBOLS = {
    "_EXPORT_ACTIVE_STATUSES",
    "_EXPORT_TERMINAL_STATUSES",
    "_EXPORT_STATUS_LABELS",
    "_remember_export_ui_state",
    "_export_ui_reset",
    "_export_ui_noop",
    "_export_ui_callback_is_current",
    "_resolve_export_ui_artifact",
    "_copy_export_ui_artifact",
    "_export_ui_values",
    "refresh_export_status",
    "reconcile_export_state",
    "open_export_location",
    "do_export",
    "refresh_export_readiness",
    "do_export_subtitles",
    "refresh_export_default_dir",
}


def test_formal_export_has_one_ui_owner():
    assert all(name in EXPORT_FUNCTIONS for name in EXPORT_SYMBOLS if name not in {
        "_EXPORT_ACTIVE_STATUSES",
        "_EXPORT_TERMINAL_STATUSES",
        "_EXPORT_STATUS_LABELS",
    })
    assert not EXPORT_SYMBOLS.intersection(APP_FUNCTIONS)
    assert "from ui import export_handlers as export_ui" in APP_SOURCE
    assert "from ui import file_component_paths" in APP_SOURCE
    assert not (ROOT / "ui" / "wiring" / "export_wiring.py").exists()
    assert "import app" not in EXPORT_SOURCE
    for forbidden in ("TaskRepository", "QualityRepository", "ProductionRuntime", "ProductionRuntimeClient"):
        assert forbidden not in EXPORT_SOURCE


def test_export_constants_live_with_export_owner():
    for name in ("_EXPORT_ACTIVE_STATUSES", "_EXPORT_TERMINAL_STATUSES", "_EXPORT_STATUS_LABELS"):
        assert name in EXPORT_SOURCE
        assert name not in APP_SOURCE


def test_do_export_signature_and_event_graph_are_frozen():
    fn = EXPORT_FUNCTIONS["do_export"]
    assert [arg.arg for arg in fn.args.args] == ["fmt", "bitrate", "output_dir"]
    assert fn.args.vararg is not None and fn.args.vararg.arg == "args"
    assert "e_export_timer = gr.Timer(1.0, active=False)" in APP_SOURCE
    for marker in (
        "export_ui.refresh_export_status",
        "export_ui.refresh_export_readiness",
        "export_ui.refresh_export_default_dir",
        "export_ui.do_export",
        "export_ui.open_export_location",
        "export_ui.do_export_subtitles",
    ):
        assert marker in APP_SOURCE
    for stale in (
        "e_go.click(\n        do_export",
        "e_open.click(\n        open_export_location",
        "e_subtitle_btn.click(do_export_subtitles",
    ):
        assert stale not in APP_SOURCE


def test_shared_safe_path_owner_and_utility_redirects():
    assert list(PATH_FUNCTIONS) == ["safe_path_for_file_component"]
    assert "_safe_path_for_file_component" not in APP_SOURCE
    assert "allowed_paths=[config.get_data_dir()]" in APP_SOURCE
    for fn_name in ("do_supplement_export", "do_quick_tts_export"):
        fn = _functions(APP_TREE)[fn_name]
        source = ast.unparse(fn)
        assert "file_component_paths.safe_path_for_file_component" in source
        assert "_safe_path_for_file_component" not in source
    assert "file_component_paths.safe_path_for_file_component" in EXPORT_SOURCE


def test_export_ux_tests_follow_new_owner():
    assert "import app" not in EXPORT_UX_TEST_SOURCE
    assert "app." not in EXPORT_UX_TEST_SOURCE
    assert "from ui import export_handlers as export_ui" in EXPORT_UX_TEST_SOURCE


def test_safe_path_internal_and_external_preserve_source(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    external_dir = tmp_path / "external"
    data_dir.mkdir()
    external_dir.mkdir()
    monkeypatch.setattr(file_component_paths.config, "get_data_dir", lambda: str(data_dir))

    internal = data_dir / "inside.wav"
    internal.write_bytes(b"inside")
    assert file_component_paths.safe_path_for_file_component(str(internal)) == str(internal)

    external = external_dir / "outside.wav"
    external.write_bytes(b"outside")
    result = file_component_paths.safe_path_for_file_component(str(external))
    try:
        assert result != str(external)
        assert os.path.dirname(result) == os.path.abspath(tempfile.gettempdir())
        assert Path(result).read_bytes() == b"outside"
        assert external.read_bytes() == b"outside"
    finally:
        if result != str(external):
            Path(result).unlink(missing_ok=True)


def test_safe_path_none_missing_and_traversal(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(file_component_paths.config, "get_data_dir", lambda: str(data_dir))
    assert file_component_paths.safe_path_for_file_component(None) is None
    missing = data_dir / "missing.wav"
    assert file_component_paths.safe_path_for_file_component(str(missing)) == str(missing)

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    traversal = data_dir / ".." / "outside.wav"
    result = file_component_paths.safe_path_for_file_component(str(traversal))
    try:
        assert result != str(traversal)
        assert Path(result).read_bytes() == b"outside"
    finally:
        if result != str(traversal):
            Path(result).unlink(missing_ok=True)


def test_safe_path_duplicate_name_uses_timestamp(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    temp_dir = tmp_path / "temp"
    external_dir = tmp_path / "external"
    data_dir.mkdir()
    temp_dir.mkdir()
    external_dir.mkdir()
    monkeypatch.setattr(file_component_paths.config, "get_data_dir", lambda: str(data_dir))
    monkeypatch.setattr(file_component_paths.tempfile, "gettempdir", lambda: str(temp_dir))
    monkeypatch.setattr(file_component_paths.time, "time", lambda: 42.123)
    source = external_dir / "duplicate.wav"
    source.write_bytes(b"duplicate")
    (temp_dir / "audiobook_export_duplicate.wav").write_bytes(b"old")

    result = file_component_paths.safe_path_for_file_component(str(source))
    assert result == str(temp_dir / "audiobook_export_42123_duplicate.wav")
    assert Path(result).read_bytes() == b"duplicate"


def test_safe_path_copy_failure_falls_back_to_source(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    external = tmp_path / "outside.wav"
    data_dir.mkdir()
    external.write_bytes(b"outside")
    monkeypatch.setattr(file_component_paths.config, "get_data_dir", lambda: str(data_dir))

    def fail_copy(*_args, **_kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(file_component_paths.shutil, "copy2", fail_copy)
    assert file_component_paths.safe_path_for_file_component(str(external)) == str(external)


def test_safe_path_commonpath_error_is_external(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    external = tmp_path / "outside.wav"
    temp_dir = tmp_path / "temp"
    data_dir.mkdir()
    temp_dir.mkdir()
    external.write_bytes(b"outside")
    monkeypatch.setattr(file_component_paths.config, "get_data_dir", lambda: str(data_dir))
    monkeypatch.setattr(file_component_paths.tempfile, "gettempdir", lambda: str(temp_dir))

    def fail_commonpath(_paths):
        raise ValueError("different drives")

    monkeypatch.setattr(file_component_paths.os.path, "commonpath", fail_commonpath)
    result = file_component_paths.safe_path_for_file_component(str(external))
    try:
        assert result != str(external)
        assert Path(result).read_bytes() == b"outside"
    finally:
        if result != str(external):
            Path(result).unlink(missing_ok=True)
