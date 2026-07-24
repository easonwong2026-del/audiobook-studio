"""QA 独立验证：复现原始 bug 场景，确认 script_loader 诊断修复有效。

独立运行（不依赖 pytest，纯标准库）：
  python -u tests/qa_verify_script_loader_fix.py
"""
import sys
import os
import json
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import script_loader as sl  # noqa: E402


def banner(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def fmt_as_app(errors):
    """复刻 app.py:269-271 的文案拼接方式。"""
    return "### ❌ 剧本校验失败：\n" + "\n".join(f"- {e}" for e in errors)


def scenario_only_meta():
    banner("场景1：只含 meta、无 voices/chapters（复现原始 bug）")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "meta_only.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"meta": {"title": "只有元信息"}}, f, ensure_ascii=False)
    script = sl.load_script(p)
    errors = sl.validate_script(script)
    print("[errors 列表]")
    for e in errors:
        print("  -", e)
    assert errors, "应触发校验失败"
    blob = "\n".join(errors)
    assert "未定义任何角色" in blob, "① 必须仍报未定义任何角色"
    assert "未定义任何章节" in blob, "① 必须仍报未定义任何章节"
    assert "顶层 key" in blob, "② 诊断段必须含顶层 key 列表"
    assert "'meta'" in blob, "② 诊断段必须显示实际顶层 key (meta)"
    assert "voices" in blob and "chapters" in blob, "② 必须提示缺 voices/chapters"
    assert "```json" in blob, "② 必须给出最小合法示例"
    print("\n[app.py 文案拼接效果]")
    print(fmt_as_app(errors))
    print("✅ 场景1通过：旧提示无诊断价值的问题已修复，文案可操作")


def scenario_alias():
    banner("场景2：顶层 key 用 characters/sections 别名")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "alias.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"title": "别名书"},
            "characters": {"旁白": {"description": "沉稳男中音"}},
            "sections": [
                {"id": 1, "title": "一",
                 "segments": [{"id": "1-001", "role": "旁白", "text": "旁白开场"}]}
            ],
        }, f, ensure_ascii=False)
    script = sl.load_script(p)
    print("voices:", dict(script.voices))
    print("chapters count:", len(script.chapters))
    errors = sl.validate_script(script)
    assert script.voices, "别名 characters 应被解析为 voices"
    assert script.chapters, "别名 sections 应被解析为 chapters"
    assert errors == [], f"别名格式应通过校验，但得到: {errors}"
    print("✅ 场景2通过：别名兼容解析且校验通过")


def scenario_not_object():
    banner("场景3a：顶层是数组（合法 JSON，但非对象）")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "arr.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump([{"voices": {}}], f, ensure_ascii=False)
    script = sl.load_script(p)
    errors = sl.validate_script(script)
    print("[errors 列表]")
    for e in errors:
        print("  -", e)
    assert errors, "应触发校验失败"
    blob = "\n".join(errors)
    assert "未定义任何角色" in blob and "未定义任何章节" in blob
    assert "不是对象" in blob or "顶层" in blob, "必须提示顶层结构非对象"
    print("✅ 场景3a通过：顶层数组不崩溃且产出可读诊断")

    banner("场景3b：空对象 {}")
    d2 = tempfile.mkdtemp()
    p2 = os.path.join(d2, "empty.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False)
    script2 = sl.load_script(p2)
    errors2 = sl.validate_script(script2)
    print("[errors 列表]")
    for e in errors2:
        print("  -", e)
    assert errors2
    blob2 = "\n".join(errors2)
    assert "未定义任何角色" in blob2 and "未定义任何章节" in blob2
    assert "顶层 key" in blob2
    print("✅ 场景3b通过：空对象产出可读诊断")


def scenario_unknown_role():
    banner("场景4：segment role 未定义且别名不掩盖")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "bad_role.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"title": "坏书"},
            "voices": {"旁白": {"description": "x"}},
            "chapters": [
                {"id": 1, "title": "一",
                 "segments": [{"id": "1-001", "role": "幽灵角色", "text": "未知"}]}
            ],
        }, f, ensure_ascii=False)
    script = sl.load_script(p)
    errors = sl.validate_script(script)
    assert any("幽灵角色" in e for e in errors), "应指出未定义角色"
    assert "幽灵角色" not in script.voices, "别名逻辑不得把缺失 role 当合法"
    print("✅ 场景4通过：未知角色被指出且不被别名掩盖")


def scenario_existing_project():
    banner("场景5：既有项目 离婚141 (v2.1) 兼容性回归")
    proj = os.path.join(PROJECT_ROOT, "workspace", "projects", "离婚141",
                        "structured_script.json")
    assert os.path.exists(proj), f"项目文件缺失: {proj}"
    script = sl.load_script(proj)
    errors = sl.validate_script(script)
    assert errors == [], f"既有成功项目应校验通过，但得到: {errors}"
    print("voices:", list(script.voices.keys()))
    print("chapters count:", len(script.chapters))
    print("✅ 场景5通过：离婚141 校验通过、无误报、解析行为不变")


def main():
    scenario_only_meta()
    scenario_alias()
    scenario_not_object()
    scenario_unknown_role()
    scenario_existing_project()
    banner("结论")
    print("全部独立验证场景通过 ✅")


if __name__ == "__main__":
    main()
