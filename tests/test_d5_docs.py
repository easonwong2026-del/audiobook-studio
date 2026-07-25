# -*- coding: utf-8 -*-
def test_architecture_md_converges_inline_to_dropdown():
    import os
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ARCH_PATH = os.path.join(PROJECT_ROOT, "ARCHITECTURE.md")
    assert os.path.isfile(ARCH_PATH), f"Missing: {ARCH_PATH}"
    with open(ARCH_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "Tab3" in text or "下拉" in text or "功能等价" in text, "ARCHITECTURE.md 未提及 D5 收敛"
