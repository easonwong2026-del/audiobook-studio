from pathlib import Path


def test_phase4_queue_page_isolated_from_v3_navigation():
    root = Path(__file__).resolve().parents[2]
    source = (root / "ui/pages/v4_queue_page.py").read_text(encoding="utf-8")
    assert 'elem_id="grp-v4-synthesis-queue"' in source
    assert "开始/继续合成" in source
    assert "拆分深度" in source
    assert "create_v4_queue_page" not in (
        root / "app.py"
    ).read_text(encoding="utf-8")
