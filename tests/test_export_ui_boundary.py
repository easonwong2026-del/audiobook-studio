"""Round 3F ownership and safe-file adapter contracts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ui import file_component_paths


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
