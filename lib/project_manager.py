"""项目管理：扫描／创建／打开／保存项目

数据目录（项目 / 产物）默认外置于程序目录（见 lib.config），并通过 legacy 目录
向后兼容打开旧版存放在程序内 workspace/projects 的历史项目。
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import time

from .types import ProjectMeta
from .snapshot import ProjectSnapshot
from . import chapter_identity, project_paths, segment_cache
from . import config as _cfg

logger = logging.getLogger(__name__)

# WORKSPACE_ROOT 保持为模块级可变变量（测试用 monkeypatch 覆盖）；
# 初值从配置读取，使项目默认存到程序目录之外。
WORKSPACE_ROOT = _cfg.get_projects_root()
# 旧版项目目录（程序目录内），仅用于向后兼容打开，不参与新建。
LEGACY_ROOT = _cfg.get_legacy_dir()


def _resolve_dir(name: str) -> str:
    """返回项目实际目录：优先新数据目录，其次 legacy 目录，否则落在新目录。"""
    new = os.path.join(WORKSPACE_ROOT, name)
    if os.path.isdir(new):
        return new
    old = os.path.join(LEGACY_ROOT, name)
    if os.path.isdir(old):
        return old
    return new


def scan_projects() -> list[str]:
    """扫描所有项目名（新数据目录 + legacy 目录合并，新目录优先去重）。"""
    names = set()
    for root in (WORKSPACE_ROOT, LEGACY_ROOT):
        if os.path.isdir(root):
            names.update(
                d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))
            )
    return sorted(names)


def create_project(name: str, script_path: str) -> str:
    """创建项目目录结构，复制 JSON，写 project.json（始终落在新数据目录）。"""
    project_dir = os.path.join(WORKSPACE_ROOT, name)
    if os.path.exists(project_dir):
        raise FileExistsError(f"项目 '{name}' 已存在")

    paths = project_paths.ensure_layout(project_dir, prefer_canonical=True, compatibility=True)

    # 复制剧本 JSON
    with open(script_path, encoding="utf-8") as f:
        script = chapter_identity.normalize_script_for_project(json.load(f))
    with open(os.path.join(project_dir, "structured_script.json"), "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    shutil.copy2(script_path, os.path.join(paths["source"], chapter_identity.safe_filename(os.path.basename(script_path))))
    for index, chapter in enumerate(script.get("chapters", [])):
        if isinstance(chapter, dict):
            with open(os.path.join(paths["chapter_text"], f"{chapter_identity.chapter_file_stem(chapter, index, len(script.get('chapters', [])))}.json"), "w", encoding="utf-8") as f:
                json.dump(chapter, f, ensure_ascii=False, indent=2)
    total_segments = 0
    for ch in script.get("chapters", []):
        total_segments += len(ch.get("segments", []))

    # 空 voice_bindings
    voice_bindings = {
        "bindings": {name: None for name in script.get("voices", {})},
        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "verified": []
    }
    with open(os.path.join(project_dir, "voice_bindings.json"), "w", encoding="utf-8") as f:
        json.dump(voice_bindings, f, ensure_ascii=False, indent=2)
    shutil.copy2(
        os.path.join(project_dir, "voice_bindings.json"),
        os.path.join(paths["voices"], "voice_bindings.json"),
    )

    # project.json
    meta = ProjectMeta(
        project_name=name,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        total_chapters=len(script.get("chapters", [])),
        total_segments=total_segments,
        pending_count=total_segments,
        segments_status={
            seg["id"]: "pending"
            for ch in script.get("chapters", [])
            for seg in ch.get("segments", [])
        },
        storage_version=project_paths.STORAGE_VERSION,
        directories=project_paths.layout_manifest(project_dir),
        source_file=os.path.relpath(
            os.path.join(paths["source"], chapter_identity.safe_filename(os.path.basename(script_path))),
            project_dir,
        ),
    )
    _save_meta(project_dir, meta)
    if meta.storage_version >= project_paths.STORAGE_VERSION:
        try:
            shutil.copy2(
                os.path.join(project_dir, "project.json"),
                os.path.join(paths["config"], "project.json"),
            )
        except OSError as exc:
            logger.warning("同步项目配置副本失败: %s", exc)

    return name


def open_project(name: str) -> tuple[ProjectMeta, dict, dict]:
    """加载项目，返回 (meta, script, voice_bindings)。向后兼容 legacy 目录。"""
    project_dir = _resolve_dir(name)
    if not os.path.isdir(project_dir):
        raise FileNotFoundError(f"项目 '{name}' 不存在")

    meta = _load_meta(project_dir)
    with open(os.path.join(project_dir, "structured_script.json"), encoding="utf-8") as f:
        script = json.load(f)
    with open(os.path.join(project_dir, "voice_bindings.json"), encoding="utf-8") as f:
        bindings = json.load(f)

    return meta, script, bindings


def load_snapshot(name: str) -> "ProjectSnapshot":
    """加载项目并产出 ``ProjectSnapshot``（供打开项目统一入口复用，自动拆出子键）。"""
    meta, script, bd = open_project(name)
    return ProjectSnapshot.build(name, meta, script, bd, _resolve_dir(name))


def delete_project(name: str):
    """删除项目（解析到实际存在的目录）。"""
    project_dir = _resolve_dir(name)
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir)


def get_project_dir(name: str) -> str:
    """返回项目目录绝对路径（解析 legacy，便于读取既有项目产物）。"""
    return _resolve_dir(name)


def update_segment_status(name: str, seg_id: str, status: str):
    """更新单段状态并写入 project.json。"""
    project_dir = _resolve_dir(name)
    meta = _load_meta(project_dir)
    meta.segments_status[seg_id] = status

    # 重新统计
    meta.completed_count = sum(1 for s in meta.segments_status.values() if s == "done")
    meta.failed_count = sum(1 for s in meta.segments_status.values() if s == "failed")
    meta.pending_count = sum(1 for s in meta.segments_status.values() if s == "pending")
    meta.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    _save_meta(project_dir, meta)


def get_remaining(name: str) -> list[str]:
    """返回所有待合成的段 ID（pending + failed + done 但 wav 不存在）。"""
    project_dir = _resolve_dir(name)
    meta = _load_meta(project_dir)
    seg_dir = project_paths.project_dir(project_dir, "segments")
    remaining = []
    for seg_id, status in meta.segments_status.items():
        # ``skipped`` is a selection marker, not a completed production
        # result.  It must become eligible again when a later all-book job (or
        # a matching chapter job) is started.
        if status in ("pending", "failed", "skipped"):
            remaining.append(seg_id)
        elif status == "done":
            # B7：用参数感知的缓存键判定（兼容历史裸文件 + glob 任意参数变体），
            #     标记 done 但对应 wav 实际不存在 → 重置为 pending。
            if not segment_cache.has_segment_wav(seg_dir, seg_id):
                meta.segments_status[seg_id] = "pending"
                meta.completed_count -= 1
                meta.pending_count += 1
                remaining.append(seg_id)
    if meta.completed_count < 0: meta.completed_count = 0
    _save_meta(project_dir, meta)
    return remaining


def _meta_path(project_dir: str) -> str:
    return os.path.join(project_dir, "project.json")


def _load_meta(project_dir: str) -> ProjectMeta:
    with open(_meta_path(project_dir), encoding="utf-8") as f:
        data = json.load(f)
    meta = ProjectMeta(**data)
    _repair_meta(project_dir, meta)
    return meta


def _repair_meta(project_dir: str, meta: ProjectMeta):
    """自动修复: 确保 segments_status 的 key 与 structured_script.json 的 seg_id 一致"""
    script_path = os.path.join(project_dir, "structured_script.json")
    if not os.path.isfile(script_path):
        return
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    # 收集 JSON 中所有段 ID
    json_ids = set()
    for ch in script.get("chapters", []):
        for seg in ch.get("segments", []):
            json_ids.add(seg["id"])

    # 已有的 status 键
    old_ids = set(meta.segments_status.keys())

    if json_ids == old_ids:
        return  # 一致，无需修复

    logger.info(f"Repairing segments_status: {len(old_ids)} → {len(json_ids)} IDs")
    # 重建：保留 done 状态，其他重置为 pending
    new_status = {}
    for sid in json_ids:
        old_status = meta.segments_status.get(sid, "pending")
        # B7：用参数感知缓存键判定该段是否已真正合成（兼容历史裸文件）。
        seg_dir = project_paths.project_dir(project_dir, "segments")
        if old_status == "done" and segment_cache.has_segment_wav(seg_dir, sid):
            new_status[sid] = "done"
        else:
            new_status[sid] = "pending"

    meta.segments_status = new_status
    meta.total_segments = len(json_ids)
    meta.completed_count = sum(1 for v in new_status.values() if v == "done")
    meta.failed_count = sum(1 for v in new_status.values() if v == "failed")
    meta.pending_count = meta.total_segments - meta.completed_count - meta.failed_count
    _save_meta(project_dir, meta)


def _save_meta(project_dir: str, meta: ProjectMeta):
    path = _meta_path(project_dir)
    tmp_path = path + ".tmp"
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
        "storage_version": meta.storage_version,
        "directories": meta.directories,
        "source_file": meta.source_file,
    }
    # 原子写：先写临时文件，fsync 后再 os.replace 替换，
    # 避免写入中途崩溃（断电 / 异常）留下半截 project.json（R4）。
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def get_synthesis_overrides(name: str) -> dict:
    """读取项目的全局合成覆盖参数（``synthesis_overrides.json``）。

    该文件与 ``structured_script.json`` 解耦（非破坏性，不动源剧本）。
    文件不存在或解析失败时返回 ``{}``。

    Args:
        name: 项目名。

    Returns:
        覆盖参数字典（键见 ``set_synthesis_overrides``），缺省为 ``{}``。
    """
    project_dir = _resolve_dir(name)
    path = os.path.join(project_dir, "synthesis_overrides.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("读取 synthesis_overrides.json 失败，回退空覆盖: %s", exc)
        return {}


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
    project_dir = _resolve_dir(name)
    path = os.path.join(project_dir, "synthesis_overrides.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)


def list_projects() -> list[dict]:
    """扫描所有项目并产出多书摘要（O4 书架用，纯函数无 gradio）。

    逐项目读 ``project.json`` meta（轻量，不读剧本），产出
    ``{name, chapters, done, failed, total, progress, status}`` 摘要。
    状态色块（§11.7）：✅完成 / 🟢进行中 / 🟡部分 / ⚪未开始 / 🔴有失败。

    Returns:
        项目摘要字典列表，按项目名排序。
    """
    summaries = []
    for name in scan_projects():
        try:
            project_dir = _resolve_dir(name)
            meta = _load_meta(project_dir)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("list_projects 读 %s 失败: %s", name, exc)
            continue
        total = getattr(meta, "total_segments", 0) or 0
        done = getattr(meta, "completed_count", 0) or 0
        failed = getattr(meta, "failed_count", 0) or 0
        status = _project_status(total, done, failed)
        progress = (done / total) if total else 0.0
        summaries.append({
            "name": name,
            "chapters": getattr(meta, "total_chapters", 0) or 0,
            "done": done,
            "failed": failed,
            "total": total,
            "progress": progress,
            "status": status,
        })
    return summaries


def _project_status(total: int, done: int, failed: int) -> str:
    """推导书架状态色块符号（§11.7）。"""
    if total == 0:
        return "⚪未开始"
    if failed > 0 and done == 0:
        return "🔴有失败"
    if done == total and failed == 0:
        return "✅完成"
    if failed > 0:
        return "🟡部分"
    if done == 0:
        return "⚪未开始"
    return "🟢进行中"


def build_chapter_tree(project: str) -> str:
    """产出章节折叠树 HTML（<details>，无 gradio，O4 右栏展示）。

    读取项目 meta（段完成态）+ 剧本（章节/段结构），生成原生折叠树：
    每章一个 ``<details>``，summary 显示「第N章 标题（完成/总）」，内部列出
    每段（状态图标 + 段 ID + 角色 + 文本预览）。

    Args:
        project: 项目名。

    Returns:
        HTML 字符串；项目不存在时返回提示文本。
    """
    try:
        meta, script, _ = open_project(project)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("build_chapter_tree 读 %s 失败: %s", project, exc)
        return "<i>未打开项目</i>"
    status_map = meta.segments_status
    lines = []
    for chapter_index, ch in enumerate(script.get("chapters", [])):
        segs = ch.get("segments", [])
        done_n = sum(1 for s in segs if status_map.get(s["id"]) == "done")
        lines.append(
            f"<details><summary>📖 {chapter_identity.chapter_label(ch, chapter_index, len(script.get('chapters', [])))}（{done_n}/{len(segs)} 完成）</summary>"
        )
        for seg in segs:
            sid = seg["id"]
            st = status_map.get(sid, "pending")
            icon = "✅" if st == "done" else ("❌" if st == "failed" else "⬜")
            text = (seg.get("text", "") or "")[:40]
            lines.append(
                f"<div style='margin-left:18px;font-size:13px'>"
                f"{icon} <b>{sid}</b> [{seg.get('role', '')}] {text}</div>"
            )
        lines.append("</details>")
    return "\n".join(lines)


def get_synthesis_selections(name: str) -> dict:
    """读取项目的合成章节勾选持久化（``synthesis_selections.json``）。

    非破坏性：与 ``synthesis_overrides.json`` 同构的独立文件。不存在/解析失败返回 ``{}``。

    Args:
        name: 项目名。

    Returns:
        勾选字典（含 ``chapters`` 键为选中章节 id 列表），缺省为 ``{}``。
    """
    project_dir = _resolve_dir(name)
    path = os.path.join(project_dir, "synthesis_selections.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("读取 synthesis_selections.json 失败，回退空: %s", exc)
        return {}


def set_synthesis_selections(name: str, selections: dict) -> None:
    """持久化合成章节勾选到 ``synthesis_selections.json``（非破坏性，同构 overrides）。

    Args:
        name: 项目名。
        selections: 勾选字典（约定含 ``chapters`` 键，值为选中章节 id 列表）。
    """
    project_dir = _resolve_dir(name)
    path = os.path.join(project_dir, "synthesis_selections.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(selections if isinstance(selections, dict) else {}, f,
                  ensure_ascii=False, indent=2)


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
