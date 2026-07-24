"""5.2：WorkspacePaths 动态解析 & 数据目录切换。

覆盖：
- WorkspacePaths 各字段正确解析；
- 切换数据目录后 WorkspacePaths 返回新路径；
- 多轮目录切换路径正确。
"""
from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import config  # noqa: E402


class TestWorkspacePaths:
    def setup_method(self):
        """每个测试开始前备份环境变量"""
        self._old_data_dir = os.environ.pop(config.ENV_DATA_DIR, None)
        self._old_legacy_dir = os.environ.pop(config.ENV_LEGACY_DIR, None)

    def teardown_method(self):
        """每个测试结束后恢复环境变量"""
        for var, val in [(config.ENV_DATA_DIR, self._old_data_dir),
                          (config.ENV_LEGACY_DIR, self._old_legacy_dir)]:
            if val is not None:
                os.environ[var] = val
            else:
                os.environ.pop(var, None)

    def test_basic_paths(self):
        """设置一个临时数据目录，验证 WorkspacePaths 各子路径正确"""
        tmp = os.path.join(os.path.dirname(__file__), "__test_ws_tmp")
        os.environ[config.ENV_DATA_DIR] = tmp
        # 重新读取配置
        ws = config.get_workspace_paths()
        assert ws.data_dir == tmp
        assert ws.projects_dir == os.path.join(tmp, "projects")
        assert ws.voice_library_dir == os.path.join(tmp, "voice_library")
        assert ws.preview_dir == os.path.join(tmp, "preview")
        assert ws.task_cache_dir == os.path.join(tmp, "preview", "supplement_tasks")

    def test_switch_data_dir(self):
        """切换数据目录后 WorkspacePaths 返回新路径"""
        tmp1 = os.path.join(os.path.dirname(__file__), "__test_ws_tmp1")
        tmp2 = os.path.join(os.path.dirname(__file__), "__test_ws_tmp2")

        os.environ[config.ENV_DATA_DIR] = tmp1
        ws1 = config.get_workspace_paths()
        assert ws1.data_dir == tmp1

        os.environ[config.ENV_DATA_DIR] = tmp2
        ws2 = config.get_workspace_paths()
        assert ws2.data_dir == tmp2
        assert ws2.projects_dir == os.path.join(tmp2, "projects")

    def test_no_env_var_fallback(self):
        """未设置环境变量时，get_workspace_paths 使用 config.json 或默认路径"""
        # 清除环境变量
        os.environ.pop(config.ENV_DATA_DIR, None)
        ws = config.get_workspace_paths()
        # 无环境变量时优先级：config.json → ~/AudiobookStudio
        # 本机 config.json 可能设置了 data_dir，不做特定值断言，只验证路径非空
        assert ws.data_dir, "data_dir 不应为空字符串"
        assert os.path.isabs(ws.data_dir), "data_dir 应为绝对路径"

    def test_voice_library_path_dynamic(self):
        """验证 voice_library_dir 随 data_dir 变化而变化"""
        tmp_a = os.path.join(os.path.dirname(__file__), "__test_ws_vl_a")
        tmp_b = os.path.join(os.path.dirname(__file__), "__test_ws_vl_b")

        os.environ[config.ENV_DATA_DIR] = tmp_a
        ws_a = config.get_workspace_paths()
        assert ws_a.voice_library_dir == os.path.join(tmp_a, "voice_library")

        os.environ[config.ENV_DATA_DIR] = tmp_b
        ws_b = config.get_workspace_paths()
        assert ws_b.voice_library_dir == os.path.join(tmp_b, "voice_library")
