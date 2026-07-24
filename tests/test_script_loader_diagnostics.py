"""script_loader 诊断性校验 / 健壮解析的回归测试。

覆盖：
1. 规范 JSON → 校验通过（无 errors）；
2. 只含 meta 的 JSON → 触发带诊断信息（含顶层 key 列表）的校验失败；
3. 顶层 key 别名（characters/sections）→ 兼容解析通过；
4. 非法 JSON（残缺 {）→ load_script 抛出 json.JSONDecodeError（友好提示路径）；
5. segment 的 role 未在 voices 中定义 → 报对应错误且不被别名逻辑掩盖；
6. 空对象 {} / 顶层数组 → 给出可读诊断；
7. 既有成功项目（离婚141, version 2.1）→ 仍校验通过（兼容性回归）。

纯 Python 标准库 + lib 模块，无需 gradio / index-tts。
运行：python -u tests/test_script_loader_diagnostics.py
"""
import sys
import os
import json
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import script_loader as sl  # noqa: E402


def _write(tmp_path, name, obj):
    """写入 JSON 文件，返回路径。obj 为 None 时按原始文本写入。"""
    p = os.path.join(tmp_path, name)
    if isinstance(obj, (dict, list)):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    else:
        with open(p, "w", encoding="utf-8") as f:
            f.write(obj)
    return p


def _tmp(tmp_path):
    return tmp_path if tmp_path else tempfile.mkdtemp()


# ─────────────────────────────────────────────────────────────────────────────
# 1. 规范 JSON 校验通过
# ─────────────────────────────────────────────────────────────────────────────
def test_standard_script_passes(tmp_path=None):
    d = _tmp(tmp_path)
    good = {
        "meta": {"title": "规范书", "version": "2.0"},
        "voices": {"旁白": {"description": "x"}, "小明": {"description": "y"}},
        "chapters": [
            {"id": 1, "title": "一",
             "segments": [{"id": "1-001", "role": "旁白", "text": "嗨", "emotion": "neutral"}]}
        ],
    }
    path = _write(d, "good.json", good)
    script = sl.load_script(path)
    assert sl.validate_script(script) == [], "规范 JSON 应无错误"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 只含 meta → 带诊断信息（含顶层 key 列表）的校验失败
# ─────────────────────────────────────────────────────────────────────────────
def test_only_meta_triggers_diagnostic_with_top_keys(tmp_path=None):
    d = _tmp(tmp_path)
    path = _write(d, "meta_only.json", {"meta": {"title": "只有元信息"}})
    script = sl.load_script(path)
    errors = sl.validate_script(script)
    assert errors, "只含 meta 应触发校验失败"
    blob = "\n".join(errors)
    # 诊断信息需包含顶层 key 列表，且用户能立刻看到只缺了 voices/chapters
    assert "顶层 key" in blob, "诊断信息应列出检测到的顶层 key"
    assert "'meta'" in blob, "诊断信息应显示实际检测到的顶层 key（如 'meta'）"
    assert "voices" in blob and "chapters" in blob, "应提示缺少 voices/chapters"
    assert "未定义任何角色" in blob and "未定义任何章节" in blob


# ─────────────────────────────────────────────────────────────────────────────
# 3. 顶层 key 别名兼容（characters → voices, sections → chapters）
# ─────────────────────────────────────────────────────────────────────────────
def test_alias_keys_compatible(tmp_path=None):
    d = _tmp(tmp_path)
    alias = {
        "meta": {"title": "别名书"},
        "characters": {"旁白": {"description": "沉稳男中音"}},
        "sections": [
            {"id": 1, "title": "一",
             "segments": [{"id": "1-001", "role": "旁白", "text": "旁白开场"}]}
        ],
    }
    path = _write(d, "alias.json", alias)
    script = sl.load_script(path)
    assert script.voices, "别名 characters 应被兼容解析为 voices"
    assert script.chapters, "别名 sections 应被兼容解析为 chapters"
    assert sl.validate_script(script) == [], "别名格式应校验通过"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 非法 JSON → load_script 抛出 json.JSONDecodeError（友好提示路径）
# ─────────────────────────────────────────────────────────────────────────────
def test_illegal_json_raises(tmp_path=None):
    d = _tmp(tmp_path)
    path = _write(d, "bad.json", "{")  # 残缺 JSON
    with pytest.raises(json.JSONDecodeError):
        sl.load_script(path)


# ─────────────────────────────────────────────────────────────────────────────
# 5. segment 的 role 未在 voices 中定义 → 报对应错误且不被别名逻辑掩盖
# ─────────────────────────────────────────────────────────────────────────────
def test_unknown_role_not_masked_by_alias(tmp_path=None):
    d = _tmp(tmp_path)
    bad = {
        "meta": {"title": "坏书"},
        "voices": {"旁白": {"description": "x"}},
        "chapters": [
            {"id": 1, "title": "一",
             "segments": [{"id": "1-001", "role": "幽灵角色", "text": "未知角色段落"}]}
        ],
    }
    path = _write(d, "unknown_role.json", bad)
    script = sl.load_script(path)
    errors = sl.validate_script(script)
    assert errors, "应返回非空错误列表"
    assert any("幽灵角色" in e for e in errors), "应指出未定义的角色名"
    # 别名逻辑不应把缺失的 role 误判为合法
    assert script.voices, "voices 应正常解析"
    assert "幽灵角色" not in script.voices


# ─────────────────────────────────────────────────────────────────────────────
# 6a. 空对象 {} → 诊断信息指出缺少 voices/chapters
# ─────────────────────────────────────────────────────────────────────────────
def test_empty_object_diagnostic(tmp_path=None):
    d = _tmp(tmp_path)
    path = _write(d, "empty.json", {})
    script = sl.load_script(path)
    errors = sl.validate_script(script)
    assert errors
    blob = "\n".join(errors)
    assert "未定义任何角色" in blob and "未定义任何章节" in blob
    assert "顶层 key" in blob


# ─────────────────────────────────────────────────────────────────────────────
# 6b. 顶层是数组而非对象 → 给出可读诊断，不会崩溃
# ─────────────────────────────────────────────────────────────────────────────
def test_array_top_level_diagnostic(tmp_path=None):
    d = _tmp(tmp_path)
    path = _write(d, "arr.json", [{"voices": {}}])  # 合法 JSON 但顶层是数组
    script = sl.load_script(path)
    errors = sl.validate_script(script)
    assert errors, "顶层数组应触发校验失败"
    blob = "\n".join(errors)
    assert "未定义任何角色" in blob and "未定义任何章节" in blob
    assert "不是对象" in blob or "顶层" in blob, "应提示顶层结构非对象"


# ─────────────────────────────────────────────────────────────────────────────
# 7. 既有成功项目（离婚141, version 2.1）→ 仍校验通过（兼容性回归）
# ─────────────────────────────────────────────────────────────────────────────
def test_existing_project_still_valid():
    proj = os.path.join(PROJECT_ROOT, "workspace", "projects", "离婚141",
                        "structured_script.json")
    if not os.path.exists(proj):
        pytest.skip("既有项目文件不存在，跳过兼容性回归")
    script = sl.load_script(proj)
    assert sl.validate_script(script) == [], "既有成功项目应仍校验通过"


if __name__ == "__main__":
    # 支持 `python -u tests/test_script_loader_diagnostics.py` 直接运行
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))
