"""工作流测试：数据目录切换（§10.4）

验证 ``config.set_data_dir(new_path)`` 后：
  - WorkspacePaths 返回新路径
  - 创建项目在新目录下而非旧目录

所有操作隔离在 tmp_path 内，不依赖外部文件系统。
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import config as _cfg  # noqa: E402
from repositories.config_repo import ConfigRepository  # noqa: E402
from repositories.project_repo import ProjectRepository  # noqa: E402
from services.project import ProjectService  # noqa: E402


SCRIPT = {
    "meta": {"title": "切换书"},
    "voices": {"旁白": {"description": "x"}},
    "chapters": [
        {
            "id": 1, "title": "一",
            "segments": [
                {"id": "1-001", "role": "旁白", "text": "第一段", "emotion": "neutral"},
            ],
        }
    ],
}


class TestDataDirSwitch:
    """数据目录切换工作流测试。"""

    @staticmethod
    def _setup_env(tmp_path, monkeypatch):
        """统一环境设置：清除环境变量，重定向 CONFIG_PATH。"""
        # 清除环境变量（让 config._data_dir_path 回退到 config.json 读取）
        monkeypatch.delenv("AUDIOBOOK_STUDIO_DATA_DIR", raising=False)
        monkeypatch.setenv("AUDIOBOOK_STUDIO_LEGACY_DIR", str(tmp_path / "legacy"))
        cfg_path = tmp_path / "cfg.json"
        # 同时 monkeypatch 两个 CONFIG_PATH：config.py 模块级 + ConfigRepository
        monkeypatch.setattr(_cfg, "CONFIG_PATH", str(cfg_path))
        monkeypatch.setattr(ConfigRepository, "CONFIG_PATH", str(cfg_path))
        return str(cfg_path)

    def test_data_dir_switch(self, tmp_path, monkeypatch):
        """set_data_dir 后 WorkspacePaths 返回新路径。"""
        old_dir = str(tmp_path / "old_data")
        new_dir = str(tmp_path / "new_data")
        self._setup_env(tmp_path, monkeypatch)

        ProjectService.set_data_dir(old_dir)

        # 验证 set_data_dir 写入了配置文件并更新 canonical roots
        ws = _cfg.get_workspace_paths()
        assert ws.data_dir == old_dir
        assert ws.projects_dir == os.path.join(old_dir, "projects")
        assert ProjectRepository.WORKSPACE_ROOT == os.path.join(old_dir, "projects")

        # 切换到新目录
        ProjectService.set_data_dir(new_dir)

        # 验证 WorkspacePaths 已更新
        ws2 = _cfg.get_workspace_paths()
        assert ws2.data_dir == new_dir
        assert ws2.projects_dir == os.path.join(new_dir, "projects")
        assert ws2.voice_library_dir == os.path.join(new_dir, "voice_library")
        assert ws2.preview_dir == os.path.join(new_dir, "preview")

        # 验证 config.json 持久化了新路径
        with open(os.path.join(str(tmp_path), "cfg.json"), encoding="utf-8") as f:
            cfg_data = json.load(f)
        assert cfg_data["data_dir"] == new_dir

    def test_create_project_in_new_dir(self, tmp_path, monkeypatch):
        """切换数据目录后，新项目创建在新目录下。"""
        old_dir = str(tmp_path / "old_data")
        new_dir = str(tmp_path / "new_data")
        self._setup_env(tmp_path, monkeypatch)

        ProjectService.set_data_dir(old_dir)

        script_path = tmp_path / "script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(SCRIPT, f, ensure_ascii=False, indent=2)
        ProjectService.create_project("old_project", str(script_path))

        # 确认项目在旧目录
        assert os.path.isdir(os.path.join(old_dir, "projects", "old_project"))

        # 切换到新目录并更新 canonical repository 路径
        ProjectService.set_data_dir(new_dir)
        # 将旧目录设为 legacy，使 canonical scan 能找到旧项目
        monkeypatch.setattr(ProjectRepository, "LEGACY_ROOT", os.path.join(old_dir, "projects"))

        # 在新目录创建第二个项目
        ProjectService.create_project("new_project", str(script_path))

        # 验证新项目在新目录
        assert os.path.isdir(os.path.join(new_dir, "projects", "new_project"))
        # 旧项目仍在旧目录（不移动）
        assert os.path.isdir(os.path.join(old_dir, "projects", "old_project"))
        # scan 通过 legacy 找到旧项目 + 新目录找到新项目
        names = ProjectService.scan_projects()
        assert "old_project" in names, f"旧项目应在 scan 结果中: {names}"
        assert "new_project" in names, f"新项目应在 scan 结果中: {names}"
