"""项目快照：打开项目后的内存一致视图（避免页面间重复读盘 + 脏检测）。

仅依赖 ``lib.types``（ProjectMeta）与 ``ProjectRepository``（在重载方法内延迟
import 以避免循环依赖），不依赖 gradio / services 层，因此可被单元测试直接 import。

设计要点：
- ``script`` / ``meta`` 来自磁盘读出的原始 dict / dataclass；
- ``bindings`` 是 ``voice_bindings.json`` 的 ``bindings`` 子键（角色 -> 参考音频路径）；
- ``role_categories`` 是 ``voice_bindings.json`` 的 ``role_categories`` 子键；
- ``loaded_at`` 记录加载时刻，供 ``is_stale`` 做脏检测。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from lib.types import ProjectMeta


@dataclass
class ProjectSnapshot:
    """打开项目后的一次性内存视图（页面刷新统一读源，避免重复解析磁盘 JSON）。"""

    name: str
    project_dir: str
    meta: ProjectMeta
    script: dict
    bindings: dict[str, str] = field(default_factory=dict)
    role_categories: dict[str, str] = field(default_factory=dict)
    loaded_at: float = field(default_factory=time.time)

    @classmethod
    def build(cls, name, meta, script, bindings, project_dir):
        """由 ``open_project`` 的三元组产出快照，拆出内层 ``bindings`` / ``role_categories``。

        Args:
            name: 项目名。
            meta: ``ProjectMeta`` 实例。
            script: 结构化剧本（raw dict）。
            bindings: ``open_project`` 返回的完整 ``voice_bindings`` dict（含 ``bindings`` /
                ``role_categories`` 子键）。
            project_dir: 项目目录绝对路径（供 ``is_stale`` 脏检测）。
        """
        inner = bindings.get("bindings", {}) if isinstance(bindings, dict) else {}
        rc = bindings.get("role_categories", {}) if isinstance(bindings, dict) else {}
        return cls(
            name=name,
            project_dir=project_dir,
            meta=meta,
            script=script,
            bindings=inner,
            role_categories=rc,
            loaded_at=time.time(),
        )

    def is_stale(self) -> bool:
        """检测磁盘关键文件是否在加载后发生变更（任一文件 mtime 晚于 loaded_at 即视为脏）。"""
        if not self.project_dir or not os.path.isdir(self.project_dir):
            return True
        from lib import project_paths
        for key in (
            "project_meta", "structured_script", "voice_bindings",
            "character_roster", "voice_cast",
        ):
            p = project_paths.project_file(self.project_dir, key)
            if os.path.isfile(p) and os.path.getmtime(p) > self.loaded_at:
                return True
        return False

    def reload_if_stale(self) -> "ProjectSnapshot":
        """若磁盘已变更则重新加载并返回新快照，否则返回自身（不重复读盘）。"""
        if not self.is_stale():
            return self
        from repositories.project_repo import ProjectRepository

        meta, script, bd = ProjectRepository.load_project(self.name)
        return ProjectSnapshot.build(self.name, meta, script, bd, self.project_dir)
