"""BindingRepository 单元测试。

测试内容：
- list_categories 分类扫描
- copy_voice_file 文件复制
- validate_bindings 绑定校验
- resolve_binding_path 路径标准化
"""
from __future__ import annotations

import json
import os

import pytest

from repositories.binding_repo import BindingRepository
from lib import config as _cfg


class TestBindingRepository:
    """BindingRepository 业务逻辑测试。"""

    def test_list_categories_empty_dir(self, tmp_path):
        """空目录的 list_categories 返回空列表。"""
        # monkeypatch 环境变量让 voice_library 指向临时目录
        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = str(tmp_path / "data_empty")
        # 不创建 voice_library 目录
        cats = BindingRepository.list_categories()
        assert cats == []

    def test_list_categories_with_files(self, tmp_path):
        """音频文件分类扫描。"""
        data_dir = str(tmp_path / "data_with_files")
        voice_lib = os.path.join(data_dir, "voice_library")
        os.makedirs(voice_lib, exist_ok=True)

        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = data_dir

        # 创建测试音频文件
        for fname in ["温柔_张三.wav", "温柔_李四.wav", "沉稳_王五.wav", "noprefix.wav"]:
            open(os.path.join(voice_lib, fname), "a").close()
        # 非音频文件
        open(os.path.join(voice_lib, "readme.txt"), "a").close()

        cats = BindingRepository.list_categories()
        assert "温柔" in cats
        assert "沉稳" in cats
        assert "未分类" in cats
        assert len(cats) == 3

    def test_copy_voice_file_no_category(self, tmp_path):
        """copy_voice_file 无分类时复制。"""
        data_dir = str(tmp_path / "data_copy_nc")
        voice_lib = os.path.join(data_dir, "voice_library")
        os.makedirs(voice_lib, exist_ok=True)
        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = data_dir

        src = str(tmp_path / "source.wav")
        with open(src, "w") as f:
            f.write("dummy wav")

        dest = BindingRepository.copy_voice_file(src, "test_voice")
        assert os.path.isfile(dest)
        assert "test_voice.wav" in dest
        assert os.path.getsize(dest) == 9  # "dummy wav"

    def test_copy_voice_file_with_category(self, tmp_path):
        """copy_voice_file 有分类时按前缀命名。"""
        data_dir = str(tmp_path / "data_copy_cat")
        voice_lib = os.path.join(data_dir, "voice_library")
        os.makedirs(voice_lib, exist_ok=True)
        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = data_dir

        src = str(tmp_path / "source.wav")
        with open(src, "w") as f:
            f.write("dummy")

        dest = BindingRepository.copy_voice_file(src, "my_voice", "温暖")
        assert os.path.isfile(dest)
        assert "温暖_my_voice.wav" in dest or "温暖_my_voice.WAV" in dest

    def test_copy_voice_file_not_exists(self, tmp_path):
        """不存在的源文件抛出 FileNotFoundError。"""
        data_dir = str(tmp_path / "data_copy_err")
        os.environ["AUDIOBOOK_STUDIO_DATA_DIR"] = data_dir

        with pytest.raises(FileNotFoundError):
            BindingRepository.copy_voice_file(
                str(tmp_path / "no_such_file.wav"), "test")

    def test_validate_bindings_all_present(self, tmp_path):
        """所有绑定路径存在时返回空列表。"""
        project_dir = str(tmp_path / "project_a")
        os.makedirs(project_dir)
        voices_dir = os.path.join(project_dir, "voices")
        os.makedirs(voices_dir)

        # 创建参考音频文件
        ref = os.path.join(voices_dir, "旁白.wav")
        with open(ref, "w") as f:
            f.write("audio")

        # 写 voice_bindings.json
        bd = {
            "bindings": {"旁白": ref, "角色B": None},
            "bound_at": "",
            "verified": [],
        }
        with open(os.path.join(project_dir, "voice_bindings.json"), "w",
                  encoding="utf-8") as f:
            json.dump(bd, f)

        missing = BindingRepository.validate_bindings(project_dir)
        assert missing == []

    def test_validate_bindings_missing(self, tmp_path):
        """绑定路径不存在时返回缺失列表。"""
        project_dir = str(tmp_path / "project_b")
        os.makedirs(project_dir)

        bd = {
            "bindings": {"旁白": "/nonexistent/path.wav"},
            "bound_at": "",
            "verified": [],
        }
        with open(os.path.join(project_dir, "voice_bindings.json"), "w",
                  encoding="utf-8") as f:
            json.dump(bd, f)

        missing = BindingRepository.validate_bindings(project_dir)
        assert len(missing) == 1
        assert "nonexistent" in missing[0]

    def test_validate_bindings_no_file(self, tmp_path):
        """voice_bindings.json 不存在时返回错误。"""
        project_dir = str(tmp_path / "project_c")
        os.makedirs(project_dir)

        missing = BindingRepository.validate_bindings(project_dir)
        assert len(missing) == 1
        assert "不存在" in missing[0]

    def test_resolve_binding_path_absolute(self):
        """绝对路径直接返回。"""
        abs_path = "/absolute/path/voice.wav"
        result = BindingRepository.resolve_binding_path(abs_path, "/project")
        assert result == os.path.normpath(abs_path)

    def test_resolve_binding_path_relative(self):
        """相对路径拼接 project_dir。"""
        result = BindingRepository.resolve_binding_path(
            "voices/旁白.wav", "/project/dir")
        expected = os.path.normpath("/project/dir/voices/旁白.wav")
        assert result == expected
