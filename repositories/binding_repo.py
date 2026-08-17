"""BindingRepository：音色绑定业务逻辑（非 I/O）。

职责边界：
- 不做 voice_bindings.json 的 I/O（归 ProjectRepository）
- 只做：分类扫描（list_categories）、文件复制（copy_voice_file）、
  绑定校验（validate_bindings）、路径标准化（resolve_binding_path）
"""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)


class BindingRepository:
    """音色绑定业务逻辑仓库。

    全部 @staticmethod，无实例状态。
    依赖 config.get_voice_library() 获取音色库目录路径。
    """

    @staticmethod
    def _get_voice_library() -> str:
        """延迟导入 lib.config，避免循环依赖。"""
        from lib import config as _cfg
        return _cfg.get_voice_library()

    @staticmethod
    def _category_of(filename: str) -> str:
        """由文件名推导分类：首个 _ 之前的部分；无 _ → 未分类。

        与 ``lib/voice_lib.py`` 的 _category_of 规则一致。
        """
        base = os.path.splitext(filename)[0]
        if "_" in base:
            return base.split("_", 1)[0]
        return "未分类"

    @staticmethod
    def list_categories() -> list[str]:
        """从 voice_library 目录扫描文件名前缀推导分类。

        扫描 config.get_voice_library() 目录，对每个音频文件调用
        _category_of 推导分类（首个 _ 之前的部分），去重排序后返回。

        Returns:
            分类名称列表（排序、去重）；目录不存在时返回空列表。
        """
        root = BindingRepository._get_voice_library()
        if not os.path.isdir(root):
            return []
        categories: set[str] = set()
        # 支持的音频扩展名（与 voice_lib.py 一致）
        voice_exts = (".wav", ".mp3", ".flac", ".ogg")
        for name in os.listdir(root):
            if not os.path.isfile(os.path.join(root, name)):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in voice_exts:
                categories.add(BindingRepository._category_of(name))
        return sorted(categories)

    @staticmethod
    def copy_voice_file(source_path: str, name: str,
                        category: str = "") -> str:
        """复制音频到 voice_library，按分类前缀命名；返回目标路径。

        目标文件名规则：
        - 有分类且非 "未分类"：{category}_{name}{ext}
        - 无分类：{name}{ext}

        Args:
            source_path: 源音频路径。
            name: 音频名称（不含扩展名）。
            category: 分类名称（可选）。

        Returns:
            目标文件绝对路径。

        Raises:
            FileNotFoundError: 源文件不存在。
            OSError: 复制失败。
        """
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"源音频文件不存在: {source_path}")
        voice_lib = BindingRepository._get_voice_library()
        os.makedirs(voice_lib, exist_ok=True)
        ext = os.path.splitext(source_path)[1].lower() or ".wav"
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        if category and category.strip() and category != "未分类":
            safe_cat = "".join(c if c.isalnum() or c in "-_" else "_"
                               for c in category.strip())
            dest_name = f"{safe_cat}_{safe_name}{ext}"
        else:
            dest_name = f"{safe_name}{ext}"
        dest = os.path.join(voice_lib, dest_name)
        shutil.copy2(source_path, dest)
        return dest

    @staticmethod
    def validate_bindings(project_dir: str) -> list[str]:
        """检查 voice_bindings.json 中所有绑定音频路径是否存在。

        Args:
            project_dir: 项目目录绝对路径。

        Returns:
            缺失的音频路径列表（空列表表示全部存在）。
        """
        missing: list[str] = []
        from lib import project_paths

        bindings_path = project_paths.project_file(project_dir, "voice_bindings")
        if not os.path.isfile(bindings_path):
            return ["voice_bindings.json 不存在"]
        import json
        try:
            with open(bindings_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            return [f"voice_bindings.json 解析失败: {exc}"]

        bindings = data.get("bindings", {}) if isinstance(data, dict) else {}
        for role, path in bindings.items():
            if path is None:
                continue
            abs_path = path
            if not os.path.isabs(abs_path):
                try:
                    abs_path = project_paths.resolve_relative(project_dir, abs_path)
                except ValueError:
                    abs_path = os.path.join(project_dir, abs_path)
            if not os.path.isfile(abs_path):
                missing.append(abs_path)
        return missing

    @staticmethod
    def resolve_binding_path(path: str, project_dir: str) -> str:
        """统一化绑定路径：相对路径 → project_dir 绝对路径；已绝对 → 直接返回。

        Args:
            path: 绑定路径（可能相对或绝对）。
            project_dir: 项目目录绝对路径。

        Returns:
            标准化后的绝对路径。
        """
        if os.path.isabs(path):
            return os.path.normpath(path)
        from lib import project_paths

        try:
            return os.path.normpath(project_paths.resolve_relative(project_dir, path))
        except ValueError:
            return os.path.normpath(os.path.join(project_dir, path))
