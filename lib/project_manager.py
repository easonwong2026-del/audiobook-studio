"""项目管理：扫描／创建／打开／保存项目

数据目录（项目 / 产物）默认外置于程序目录（见 lib.config），并通过 legacy 目录
向后兼容打开旧版存放在程序内 workspace/projects 的历史项目。
"""
from __future__ import annotations

from .types import ProjectMeta
from .snapshot import ProjectSnapshot
from . import config as _cfg
from repositories.project_repo import ProjectRepository

# WORKSPACE_ROOT 保持为模块级可变变量（测试用 monkeypatch 覆盖）；
# 初值从配置读取，使项目默认存到程序目录之外。
WORKSPACE_ROOT = _cfg.get_projects_root()
# 旧版项目目录（程序目录内），仅用于向后兼容打开，不参与新建。
LEGACY_ROOT = _cfg.get_legacy_dir()


def _repository() -> type[ProjectRepository]:
    """Synchronize mutable compatibility roots and return the canonical repo."""
    ProjectRepository.WORKSPACE_ROOT = WORKSPACE_ROOT
    ProjectRepository.LEGACY_ROOT = LEGACY_ROOT
    ProjectRepository._INITIALIZED = True
    return ProjectRepository


def _resolve_dir(name: str) -> str:
    """Compatibility wrapper for the canonical repository resolver."""
    return _repository().get_project_dir(name)


def scan_projects() -> list[str]:
    """Compatibility wrapper for :meth:`ProjectRepository.scan_projects`."""
    return _repository().scan_projects()


def create_project(name: str, script_path: str) -> str:
    """Compatibility wrapper; new disk writes live only in the repository."""
    return _repository().create_project(name, script_path)


def open_project(name: str) -> tuple[ProjectMeta, dict, dict]:
    """Compatibility wrapper for the canonical load path."""
    return _repository().load_project(name)


def load_snapshot(name: str) -> "ProjectSnapshot":
    """Compatibility wrapper for the canonical snapshot path."""
    return _repository().load_snapshot(name)


def delete_project(name: str):
    """Compatibility wrapper for the canonical delete path."""
    return _repository().delete_project(name)


def get_project_dir(name: str) -> str:
    """Compatibility wrapper for the canonical project resolver."""
    return _repository().get_project_dir(name)


def update_segment_status(name: str, seg_id: str, status: str):
    """Compatibility wrapper for the canonical segment status mutation."""
    return _repository().update_segment_status(name, seg_id, status)


def get_remaining(name: str) -> list[str]:
    """Compatibility wrapper for the canonical recovery query."""
    return _repository().get_remaining(name)


def _meta_path(project_dir: str) -> str:
    return _repository()._meta_path(project_dir)


def _load_meta(project_dir: str) -> ProjectMeta:
    return _repository()._load_meta(project_dir)


def _repair_meta(project_dir: str, meta: ProjectMeta):
    return _repository()._repair_meta(project_dir, meta)


def _save_meta(project_dir: str, meta: ProjectMeta):
    return _repository()._save_meta(project_dir, meta)


def get_synthesis_overrides(name: str) -> dict:
    """读取项目的全局合成覆盖参数（``synthesis_overrides.json``）。

    该文件与 ``structured_script.json`` 解耦（非破坏性，不动源剧本）。
    文件不存在或解析失败时返回 ``{}``。

    Args:
        name: 项目名。

    Returns:
        覆盖参数字典（键见 ``set_synthesis_overrides``），缺省为 ``{}``。
    """
    return _repository().get_synthesis_overrides(name)


def set_synthesis_overrides(name: str, overrides: dict) -> None:
    """持久化全局合成覆盖参数到 ``synthesis_overrides.json``。

    非破坏性：仅写独立的覆盖文件，不改动 ``structured_script.json`` 源剧本。

    Args:
        name: 项目名。
        overrides: 覆盖参数字典，约定键：
            - ``emotion``: str 或 None（None=按剧本）。
            - ``override``: bool，是否覆盖 alpha / rate。
            - ``emo_alpha``: float。
            - ``speech_rate``: float。
    """
    return _repository().set_synthesis_overrides(name, overrides)


def _project_status(total: int, done: int, failed: int) -> str:
    """Compatibility wrapper for the shared bookshelf status derivation."""
    return _repository()._project_status(total, done, failed)


def get_synthesis_selections(name: str) -> dict:
    """读取项目的合成章节勾选持久化（``synthesis_selections.json``）。

    非破坏性：与 ``synthesis_overrides.json`` 同构的独立文件。不存在/解析失败返回 ``{}``。

    Args:
        name: 项目名。

    Returns:
        勾选字典（含 ``chapters`` 键为选中章节 id 列表），缺省为 ``{}``。
    """
    return _repository().get_synthesis_selections(name)


def set_synthesis_selections(name: str, selections: dict) -> None:
    """持久化合成章节勾选到 ``synthesis_selections.json``（非破坏性，同构 overrides）。

    Args:
        name: 项目名。
        selections: 勾选字典（约定含 ``chapters`` 键，值为选中章节 id 列表）。
    """
    return _repository().set_synthesis_selections(name, selections)


def build_role_choices(script: dict, bindings: dict, role_categories: dict | None = None) -> list[tuple]:
    """构造 v_role 分组 choices：(label, value)。

    - ``label`` = ``【分类】角色名``（分组展示用）。
    - ``value`` = 原始角色名（Gradio ``gr.Dropdown`` 事件回调拿到的是 value，
      保证 ``bind_voice`` 拿到原始角色名，安全）。

    Args:
        script: 剧本 dict（取 ``voices`` 键顺序）。
        bindings: 当前会话绑定表（``ss.bindings``），用于判定「未绑定」。
        role_categories: ``voice_bindings.json`` 的 ``role_categories`` 映射
            （bind_voice 时持久化）；缺省/为空时按「未绑定/未分类」处理。

    Returns:
        ``(label, value)`` 元组列表；分组顺序：有分类在前，「未绑定/未分类」置末。
    """
    role_categories = role_categories or {}
    groups: dict[str, list[str]] = {}
    for role in script.get("voices", {}).keys():
        cat = role_categories.get(role)  # bind_voice 时持久化的分类
        if not cat:
            cat = "未绑定" if not bindings.get(role) else "未分类"
        groups.setdefault(cat, []).append(role)
    # 分组顺序：已绑定分类在前，未绑定/未分类置末
    bound = [c for c in groups if c not in ("未绑定", "未分类")]
    tail = [c for c in ("未绑定", "未分类") if c in groups]
    choices: list[tuple] = []
    for cat in bound + tail:
        for role in sorted(groups[cat]):
            choices.append((f"【{cat}】{role}", role))
    return choices


def build_bound_role_choices(script: dict, bindings: dict) -> list[tuple]:
    """构造「已绑定音色角色」下拉 choices：(label, value)，供补录页使用。

    仅返回 ``bindings.get(role)`` 为真值（已绑定参考音频）的角色，未绑定角色
    不出现；标签沿用 ``build_role_choices`` 的分组风格（``【已绑定】角色名``），
    value 为原始角色名（Gradio 事件回调拿到的是 value，安全）。

    Args:
        script: 剧本 dict（取 ``voices`` 键顺序）。
        bindings: 当前会话绑定表（``ss.bindings``），仅取真值项。

    Returns:
        ``(label, value)`` 元组列表；仅包含已绑定角色（保持脚本 voice 顺序）。
    """
    choices: list[tuple] = []
    for role in script.get("voices", {}).keys():
        if bindings.get(role):
            choices.append((f"【已绑定】{role}", role))
    return choices
