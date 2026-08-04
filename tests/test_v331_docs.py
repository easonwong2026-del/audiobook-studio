from pathlib import Path

from lib import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_v331_documentation_is_consistent():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"v{__version__}" in readme
    assert "本地有声书制作工作台" in readme
    assert "外部 Agent" in readme
    assert "AI 驱动的本地有声书制作工作台" not in readme
    assert "## 当前开发线：V3.3.3 JSON 工作台" in changelog
    assert (ROOT / "docs/releases/v3.3.3.md").is_file()
