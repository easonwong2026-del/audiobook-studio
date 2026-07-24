"""工作流测试：项目全生命周期（§10.3 1-10）

验证：
  1. 创建项目（project.json + structured_script.json + voice_bindings.json）
  2. 打开项���并验证 ProjectSnapshot 一致性
  3. 绑定音色 → bindings 中该角色有路径
  4. 删除项目 → 目录不存、scan 不再返回

使用 ProjectService / ProjectRepository 直调（不走 app.py / Gradio），
在 ``tmp_path`` 内完全隔离。
"""
import sys
import os
import json
import struct
import wave

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib.types import ProjectMeta  # noqa: E402
from lib.snapshot import ProjectSnapshot  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402
from repositories.exceptions import ProjectNotFoundError  # noqa: E402


# ── 假音频生成（可复用） ──────────────────────────────────────────────────────

def _make_fake_wav(path: str, sample_rate: int = 22050, duration: float = 0.5):
    """生成一个指定采样率和时长的静音 WAV 文件。"""
    n_samples = int(sample_rate * duration)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))


SCRIPT = {
    "meta": {"title": "测试书"},
    "voices": {"旁白": {"description": "沉稳男中音"}, "小明": {"description": "少年音"}},
    "chapters": [
        {
            "id": 1, "title": "第一章",
            "segments": [
                {"id": "s1", "role": "旁白", "text": "从前有座山。", "emotion": "neutral"},
                {"id": "s2", "role": "小明", "text": "山上有个庙。", "emotion": "neutral"},
            ],
        },
        {
            "id": 2, "title": "第二章",
            "segments": [
                {"id": "s3", "role": "旁白", "text": "庙里有个老和尚。", "emotion": "neutral"},
                {"id": "s4", "role": "小明", "text": "和一个小和尚。", "emotion": "neutral"},
            ],
        },
    ],
}


@pytest.fixture
def mini_project(tmp_path, monkeypatch):
    """在 tmp_path 下造一个最小 2 章 4 段 2 角色项目，返回 (project_dir, meta, script, bindings)。

    monkeypatch 在最前面设置环境变量，确保 ProjectRepository / project_manager
    在首次 import 后读取正确的 WORKSPACE_ROOT。
    """
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))
    # 重置仓库缓存
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True

    proj_dir = tmp_path / "projects" / "test_book"
    os.makedirs(proj_dir / "voices")
    os.makedirs(proj_dir / "segments")
    os.makedirs(proj_dir / "chapters")
    os.makedirs(proj_dir / "output")

    # 写 structured_script.json
    script_path = proj_dir / "structured_script.json"
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(SCRIPT, f, ensure_ascii=False, indent=2)

    # 写 project.json
    meta = ProjectMeta(
        project_name="test_book",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        total_chapters=2,
        total_segments=4,
        pending_count=4,
        segments_status={"s1": "pending", "s2": "pending", "s3": "pending", "s4": "pending"},
    )
    # 手动写 project.json（仿 _save_meta 格式）
    _write_meta(str(proj_dir), meta)

    # 写 voice_bindings.json（初始无绑定）
    bindings_dict = {
        "bindings": {"旁白": None, "小明": None},
        "bound_at": "2026-01-01T00:00:00",
        "verified": [],
    }
    with open(proj_dir / "voice_bindings.json", "w", encoding="utf-8") as f:
        json.dump(bindings_dict, f, ensure_ascii=False, indent=2)

    return str(proj_dir), meta, SCRIPT, bindings_dict


def _write_meta(project_dir: str, meta: ProjectMeta):
    """直接写 project.json（不依赖 project_manager._save_meta）。"""
    payload = {
        "project_name": meta.project_name,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "total_chapters": meta.total_chapters,
        "total_segments": meta.total_segments,
        "completed_count": meta.completed_count,
        "failed_count": meta.failed_count,
        "pending_count": meta.pending_count,
        "segments_status": meta.segments_status,
        "voice_bindings_path": meta.voice_bindings_path,
    }
    path = os.path.join(project_dir, "project.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_meta(project_dir: str) -> ProjectMeta:
    """从 project.json 加载 meta。"""
    with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
        data = json.load(f)
    return ProjectMeta(**data)


# ── 测试用例 ──────────────────────────────────────────────────────────────────


class TestProjectLifecycle:
    """项目全生命周期工作流测试。"""

    def test_create_project(self, tmp_path, monkeypatch):
        """创建项目 → scan_projects 返回该项目名。"""
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))
        ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
        ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
        ProjectRepository._INITIALIZED = True

        # 先写一个 script JSON 供创建用
        script_path = tmp_path / "script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(SCRIPT, f, ensure_ascii=False, indent=2)

        name = ProjectRepository.create_project("my_new_book", str(script_path))
        assert name == "my_new_book"

        names = ProjectRepository.scan_projects()
        assert "my_new_book" in names

        # 验证项目目录结构
        proj_dir = tmp_path / "projects" / "my_new_book"
        assert os.path.isdir(str(proj_dir / "voices"))
        assert os.path.isdir(str(proj_dir / "segments"))
        assert os.path.isdir(str(proj_dir / "chapters"))
        assert os.path.isdir(str(proj_dir / "output"))
        assert os.path.isfile(str(proj_dir / "structured_script.json"))
        assert os.path.isfile(str(proj_dir / "voice_bindings.json"))
        assert os.path.isfile(str(proj_dir / "project.json"))

    def test_open_project(self, tmp_path, monkeypatch):
        """打开已有项目 → ProjectSnapshot 含正确的 name/meta/script/bindings。"""
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))
        ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
        ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
        ProjectRepository._INITIALIZED = True

        # 准备项目
        script_path = tmp_path / "script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(SCRIPT, f, ensure_ascii=False, indent=2)
        ProjectRepository.create_project("open_test", str(script_path))

        # 通过 ProjectRepository 加载快��
        snap = ProjectRepository.load_snapshot("open_test")
        assert isinstance(snap, ProjectSnapshot)
        assert snap.name == "open_test"
        assert snap.meta.project_name == "open_test"
        assert snap.meta.total_chapters == 2
        assert snap.meta.total_segments == 4
        assert snap.meta.pending_count == 4
        # script 内容校验
        assert snap.script["meta"]["title"] == "测试书"
        assert len(snap.script["chapters"]) == 2
        assert len(snap.script["chapters"][0]["segments"]) == 2
        # bindings：初始全部为 None
        assert "旁白" in snap.bindings
        assert "小明" in snap.bindings
        assert snap.bindings["旁白"] is None
        assert snap.bindings["小明"] is None

    def test_bind_voice(self, tmp_path, monkeypatch):
        """绑定参考音频 → binding 写入 voice_bindings.json。"""
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))
        ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
        ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
        ProjectRepository._INITIALIZED = True

        # 准备项目
        script_path = tmp_path / "script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(SCRIPT, f, ensure_ascii=False, indent=2)
        ProjectRepository.create_project("bind_test", str(script_path))

        # 制造一个假参考音频
        ref_wav = tmp_path / "ref_旁白.wav"
        _make_fake_wav(str(ref_wav))

        # 读当前 bindings 并写入
        proj_dir = tmp_path / "projects" / "bind_test"
        bindings_path = proj_dir / "voice_bindings.json"
        with open(bindings_path, encoding="utf-8") as f:
            bd = json.load(f)
        bd["bindings"]["旁白"] = str(ref_wav)
        with open(bindings_path, "w", encoding="utf-8") as f:
            json.dump(bd, f, ensure_ascii=False, indent=2)

        # 重新加载快照，验证 bindings 已更新
        snap = ProjectRepository.load_snapshot("bind_test")
        assert snap.bindings["旁白"] == str(ref_wav)
        assert snap.bindings["小明"] is None  # 未绑定

    def test_delete_project(self, tmp_path, monkeypatch):
        """删除项目 → 目录消失、scan_projects 不再返回。"""
        monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path))
        ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
        ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
        ProjectRepository._INITIALIZED = True

        # 准备项目
        script_path = tmp_path / "script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(SCRIPT, f, ensure_ascii=False, indent=2)
        ProjectRepository.create_project("delete_me", str(script_path))
        assert "delete_me" in ProjectRepository.scan_projects()

        # 删除
        ProjectRepository.delete_project("delete_me")
        assert "delete_me" not in ProjectRepository.scan_projects()
        proj_dir = tmp_path / "projects" / "delete_me"
        assert not os.path.isdir(str(proj_dir))

        # 加载已删除项目应抛出异常
        with pytest.raises(ProjectNotFoundError):
            ProjectRepository.load_project("delete_me")
