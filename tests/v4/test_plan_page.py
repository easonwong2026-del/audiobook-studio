from pathlib import Path


def test_phase3_plan_preview_is_isolated_from_v3_navigation():
    root = Path(__file__).resolve().parents[2]
    source = (root / "ui/pages/v4_plan_page.py").read_text(encoding="utf-8")
    assert 'elem_id="grp-v4-synthesis-plan"' in source
    assert "重新生成计划" in source
    assert "TTS 合成计划" in source
    assert "create_v4_plan_page" not in (
        root / "app.py"
    ).read_text(encoding="utf-8")
