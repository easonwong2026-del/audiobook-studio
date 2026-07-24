"""单元测试：lib/dataframe_style.py（纯模块，不依赖 gradio）。

覆盖点（独立于实现细节，仅断言契约行为）：
  1. style_dataframe 返回 dict，含 data / headers / metadata，且 metadata["styling"] 存在。
  2. styling 与 data 同形（行数==data 行数，每行列数==data 列数）。
  3. status_col=None 时，所有单元格样式均为 ""。
  4. 有 status_col 时：仅该列单元格可能非空；非空单元格以 "color:" 开头且值等于
     status_color_map[cell]；其余列全为 ""。
  5. status_color_map 命中校验：✅→#30D158、✅完成→#30D158 等（ICON_COLORS / STATUS_WORD_COLORS）。
  6. 未命中 status_color_map 的单元格回落 default_text（默认 #E8E8ED，可自定义）。
  7. 空 data（如 []）返回合法 dict，styling == []，不抛异常。
  8. STATUS_WORD_COLORS 的键集合与 lib.project_manager._project_status 实际产出一致。
  9. 纪律红线：本模块禁止 import gradio（源码中不得出现 gradio 导入）。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.dataframe_style as df  # noqa: E402

# 供多用例复用的样例数据（O4 书架形态：状态在第 4 列，索引 3）
BOOKSHELF_ROWS = [
    ["书A", "第一章", "1/3", "✅完成"],
    ["书B", "第二章", "0/3", "🟢进行中"],
    ["书C", "第三章", "2/3", "🟡部分"],
    ["书D", "第四章", "0/3", "🔴有失败"],
    ["书E", "第五章", "0/3", "⚪未开始"],
]


def _assert_same_shape(result, data):
    """断言 styling 与 data 同形。"""
    styling = result["metadata"]["styling"]
    assert len(styling) == len(data), "styling 行数应与 data 行数一致"
    for s_row, d_row in zip(styling, data):
        assert len(s_row) == len(d_row), "styling 每行列数应与 data 对应行列数一致"


def test_returns_contract_dict_with_styling():
    """契约 1：返回 dict，含 data/headers/metadata，且 metadata['styling'] 存在。"""
    result = df.style_dataframe(BOOKSHELF_ROWS, df.BOOKSHELF_HEADERS,
                                status_col=3, status_color_map=df.STATUS_WORD_COLORS)
    assert isinstance(result, dict)
    assert "data" in result and "headers" in result and "metadata" in result
    assert isinstance(result["metadata"], dict)
    assert "styling" in result["metadata"]
    # data / headers 原样透传
    assert result["data"] is BOOKSHELF_ROWS
    assert result["headers"] is df.BOOKSHELF_HEADERS


def test_styling_same_shape_as_data():
    """契约 2：styling 与 data 同形（行数、每行列数均相等）。"""
    result = df.style_dataframe(BOOKSHELF_ROWS, df.BOOKSHELF_HEADERS,
                                status_col=3, status_color_map=df.STATUS_WORD_COLORS)
    _assert_same_shape(result, BOOKSHELF_ROWS)


def test_no_status_col_all_empty():
    """契约 3：status_col=None 时，所有单元格样式均为 ''。"""
    result = df.style_dataframe(BOOKSHELF_ROWS, df.BOOKSHELF_HEADERS, status_col=None)
    styling = result["metadata"]["styling"]
    for row in styling:
        assert all(cell == "" for cell in row), "无状态列时所有单元格应为空样式"


def test_only_status_col_colored_others_empty():
    """契约 4：有 status_col 时，仅该列非空；其余列全为 ''。"""
    result = df.style_dataframe(BOOKSHELF_ROWS, df.BOOKSHELF_HEADERS,
                                status_col=3, status_color_map=df.STATUS_WORD_COLORS)
    styling = result["metadata"]["styling"]
    for r, d_row in enumerate(BOOKSHELF_ROWS):
        for c in range(len(d_row)):
            if c == 3:
                continue
            assert styling[r][c] == "", "非状态列单元格必须为空样式"


def test_status_col_hit_uses_map_color():
    """契约 4/5：状态列命中映射时，样式为 'color:<map[cell]>'。"""
    result = df.style_dataframe(BOOKSHELF_ROWS, df.BOOKSHELF_HEADERS,
                                status_col=3, status_color_map=df.STATUS_WORD_COLORS)
    styling = result["metadata"]["styling"]
    for r, d_row in enumerate(BOOKSHELF_ROWS):
        cell = d_row[3]
        expected_color = df.STATUS_WORD_COLORS[cell]
        assert styling[r][3].startswith("color:"), "状态列命中应写为 color: 前缀"
        assert styling[r][3] == f"color:{expected_color}", "颜色值应与映射一致"


def test_icon_color_map_hit():
    """契约 5：ICON_COLORS 命中校验（O3 队列图标列，status_col=0）。"""
    rows = [
        ["✅", "任务1", "done"],
        ["⏳", "任务2", "running"],
        ["❌", "任务3", "error"],
    ]
    result = df.style_dataframe(rows, ["状态", "名称", "备注"],
                                status_col=0, status_color_map=df.ICON_COLORS)
    styling = result["metadata"]["styling"]
    for r, d_row in enumerate(rows):
        icon = d_row[0]
        assert styling[r][0] == f"color:{df.ICON_COLORS[icon]}"
        # 非状态列仍为空
        assert styling[r][1] == "" and styling[r][2] == ""


def test_color_value_matches_palette_constants():
    """契约 5：具体色值校验（✅→#30D158，✅完成→#30D158，🟢进行中→#0A84FF 等）。"""
    result = df.style_dataframe(BOOKSHELF_ROWS, df.BOOKSHELF_HEADERS,
                                status_col=3, status_color_map=df.STATUS_WORD_COLORS)
    styling = result["metadata"]["styling"]
    assert styling[0][3] == f"color:{df.APPLE_GREEN}"      # ✅完成
    assert styling[1][3] == f"color:{df.APPLE_BLUE}"       # 🟢进行中
    assert styling[2][3] == f"color:{df.APPLE_ORANGE}"     # 🟡部分
    assert styling[3][3] == f"color:{df.APPLE_RED}"        # 🔴有失败
    assert styling[4][3] == f"color:{df.APPLE_GREY}"       # ⚪未开始


def test_unmatched_cell_falls_back_to_default_text():
    """契约 6：未命中 status_color_map 的单元格回落 default_text（默认 #E8E8ED）。"""
    rows = [["未知状态", "x"]]
    result = df.style_dataframe(rows, ["状态", "备注"],
                                status_col=0, status_color_map=df.STATUS_WORD_COLORS)
    styling = result["metadata"]["styling"]
    assert styling[0][0] == "color:#E8E8ED", "未命中应回落默认文字色 #E8E8ED"


def test_custom_default_text_used_for_unmatched():
    """契约 6：自定义 default_text 应用于未命中单元格。"""
    rows = [["未知状态", "x"]]
    result = df.style_dataframe(rows, ["状态", "备注"],
                                status_col=0, status_color_map=df.STATUS_WORD_COLORS,
                                default_text="#FF0000")
    styling = result["metadata"]["styling"]
    assert styling[0][0] == "color:#FF0000", "应使用自定义 default_text"


def test_status_col_with_none_map_falls_back_default():
    """契约 6 边界：status_col 指定但 status_color_map=None 时，整列回落 default_text。"""
    rows = [["✅完成", "x"]]
    result = df.style_dataframe(rows, ["状态", "备注"], status_col=0, status_color_map=None)
    styling = result["metadata"]["styling"]
    assert styling[0][0] == "color:#E8E8ED", "无映射时状态列单元格应回落默认色"


def test_empty_data_returns_valid_dict():
    """契约 7：空 data（[]）返回合法 dict，styling == []，不抛异常。"""
    result = df.style_dataframe([], df.BOOKSHELF_HEADERS,
                                status_col=3, status_color_map=df.STATUS_WORD_COLORS)
    assert isinstance(result, dict)
    assert result["data"] == []
    assert result["metadata"]["styling"] == []
    # 也支持 status_col=None 的空数据
    result2 = df.style_dataframe([], df.BOOKSHELF_HEADERS, status_col=None)
    assert result2["metadata"]["styling"] == []


def test_status_word_colors_keys_match_project_status():
    """契约 8：STATUS_WORD_COLORS 键集合 == _project_status 实际产出集合。"""
    import lib.project_manager as pm  # noqa: E402

    # 枚举 _project_status 各分支的代表输入
    samples = [
        pm._project_status(total=0, done=0, failed=0),                       # ⚪未开始
        pm._project_status(total=3, done=0, failed=1),                       # 🔴有失败
        pm._project_status(total=3, done=3, failed=0),                       # ✅完成
        pm._project_status(total=3, done=1, failed=1),                       # 🟡部分
        pm._project_status(total=3, done=0, failed=0),                       # ⚪未开始
        pm._project_status(total=3, done=2, failed=0),                       # 🟢进行中
    ]
    produced = set(samples)
    assert produced == set(df.STATUS_WORD_COLORS.keys()), (
        f"STATUS_WORD_COLORS 键 {set(df.STATUS_WORD_COLORS.keys())} "
        f"与 _project_status 产出 {produced} 不一致"
    )


def test_no_gradio_import_discipline():
    """纪律红线：本模块源码不得出现真实的 gradio 导入语句（保证可独立单测）。

    注意：用 AST 解析真实 import 节点，避免匹配文档字符串中出现的 'import gradio' 字面量。
    """
    import ast

    source = open(df.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "gradio", "lib/dataframe_style.py 禁止 `import gradio`"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "gradio", "lib/dataframe_style.py 禁止 `from gradio import ...`"


def test_exported_constants_present():
    """契约：导出的常量/表头存在且值正确。"""
    assert df.ICON_COLORS["✅"] == df.APPLE_GREEN
    assert len(df.ICON_COLORS) == 7, "ICON_COLORS 应有 7 个键"
    assert len(df.STATUS_WORD_COLORS) == 5, "STATUS_WORD_COLORS 应有 5 个键"
    assert df.BOOKSHELF_HEADERS == ["项目", "章", "段进度", "状态"]
    assert df.VOICE_HEADERS == ["名称", "分类", "大小(KB)", "试听"]
