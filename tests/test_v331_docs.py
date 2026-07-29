from pathlib import Path

from lib import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_v331_documentation_is_consistent():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"v{__version__}" in readme
    assert "AI 驱动的本地有声书制作工作台" in readme
    assert "文本分析必须由 WorkBuddy" not in readme
    assert "本工作台只负责「加载 JSON" not in readme
    assert "## [3.3.1] - 2026-07-28" in changelog
    assert (ROOT / "docs/releases/v3.3.1.md").is_file()
