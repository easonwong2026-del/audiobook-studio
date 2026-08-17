from lib import project_paths
"""单元测试：lib/project_manager.py + lib/script_loader.py

验证：
  - 项目状态机：create_project / update_segment_status 计数（done/failed/pending）
  - get_remaining：标记 done 但 wav 实际缺失时重置为 pending，completed_count 不为负（B12 相关健壮性）
  - script_loader.load_script + validate_script：角色不在 voices 中时返回错误列表（B12 校验函数可用）
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.project_manager as pm  # noqa: E402
import lib.script_loader as sl  # noqa: E402


SCRIPT_VALID = {
    "meta": {"title": "测试书"},
    "voices": {
        "旁白": {"description": "沉稳男中音"},
        "小明": {"description": "少年音"},
    },
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段内容"},
                {"id": "1-002", "role": "小明", "text": "第二段内容"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "2-001", "role": "旁白", "text": "第三段内容"},
                {"id": "2-002", "role": "小明", "text": "第四段内容"},
            ],
        },
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """用临时目录作为 WORKSPACE_ROOT，并创建一个 2 章 4 段 2 角色的项目。"""
    monkeypatch.setattr(pm, "WORKSPACE_ROOT", str(tmp_path))
    script_path = tmp_path / "structured_script.json"
    script_path.write_text(json.dumps(SCRIPT_VALID, ensure_ascii=False), encoding="utf-8")
    name = "testbook"
    pm.create_project(name, str(script_path))
    return name


def test_create_project_counts(project):
    meta, script, bindings = pm.open_project(project)
    assert meta.total_segments == 4
    assert meta.total_chapters == 2
    assert meta.pending_count == 4
    assert meta.completed_count == 0
    assert meta.failed_count == 0
    # voice_bindings 中每个角色初始为 None
    assert set(bindings["bindings"].keys()) == {"旁白", "小明"}
    assert all(v is None for v in bindings["bindings"].values())


def test_update_status_done_and_failed(project):
    pm.update_segment_status(project, "1-001", "done")
    meta, _, _ = pm.open_project(project)
    assert meta.completed_count == 1
    assert meta.pending_count == 3
    assert meta.failed_count == 0

    pm.update_segment_status(project, "1-001", "failed")
    meta, _, _ = pm.open_project(project)
    assert meta.failed_count == 1
    assert meta.completed_count == 0
    assert meta.pending_count == 3


def test_get_remaining_resets_missing_done(project, tmp_path):
    pm.update_segment_status(project, "1-001", "done")
    seg_dir = project_paths.project_dir(pm.get_project_dir(project), "segments", create=True)
    os.makedirs(seg_dir, exist_ok=True)
    wav_path = os.path.join(seg_dir, "1-001.wav")
    with open(wav_path, "w", encoding="utf-8") as f:
        f.write("dummy")
    # 标记 done 并已写出 wav，随后删除 wav（模拟“显示完成但实际文件丢失”）
    os.remove(wav_path)

    remaining = pm.get_remaining(project)
    assert "1-001" in remaining, "缺失 wav 的 done 段应回到 remaining"
    meta, _, _ = pm.open_project(project)
    assert meta.segments_status["1-001"] == "pending"
    assert meta.completed_count >= 0, "completed_count 不应为负"


def test_validate_script_detects_unknown_role(tmp_path):
    bad = {
        "meta": {"title": "坏书"},
        "voices": {"旁白": {"description": "x"}},
        "chapters": [
            {
                "id": 1, "title": "一",
                "segments": [
                    {"id": "1-001", "role": "幽灵角色", "text": "未知角色段落"},
                ],
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    script = sl.load_script(str(p))
    errors = sl.validate_script(script)
    assert errors, "应返回非空错误列表"
    assert any("幽灵角色" in e for e in errors)

    # 合法剧本：所有段 role 都在 voices 中
    good = tmp_path / "good.json"
    good.write_text(json.dumps(SCRIPT_VALID, ensure_ascii=False), encoding="utf-8")
    script2 = sl.load_script(str(good))
    assert sl.validate_script(script2) == [], "合法剧本应无错误"


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 O2 / 2.4：synthesis_overrides.json 持久化（全局合成覆盖读写）
# ─────────────────────────────────────────────────────────────────────────────

def test_set_get_synthesis_overrides_roundtrip(project):
    """写入后读出一致（覆盖参数字典原样还原）。"""
    ov = {"emotion": "happy", "override": True, "emo_alpha": 0.8, "speech_rate": 1.2}
    pm.set_synthesis_overrides(project, ov)
    assert pm.get_synthesis_overrides(project) == ov


def test_get_synthesis_overrides_missing_returns_empty(project):
    """覆盖文件不存在时返回 {}（不抛异常）。"""
    assert pm.get_synthesis_overrides(project) == {}


def test_get_synthesis_overrides_corrupt_file_returns_empty(project):
    """覆盖文件损坏（非法 JSON）时安全回退为 {}。"""
    ov_path = project_paths.project_file(pm.get_project_dir(project), "synthesis_overrides")
    with open(ov_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    assert pm.get_synthesis_overrides(project) == {}


def test_set_synthesis_overrides_non_destructive(project):
    """set 仅写独立覆盖文件，不改动 structured_script.json 源剧本。"""
    script_path = project_paths.project_file(pm.get_project_dir(project), "structured_script")
    before = open(script_path, encoding="utf-8").read()
    pm.set_synthesis_overrides(project, {"emotion": "sad"})
    after = open(script_path, encoding="utf-8").read()
    assert before == after, "set_synthesis_overrides 不应改动 structured_script.json"
    ov_path = project_paths.project_file(pm.get_project_dir(project), "synthesis_overrides")
    assert os.path.isfile(ov_path), "覆盖应写入独立的 synthesis_overrides.json"
