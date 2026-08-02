from pathlib import Path


def test_phase2_review_page_is_isolated_and_has_required_controls():
    root = Path(__file__).resolve().parents[2]
    source = (root / "ui/pages/v4_speaker_review_page.py").read_text(encoding="utf-8")
    assert 'elem_id="grp-v4-speaker-review"' in source
    assert "待确认片段" in source
    assert "指定已有角色" in source
    assert "新建角色" in source
    assert "锁定角色" in source
    assert "应用到选中片段" in source
    assert "候选角色" in source
    assert "确认成为正式角色" in source
    assert "拒绝候选" in source
    assert "合并到已有角色" in source
    assert "create_v4_speaker_review_page" not in (
        root / "app.py"
    ).read_text(encoding="utf-8")
