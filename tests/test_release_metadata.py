"""Current release metadata and runtime dependency contracts."""
from __future__ import annotations

from pathlib import Path

from lib import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_current_release_metadata_is_consistent():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_path = ROOT / f"docs/releases/v{__version__}.md"
    lib_init = (ROOT / "lib/__init__.py").read_text(encoding="utf-8")
    launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")

    assert f'__version__ = "{__version__}"' in lib_init
    assert f"v{__version__}" in readme
    assert "本地有声书制作工作台" in readme
    assert "外部 Agent" in readme
    assert "AI 驱动的本地有声书制作工作台" not in readme
    assert f"## {__version__} — " in changelog
    assert release_path.is_file()
    release_notes = release_path.read_text(encoding="utf-8")
    assert f"# Audiobook Studio v{__version__}" in release_notes
    assert "No user project migration required." in release_notes
    assert 'title=f"Audiobook Studio v{__version__}"' in app
    assert "from lib import __version__" in launcher


def test_supported_runtime_dependency_contract():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "gradio==5.50.0" in requirements
    assert "huggingface-hub" not in requirements
    assert "pydantic" not in requirements
