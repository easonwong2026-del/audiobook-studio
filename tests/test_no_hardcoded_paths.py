"""5.7：验证代码库中无个人电脑绝对路径硬编码，仓库可整体移动。

检查的范围：
- .py 文件中不应出现特定用户路径（如 C:\\Users\\xxx 或 /home/xxx）；
- launcher.py 使用相对路径推导 PYTHON；
- config.py 使用显式配置 / 环境变量获取模型目录；
- lib/tts_engine.py 中使用 config.get_model_dir() 而非硬编码路径；
- start.bat 使用相对路径（%~dp0）。

注意：允许 test 文件引用测试临时目录（tempfile）。
允许第三方包和 .venv 中有固定路径（那是 pip 安装的产物，与本项目无关）。
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pytest

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要检查的 Python 源文件（排除 .venv/ __pycache__/ 和第三方包）
PYTHON_SOURCE_FILES = [
    "app.py",
    "launcher.py",
    "start.bat",
    "lib/config.py",
    "lib/tts_engine.py",
    "lib/audio_format.py",
    "lib/environment.py",
    "lib/audio_pipeline.py",
    "lib/script_loader.py",
    "lib/segment_cache.py",
    "lib/project_manager.py",
    "lib/voice_lib.py",
    "lib/progress.py",
    "lib/dataframe_style.py",
    "services/supplement.py",
    "services/synthesis.py",
    "services/project.py",
    "services/session.py",
    "services/export.py",
]

# 匹配 Windows 绝对路径（C:\Users\xxx 或 C:/Users/xxx）
WINDOWS_USER_PATH_RE = re.compile(
    r'[a-zA-Z]:[\\/]Users[\\/]\w+',
    re.IGNORECASE,
)

# 匹配 Unix 绝对路径（/home/xxx 或 /Users/xxx）
UNIX_USER_PATH_RE = re.compile(
    r'[/]home[/]\w+|[/]Users[/]\w+',
    re.IGNORECASE,
)


def _read_file(path):
    """读取文件内容，按行返回。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _is_allowed_pattern(line, filepath):
    """判断一行中的路径是否是允许的（注释、文档字符串、已知模式）。"""
    stripped = line.strip()
    # 允许注释中的示例路径
    if stripped.startswith("#") or stripped.startswith("//"):
        return True
    # 允许 docstring
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    # 允许 .gitignore 中的模式
    if filepath.endswith(".gitignore"):
        return True
    # 检查是否是字符串字面量中的路径
    # 只检查非注释行
    return False


@pytest.mark.parametrize("rel_path", PYTHON_SOURCE_FILES)
class TestNoHardcodedPaths:
    """验证源文件中没有硬编码的个人绝对路径"""

    def test_no_windows_user_abs_path(self, rel_path):
        """Windows C:\\Users\\xxx 绝对路径不应出现在源码中"""
        abs_path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.isfile(abs_path):
            pytest.skip(f"文件不存在: {rel_path}")
        content = _read_file(abs_path)
        matches = WINDOWS_USER_PATH_RE.findall(content)
        # 过滤掉注释行和 docstring 中的匹配
        offending = []
        for m in set(matches):
            # 找包含此匹配的行
            for i, line in enumerate(content.splitlines()):
                if m in line and not _is_allowed_pattern(line, rel_path):
                    offending.append((i + 1, line.strip()))
        if offending:
            lines_str = "; ".join(f"L{ln}: {txt}" for ln, txt in offending)
            pytest.fail(f"{rel_path} 包含 Windows 用户绝对路径:\n{lines_str}")

    def test_no_unix_user_abs_path(self, rel_path):
        """Unix /home/xxx 或 /Users/xxx 绝对路径不应出现在源码中"""
        abs_path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.isfile(abs_path):
            pytest.skip(f"文件不存在: {rel_path}")
        content = _read_file(abs_path)
        matches = UNIX_USER_PATH_RE.findall(content)
        offending = []
        for m in set(matches):
            for i, line in enumerate(content.splitlines()):
                if m in line and not _is_allowed_pattern(line, rel_path):
                    offending.append((i + 1, line.strip()))
        if offending:
            lines_str = "; ".join(f"L{ln}: {txt}" for ln, txt in offending)
            pytest.fail(f"{rel_path} 包含 Unix 用户绝对路径:\n{lines_str}")

    def test_launcher_uses_relative_python(self, rel_path):
        """launcher.py 不使用硬编码 python 路径"""
        if "launcher.py" not in rel_path and "launcher" not in rel_path:
            pytest.skip("仅检查 launcher.py")
        abs_path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.isfile(abs_path):
            pytest.skip(f"文件不存在: {rel_path}")
        content = _read_file(abs_path)
        # 检查没有 C:\\Users 或类似路径
        if "C:\\Users" in content or "C:/Users" in content:
            pytest.fail(f"{rel_path} 包含硬编码个人路径")


class TestConfigPaths:
    """验证 config.py 和 tts_engine.py 使用配置化的路径"""

    def test_config_uses_env_or_default(self):
        """config.py 模型目录使用环境变量或默认值，无硬编码路径"""
        cfg_path = os.path.join(PROJECT_ROOT, "lib", "config.py")
        content = _read_file(cfg_path)
        # ��查 get_model_dir 函数使用 ENV_MODEL_DIR
        assert "ENV_MODEL_DIR" in content, "config.py 应引�� ENV_MODEL_DIR"
        assert "_DEFAULT_MODEL_DIR" in content, "config.py 应有 _DEFAULT_MODEL_DIR"

    def test_tts_engine_uses_get_model_dir(self):
        """tts_engine.py 使用 config.get_model_dir() 而非硬编码路径"""
        tts_path = os.path.join(PROJECT_ROOT, "lib", "tts_engine.py")
        content = _read_file(tts_path)
        assert "_cfg.get_model_dir()" in content or "config.get_model_dir()" in content, \
            "tts_engine.py 应通过 config.get_model_dir() 获取模型目录"


class TestStartBatRelativePaths:
    """start.bat 使用相对路径"""

    def test_start_bat_no_absolute_paths(self):
        """start.bat 使用 %~dp0 而非 C:\\Users\\..."""
        bat_path = os.path.join(PROJECT_ROOT, "start.bat")
        if not os.path.isfile(bat_path):
            pytest.skip("start.bat 不存在")
        content = _read_file(bat_path)
        assert "%~dp0" in content, "start.bat 应使用 %~dp0 相对路径"
        if "C:\\Users" in content or "C:/Users" in content:
            pytest.fail("start.bat 包含硬编码个人路径")
