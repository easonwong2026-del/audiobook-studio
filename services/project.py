"""项目服务：包 ``lib.project_manager`` + ``lib.script_loader``（禁止 import gradio）。

把项目的 CRUD / 校验 / 绑定等纯业务逻辑从 ``app.py`` 抽离到这里，使 UI 层只负责
编排与展示。所有方法均为 ``staticmethod``，无实例状态，便于单测。

注意：角色绑定（``bind_voice``）会**原地修改**传入会话的 ``bindings`` 字典
（见 ``app.bind_voice`` 调用 ``ss.bindings[role] = dest``），而非通过返回值回传——
这与 R1「多标签各自持有独立 ``SessionState``、原地 mutate」的约定一致。
``save_to_lib`` ���把音频存入 ``voice_library`` 目录，不改变角色绑定表。

阶段四重构：所有磁盘操作从 ``lib.project_manager (pm)`` 改为
``repositories.ProjectRepository`` / ``ConfigRepository``。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Optional

from lib import script_loader
from lib import config
from repositories.project_repo import ProjectRepository
from repositories.config_repo import ConfigRepository

logger = logging.getLogger(__name__)


def _safe_name(s: str) -> str:
    """把任意字符串转成安全的文件名（替换文件系统非法字符为下划线）。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


class ProjectService:
    """项目 CRUD / 校验 / 绑定。包装 ``ProjectRepository`` + ``lib.script_loader``。"""

    @staticmethod
    def scan_projects() -> list[str]:
        """扫描所有项目名。"""
        return ProjectRepository.scan_projects()

    @staticmethod
    def validate_script_file(script_file: str) -> list[str]:
        """加载并校验剧本文件，返回错误列表（空列表表示通过）。"""
        script = script_loader.load_script(script_file)
        return script_loader.validate_script(script)

    @staticmethod
    def create_project(name: str, script_file: str) -> None:
        """创建项目（包 ``ProjectRepository.create_project``）。"""
        ProjectRepository.create_project(name, script_file)

    @staticmethod
    def open_project(name: str):
        """打开项目，返回 ``(meta, script, voice_bindings)`` 元组。"""
        return ProjectRepository.load_project(name)

    @staticmethod
    def open_project_as_snapshot(name: str):
        """打开项目并返回 ``ProjectSnapshot``（含自动拆出的 bindings / role_categories）。"""
        return ProjectRepository.load_snapshot(name)

    @staticmethod
    def list_projects() -> list[dict]:
        """O4：扫描所有项目并产出多书摘要。"""
        return ProjectRepository.list_projects()

    @staticmethod
    def delete_project(name: str) -> None:
        """删除项目。"""
        ProjectRepository.delete_project(name)

    @staticmethod
    def get_project_dir(name: str) -> str:
        """返回项目目录绝对路径。"""
        return ProjectRepository.get_project_dir(name)

    @staticmethod
    def update_segment_status(name: str, seg_id: str, status: str) -> None:
        """更新单段状态（包 ``ProjectRepository.update_segment_status``）。"""
        ProjectRepository.update_segment_status(name, seg_id, status)

    @staticmethod
    def set_data_dir(new_dir: str) -> str:
        """设置并持久化数据目录，且立即对本会话生效（新项目 / 扫描切到新目录）。

        Args:
            new_dir: 用户指定的新数据根目录（绝对或相对路径均可）。

        Returns:
            规范化后的绝对路径。
        """
        d = ConfigRepository.set_data_dir(new_dir)
        # 立即让本次运行切到新目录（WORKSPACE_ROOT 为模块级可变变量，可被覆盖）。
        ProjectRepository.WORKSPACE_ROOT = config.get_projects_root()
        return d

    @staticmethod
    def bind_voice(project: str, role: str, audio_path: str, category: str = "未分类") -> str:
        """拷贝参考音频到项目 ``voices/`` 并更新 ``voice_bindings.json``。

        Args:
            project: 项目名。
            role: 角色名。
            audio_path: 参考音频源路径。
            category: 该角色绑定音色的分类（用于 ``v_role`` 下拉分组），
                默认 ``"未分类"``。写入 ``voice_bindings.json`` 的 ``role_categories``
                映射（additive，旧项目无此键向后兼容）。

        Returns:
            拷贝后的参考音频绝对路径。

        Raises:
            ValueError: project / role / audio_path 任一为空。
        """
        if not project or not role or not audio_path:
            raise ValueError("bind_voice 需要 project / role / audio_path 均非空")
        d = ProjectRepository.get_project_dir(project)
        vd = os.path.join(d, "voices")
        os.makedirs(vd, exist_ok=True)
        ext = os.path.splitext(audio_path)[1] or ".wav"
        dest = os.path.join(vd, f"{_safe_name(role)}{ext}")
        shutil.copy2(audio_path, dest)

        # 使用 ProjectRepository 读写 bindings（原子写）
        bd = ProjectRepository.load_bindings(d)
        if not bd:
            bd = {"bindings": {}, "bound_at": "", "verified": []}
        bd["bindings"][role] = dest
        bd.setdefault("role_categories", {})[role] = category
        bd["bound_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        ProjectRepository.save_bindings(d, bd)
        return dest

    @staticmethod
    def save_to_lib(recorded: Optional[str], uploaded: Optional[str], name: str,
                    category: str = "") -> str:
        """把录制 / 上传的音频保存到 ``voice_library``，返回目标绝对路径。

        5.2：音色库路径在「调用时」动态解析 ``config.get_voice_library()``，不再在
        模块导入期缓存。这样运行期切换数据目录后，新保存的音色会自动落到新目录，
        旧路径缓存失效问题不再发生。

        Args:
            recorded: 录制得到的音频路径。
            uploaded: 上传得到的音频路径。
            name: 音频名称（用于文件名）。
            category: 分类名称（非空时文件名前缀为 ``{category}_{name}``）。

        Returns:
            保存后的音频绝对路径。

        Raises:
            ValueError: 名称为空，或 recorded / uploaded 均未提供。
        """
        if not name:
            raise ValueError("请填写音频名称")
        audio_file = recorded or uploaded
        if not audio_file:
            raise ValueError("请先录制或上传音频")
        # 5.2：动态读取当前数据目录下的音色库（切换数据目录后立即生效）
        voice_lib = config.get_voice_library()
        os.makedirs(voice_lib, exist_ok=True)
        ext = os.path.splitext(audio_file)[1] or ".wav"
        safe = _safe_name(name)
        # 有分类时前缀 {category}_{name}，与 voice_lib._category_of 推导规则一致
        prefix = f"{_safe_name(category)}_" if category and category.strip() and category != "未分类" else ""
        dest = os.path.join(voice_lib, f"{prefix}{safe}{ext}")
        shutil.copy2(audio_file, dest)
        return dest
