"""Storage v3 migration — failure-injection / rollback integrity (P1-A).

这些测试在 v2 → v3 迁移的 *执行过程中* 注入失败，验证回滚后：

- storage_version 回到 2；
- 所有被迁移「就地重写」的 authoritative state（project.json / quality_state.json
  / voice_bindings.json / voice_cast.json / production_tasks.sqlite3）内容完整恢复；
- 不存在「位置回到 v2、但内容仍是 v3」的混合态；
- 真实业务服务（ProjectRepository / QualityService / TaskRepository /
  ExportService）读出的仍是原 v2 数据。

故意先用「只恢复固定 root JSON」的旧 rollback 实现跑一次，确认 R2/R3 能真实
暴露 hybrid-state bug；再用统一的 ``_restore_mutated_state_from_backup`` 修复后
全部 PASS。
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from lib import project_paths
from repositories.project_repo import ProjectRepository
from repositories.project_storage_repo import ProjectStorageRepository
from repositories.quality_repo import QualityRepository
from repositories.task_repo import TaskRepository, TaskRecord
from services.export import ExportService
from services.project_storage import ProjectStorageService
from services.quality import QualityService

_V2_SEG = "05_分段音频/1-001.wav"
_V2_EXPORT = "09_导出文件/exports/t1/book.json"
_V2_VOICE = "04_角色与声音/旁白.mp3"
_V2_SOURCE = "02_原始文件/book.json"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_rich_v2_project(tmp_path, monkeypatch, name="v2rich"):
    """构造一个内容丰富的 v2 项目：含 quality / voice / task DB / export 等可重写状态。"""
    data_root = tmp_path / "data"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_root))
    ProjectRepository.WORKSPACE_ROOT = str(data_root / "projects")
    ProjectRepository.LEGACY_ROOT = str(data_root / "legacy")
    ProjectRepository._INITIALIZED = True
    project_dir = data_root / "projects" / name
    os.makedirs(project_dir, exist_ok=True)
    for directory in (
        "01_项目配置", "02_原始文件", "03_章节文本", "04_角色与声音",
        "05_分段音频", "06_章节音频", "07_合并音频", "08_质检记录",
        "09_导出文件", "cache", "logs",
    ):
        os.makedirs(project_dir / directory, exist_ok=True)

    script = {
        "meta": {"title": "测试书", "author": "测试"},
        "voices": {"旁白": {"description": "测试"}},
        "chapters": [{
            "id": 1, "title": "第一章",
            "segments": [{"id": "1-001", "role": "旁白", "emotion": "neutral", "text": "测试"}],
        }],
    }
    meta = {
        "project_name": name, "storage_version": 2,
        "total_segments": 1, "segments_status": {"1-001": "done"},
        "source_file": _V2_SOURCE,
        "voice_bindings_path": "voice_bindings.json",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }
    (project_dir / "project.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (project_dir / "structured_script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (project_dir / "voice_bindings.json").write_text(json.dumps({
        "bindings": {"旁白": _V2_VOICE},
        "role_bindings": {"r1": {"project_voice_path": _V2_VOICE}},
    }, ensure_ascii=False), encoding="utf-8")
    (project_dir / "voice_cast.json").write_text(json.dumps({
        "roles": {"旁白": {"project_voice_path": _V2_VOICE}},
    }, ensure_ascii=False), encoding="utf-8")
    (project_dir / "02_原始文件" / "book.json").write_text("{}", encoding="utf-8")
    (project_dir / "04_角色与声音" / "旁白.mp3").write_bytes(b"mp3ref")
    (project_dir / "05_分段音频" / "1-001.wav").write_bytes(b"RIFFwav")

    quality = {
        "revisions": {"r1": {"revision_id": "r1", "relative_path": _V2_SEG, "segment_id": "1-001"}},
        "active_revisions": {"1-001": "r1"},
        "export_jobs": {"j1": {"task_id": "j1", "outputs": [{"relative_path": _V2_EXPORT}]}},
        "delivery_manifests": {"m1": {"manifest_id": "m1", "outputs": [{"relative_path": _V2_EXPORT}]}},
        "repair_history": {
            "rep1": {
                "repair_id": "rep1",
                "status": "done",
                "revision_ids": ["r1"],
                "prepared": [
                    {
                        "segment_id": "1-001",
                        "revision_id": "r1",
                        "target_relative_path": _V2_SEG,
                        "preserved_relative_path": "08_质检记录/repair_backup/1-001.bak",
                        "original_status": "done",
                    }
                ],
            }
        },
    }
    (project_dir / "08_质检记录" / "quality_state.json").write_text(
        json.dumps(quality, ensure_ascii=False), encoding="utf-8")
    # repair_history.preserved_relative_path 指向的真实文件（迁移后随目录整体搬迁）。
    (project_dir / "08_质检记录" / "repair_backup").mkdir(parents=True, exist_ok=True)
    (project_dir / "08_质检记录" / "repair_backup" / "1-001.bak").write_bytes(b"bakref")
    os.makedirs(project_dir / "09_导出文件" / "exports" / "t1", exist_ok=True)
    (project_dir / "09_导出文件" / "exports" / "t1" / "book.json").write_text("exported", encoding="utf-8")
    (project_dir / "user_note.txt").write_text("用户自己加的文件", encoding="utf-8")

    # SQLite 任务库：用 runtime task_type（synthesis）确保落到 production_tasks.sqlite3，
    # 且被 _rewrite_task_database 原地改写 options_json / artifact_dir。
    TaskRepository.save_task(TaskRecord(
        task_id="t1", task_type="synthesis", project=name, status="done",
        options={"revision_snapshot": [{"relative_path": _V2_SEG}]},
        artifact_dir="05_分段音频",
    ))
    return data_root, project_dir


def _snapshot_authoritative(project_dir) -> dict:
    """捕获迁移前/后的 authoritative 状态，便于回滚前后对照。"""
    project_dir = str(project_dir)
    snap: dict = {}
    snap["detect"] = project_paths.detect_storage_version(project_dir)
    for rel in (
        "project.json",
        "structured_script.json",
        "voice_bindings.json",
        "voice_cast.json",
        "08_质检记录/quality_state.json",
        "01_项目配置/production_tasks.sqlite3",
    ):
        path = os.path.join(project_dir, rel)
        if os.path.isfile(path):
            snap[f"sha:{rel}"] = _sha256(path)
    quality_path = os.path.join(project_dir, "08_质检记录", "quality_state.json")
    if os.path.isfile(quality_path):
        snap["quality"] = json.loads(Path(quality_path).read_text(encoding="utf-8"))
    db_path = os.path.join(project_dir, "01_项目配置", "production_tasks.sqlite3")
    if os.path.isfile(db_path):
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT task_id, options_json, artifact_dir FROM production_tasks"
            ).fetchone()
            snap["task"] = (row[0], row[1], row[2]) if row else None
            if row:
                snap["task_options"] = json.loads(row[1]) if row[1] else {}
        finally:
            conn.close()
    meta_path = os.path.join(project_dir, "project.json")
    if os.path.isfile(meta_path):
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        snap["source_file"] = meta.get("source_file")
        snap["voice_bindings_path"] = meta.get("voice_bindings_path")
    snap["v3_config_project_json_exists"] = os.path.isfile(
        os.path.join(project_dir, "99_系统数据", "配置", "project.json"))
    return snap


# ── R1：物理移动中途失败 ─────────────────────────────────────────────────────
def test_R1_physical_move_mid_failure_restores_v2(tmp_path, monkeypatch):
    _data_root, project_dir = _make_rich_v2_project(tmp_path, monkeypatch)
    before = _snapshot_authoritative(project_dir)

    # 前向迁移（仅 _move_tree_contents）在若干次调用后抛 OSError；回滚用 shutil.move 不受影响。
    real_move = ProjectStorageService._move_tree_contents
    counter = {"n": 0}

    def _boom(src, dst, **kw):
        counter["n"] += 1
        if counter["n"] > 3:
            raise OSError("injected R1 move failure")
        return real_move(src, dst, **kw)

    monkeypatch.setattr(ProjectStorageService, "_move_tree_contents", _boom)

    plan = ProjectStorageService.plan_storage_upgrade("v2rich")
    with pytest.raises(OSError):
        ProjectStorageService.upgrade_storage("v2rich", plan["token"])

    after = _snapshot_authoritative(project_dir)
    # storage_version 仍为 2
    assert project_paths.detect_storage_version(str(project_dir)) == 2
    # 未进入 step6 重写，所有内容 hash 与迁移前一致
    assert after["sha:project.json"] == before["sha:project.json"]
    assert after["sha:08_质检记录/quality_state.json"] == before["sha:08_质检记录/quality_state.json"]
    assert after["sha:01_项目配置/production_tasks.sqlite3"] == before["sha:01_项目配置/production_tasks.sqlite3"]
    assert after["source_file"] == _V2_SOURCE
    # 不存在被 scanner 误识别的 v3 权威标记
    assert not after["v3_config_project_json_exists"]
    # 用户文件不得丢失
    assert (project_dir / "user_note.txt").is_file()


# ── R2：durable reference rewrite 中途失败（JSON + SQLite 都必须恢复）──────────
def test_R2_reference_rewrite_failure_restores_v2(tmp_path, monkeypatch):
    _data_root, project_dir = _make_rich_v2_project(tmp_path, monkeypatch)
    before = _snapshot_authoritative(project_dir)

    # 让全部物理移动 + 全部就地重写（quality/bindings/cast/task_db）完成，然后抛异常。
    real_rewrite = ProjectStorageService._rewrite_persisted_paths

    def _boom(*args, **kwargs):
        real_rewrite(*args, **kwargs)
        raise RuntimeError("injected R2 rewrite failure")

    monkeypatch.setattr(ProjectStorageService, "_rewrite_persisted_paths", _boom)

    plan = ProjectStorageService.plan_storage_upgrade("v2rich")
    with pytest.raises(RuntimeError):
        ProjectStorageService.upgrade_storage("v2rich", plan["token"])

    after = _snapshot_authoritative(project_dir)
    assert project_paths.detect_storage_version(str(project_dir)) == 2
    # quality_state.json 完整恢复（含 revisions / export_jobs / delivery_manifests）
    assert after["quality"] == before["quality"], "quality_state.json 未完整恢复"
    # SQLite 通过 TaskRepository 读取：revision_snapshot 恢复为 v2 值
    rec = TaskRepository.load_task("t1")
    assert rec is not None
    snap_after = (rec.options or {}).get("revision_snapshot")
    assert snap_after[0]["relative_path"] == _V2_SEG
    assert json.loads(after["task"][1])["revision_snapshot"][0]["relative_path"] == _V2_SEG
    # voice_bindings / voice_cast 的 project_voice_path 恢复
    vb = json.loads((project_dir / "voice_bindings.json").read_text(encoding="utf-8"))
    assert vb["role_bindings"]["r1"]["project_voice_path"] == _V2_VOICE
    vc = json.loads((project_dir / "voice_cast.json").read_text(encoding="utf-8"))
    assert vc["roles"]["旁白"]["project_voice_path"] == _V2_VOICE
    # 无 v3 残留，用户文件保留
    assert not after["v3_config_project_json_exists"]
    assert (project_dir / "user_note.txt").is_file()


# ── R3：最终校验（late validation）失败 ───────────────────────────────────────
def test_R3_late_validation_failure_restores_v2_and_reads(tmp_path, monkeypatch):
    _data_root, project_dir = _make_rich_v2_project(tmp_path, monkeypatch)
    before = _snapshot_authoritative(project_dir)

    # 物理移动 + 全部引用重写均成功，仅在最终 integrity 校验阶段失败。
    def _boom(name):
        raise OSError("injected R3 validation failure")

    orig = ProjectStorageRepository.check_project_integrity
    ProjectStorageRepository.check_project_integrity = staticmethod(_boom)
    try:
        plan = ProjectStorageService.plan_storage_upgrade("v2rich")
        with pytest.raises(OSError):
            ProjectStorageService.upgrade_storage("v2rich", plan["token"])
    finally:
        ProjectStorageRepository.check_project_integrity = orig

    after = _snapshot_authoritative(project_dir)
    assert project_paths.detect_storage_version(str(project_dir)) == 2
    # 全部 authoritative 内容恢复为 v2
    assert after["quality"] == before["quality"]
    rec = TaskRepository.load_task("t1")
    assert (rec.options or {}).get("revision_snapshot")[0]["relative_path"] == _V2_SEG

    # 真实业务服务全部可正常读取原 v2 项目
    meta, _script, _bindings = ProjectRepository.load_project("v2rich")
    assert meta.storage_version == 2
    report = QualityService.get_quality_report("v2rich")
    assert report is not None
    exports = ExportService.list_exports("v2rich")
    assert len(exports) >= 1
    # 被引用的导出文件确实存在于 v2 位置
    assert os.path.isfile(os.path.join(project_dir, "09_导出文件", "exports", "t1", "book.json"))
    assert not after["v3_config_project_json_exists"]
    assert (project_dir / "user_note.txt").is_file()


# ── R4：storage_version / 最终 metadata 切换附近失败 ─────────────────────────
def test_R4_storage_version_finalization_failure_restores_v2(tmp_path, monkeypatch):
    _data_root, project_dir = _make_rich_v2_project(tmp_path, monkeypatch)

    # storage_version 已写成 3（step7），在最终校验阶段抛异常。
    def _boom(name):
        raise OSError("injected R4 finalization failure")

    orig = ProjectStorageRepository.check_project_integrity
    ProjectStorageRepository.check_project_integrity = staticmethod(_boom)
    try:
        plan = ProjectStorageService.plan_storage_upgrade("v2rich")
        with pytest.raises(OSError):
            ProjectStorageService.upgrade_storage("v2rich", plan["token"])
    finally:
        ProjectStorageRepository.check_project_integrity = orig

    after = _snapshot_authoritative(project_dir)
    # 即使 version 曾暂时写成 3，回滚后也必须恢复成 2
    assert project_paths.detect_storage_version(str(project_dir)) == 2
    assert after["source_file"] == _V2_SOURCE
    # 99_系统数据/配置/project.json 不得残留为 v3 权威标记
    assert not after["v3_config_project_json_exists"]
    meta = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert meta["storage_version"] == 2
    # 真实读取
    loaded, _s, _b = ProjectRepository.load_project("v2rich")
    assert loaded.storage_version == 2


# ── Rollback smoke：独立 v2 副本，plan → upgrade → late failure → rollback → 真读 ──
def test_rollback_copied_project_smoke(tmp_path, monkeypatch):
    _data_root, project_dir = _make_rich_v2_project(tmp_path, monkeypatch)
    before = _snapshot_authoritative(project_dir)

    def _boom(name):
        raise OSError("injected smoke late failure")

    orig = ProjectStorageRepository.check_project_integrity
    ProjectStorageRepository.check_project_integrity = staticmethod(_boom)
    try:
        plan = ProjectStorageService.plan_storage_upgrade("v2rich")
        with pytest.raises(OSError):
            ProjectStorageService.upgrade_storage("v2rich", plan["token"])
    finally:
        ProjectStorageRepository.check_project_integrity = orig

    after = _snapshot_authoritative(project_dir)
    assert project_paths.detect_storage_version(str(project_dir)) == 2
    assert after["quality"] == before["quality"]
    rec = TaskRepository.load_task("t1")
    assert (rec.options or {}).get("revision_snapshot")[0]["relative_path"] == _V2_SEG
    # 真实服务链路
    ProjectRepository.load_project("v2rich")
    QualityService.get_quality_report("v2rich")
    TaskRepository.load_task("t1")
    ExportService.list_exports("v2rich")
    assert (project_dir / "user_note.txt").is_file()


# ── Success-path completeness：repair_history 也必须被改写 ───────────────────
def test_successful_migration_rewrites_repair_history(tmp_path, monkeypatch):
    """迁移成功后，repair_history.prepared 内的 target/preserved 相对路径必须
    改写为 v3 路径，且真实文件随之搬迁、服务可读。

    这是 success-path completeness 测试（不是 rollback 测试）。
    """
    _data_root, project_dir = _make_rich_v2_project(tmp_path, monkeypatch)

    plan = ProjectStorageService.plan_storage_upgrade("v2rich")
    ProjectStorageService.upgrade_storage("v2rich", plan["token"])

    # 迁移成功：storage_version == 3
    assert project_paths.detect_storage_version(str(project_dir)) == 3

    after_q = json.loads(
        (project_dir / "99_系统数据" / "质检" / "quality_state.json").read_text(encoding="utf-8")
    )
    after_rep = after_q["repair_history"]["rep1"]["prepared"][0]
    # 1) 改写为 v3 相对路径
    assert after_rep["target_relative_path"] == "02_生成音频/分段音频/1-001.wav"
    assert after_rep["preserved_relative_path"] == "99_系统数据/质检/repair_backup/1-001.bak"
    # 2) 实际 resolve 到存在文件（随目录整体搬迁）
    assert (project_dir / "02_生成音频" / "分段音频" / "1-001.wav").is_file()
    assert (project_dir / "99_系统数据" / "质检" / "repair_backup" / "1-001.bak").is_file()
    # 3) QualityRepository 仍可读取 history
    repairs = QualityRepository.list_history("v2rich", "repair_history")
    assert any(r.get("repair_id") == "rep1" for r in repairs)
