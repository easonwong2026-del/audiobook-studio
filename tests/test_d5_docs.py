"""D5 文档收敛测试：日志行内 ⏯🔄 按钮承诺收敛为段落下拉式试听/重合成。

REVIEW D5 确认：Tab2 日志为只读 Textbox、Tab3 用段落下拉 + 试听/重合成按钮的等价方案，
功能已可用、非功能性断点。本测试断言两份文档（更新日志.txt、根目录 ARCHITECTURE.md）
已将「行内按钮」承诺收敛为「段落下拉式试听/重合成（功能等价）」，消除文档漂移。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_ROOT = PROJECT_ROOT
ARCH_PATH = os.path.join(os.path.dirname(PROJECT_ROOT), "ARCHITECTURE.md")
CHANGELOG_PATH = os.path.join(APP_ROOT, "更新日志.txt")


def test_architecture_md_converges_inline_to_dropdown():
    assert os.path.isfile(ARCH_PATH), "根目录 ARCHITECTURE.md 不存在"
    text = open(ARCH_PATH, encoding="utf-8").read()
    # 必须出现「下拉式」收敛表述（功能等价方案）
    assert ("下拉式" in text) or ("功能等价" in text), (
        "ARCHITECTURE.md 应说明采用段落下拉式试听/重合成，替代原日志行内按钮"
    )
    # 关键决策表中不应再主张「日志行内嵌 ⏯ 🔄」为已落地方案
    assert "日志行内嵌 ⏯ 🔄" not in text, (
        "ARCHITECTURE.md 仍把「日志行内嵌 ⏯🔄」作为承诺，未收敛为下拉方案"
    )


def test_changelog_converges_inline_to_dropdown():
    assert os.path.isfile(CHANGELOG_PATH), "更新日志.txt 不存在"
    text = open(CHANGELOG_PATH, encoding="utf-8").read()
    assert ("下拉式" in text) or ("功能等价" in text), (
        "更新日志.txt 应说明 D5 收敛为段落下拉式试听/重合成方案"
    )
