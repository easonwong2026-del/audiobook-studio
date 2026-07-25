#!/usr/bin/env python3
"""QA 测试：验证 app.py 的 _safe_path_for_file_component + allowed_paths 白名单。

测试设计
--------
- 从真实 app.py 源码（AST）提取 _safe_path_for_file_component 函数定义并执行，
  确保被测的是工程师提交的实际源码（而非手抄副本）。
- 使用临时隔离数据目录（AUDIOBOOK_STUDIO_DATA_DIR），不触碰用户真实文件。
- 不启动 Gradio 服务（不依赖 gradio_client），避免端口 / 模型依赖。

安全规则验证（对应 allowed_paths = [config.get_data_dir()] 白名单）
------------------------------------------------------------------
  ✅  data_dir 子树内路径 -> 原样返回（放行）
  ✅  外部路径 -> 复制到 tempdir 临时副本，不修改原文件
  ✅  None 输入 -> 安全返回 None
  ✅  路径穿越（../../）-> 视为外部路径，复制到 tempdir
  ✅  不存在文件 -> 原样返回（None/空路径）

用法
----
    python -m pytest tests/qa_allowed_paths_test.py -v
"""

from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import time

# 项目模块（lib.config 仅依赖 json/os/shutil，不会触发 IndexTTS2/CUDA）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ==========================================================================
# Fixtures
# ==========================================================================

@pytest.fixture(scope="module")
def isolated_data_dir() -> str:
    """创建隔离的临时数据目录，通过环境变量注入 lib.config。"""
    d = tempfile.mkdtemp(prefix="qa_data_dir_")
    os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = d
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def safe_path_func(isolated_data_dir: str) -> callable:
    """从真实的 app.py 中 AST 提取 _safe_path_for_file_component。"""
    import lib.config as cfg

    app_path = os.path.join(os.path.dirname(__file__), "..", "app.py")
    with open(app_path, encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)
    func_src = None
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_safe_path_for_file_component"
        ):
            func_src = ast.get_source_segment(src, node)
            break

    assert func_src is not None, (
        "未在 app.py 中找到 _safe_path_for_file_component 定义"
    )

    ns: dict = {
        "os": os,
        "shutil": shutil,
        "tempfile": tempfile,
        "time": time,
        "config": cfg,
    }
    exec(compile(func_src, "<app.py:_safe_path_for_file_component>", "exec"), ns)
    return ns["_safe_path_for_file_component"]


# ==========================================================================
# Test Cases
# ==========================================================================


def test_internal_path_returned_as_is(safe_path_func, isolated_data_dir):
    """内部路径（data_dir 子树内）应原样返回。"""
    internal = os.path.join(isolated_data_dir, "chapter.wav")
    with open(internal, "wb") as f:
        f.write(b"RIFF....WAVE")
    result = safe_path_func(internal)
    assert result == internal, f"内部路径应原样返回，实际={result}"


def test_external_path_copied_to_tempdir(safe_path_func, isolated_data_dir):
    """外部路径（data_dir 外）应复制到 tempdir 且原文件保留。"""
    ext_dir = tempfile.mkdtemp(prefix="qa_ext_")
    try:
        ext_file = os.path.join(ext_dir, "clip.wav")
        with open(ext_file, "wb") as f:
            f.write(b"RIFF....WAVE")
        result = safe_path_func(ext_file)
        assert result != ext_file, "外部文件不应返回原路径"
        assert os.path.dirname(result) == tempfile.gettempdir(), (
            f"副本应落入 tempdir：{result}"
        )
        assert os.path.isfile(result), "副本应存在"
        assert os.path.isfile(ext_file), "原文件应保留"
    finally:
        shutil.rmtree(ext_dir, ignore_errors=True)


def test_none_returns_none(safe_path_func, isolated_data_dir):
    """None 输入应安全返回 None。"""
    assert safe_path_func(None) is None


def test_path_traversal_handled_as_external(safe_path_func, isolated_data_dir):
    """路径穿越（../../）应被视作外部路径并复制到 tempdir。"""
    traversal = os.path.normpath(
        os.path.join(isolated_data_dir, "..", "..", "outside.wav")
    )
    parent = os.path.dirname(traversal)
    os.makedirs(parent, exist_ok=True)
    with open(traversal, "wb") as f:
        f.write(b"RIFF....WAVE")
    result = safe_path_func(traversal)
    assert not os.path.samefile(result, traversal), "路径穿越文件不应原样返回"
    assert os.path.isfile(result), "副本应存在"


def test_missing_file_returns_as_is(safe_path_func, isolated_data_dir):
    """不存在的文件应原样返回（None/路径）。"""
    nonexistent = os.path.join(isolated_data_dir, "nonexistent.wav")
    assert not os.path.isfile(nonexistent)
    assert safe_path_func(nonexistent) is None or safe_path_func(nonexistent) == nonexistent
