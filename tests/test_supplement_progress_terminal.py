"""P1-B 回归：补录完成后 terminal progress 必须被清空/替换为明确终态。

Windows 实机现象：上方已显示「补合成完成（2/2 成功）」，下方仍残留
「正在加载 IndexTTS 2.5（2.5）...」。根因是 ``progress(None, desc=...)``
设置的非终态进度在任务返回后没有清理。

修复分两层：
1. service（``RuntimeTTSService._submit``）：任务到达 done/error 后向
   ``progress_cb`` 发送终态 phase（done/error），任何调用方都能拿到终态；
2. UI（``app.do_supplement_synth``）：成功/异常路径显式调用
   ``progress(1.0, desc=终态文案)``，覆盖残留的「正在加载模型…」。

本测试同时验证“冷启动仍会显示加载进度”没有被删除（Case D 语义在
test_runtime_start_fail_fast.py 中覆盖 runtime 层面）。
"""
from __future__ import annotations
from lib import project_paths

import os
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from repositories.project_repo import ProjectRepository
from repositories.task_repo import TaskRecord
from services.production_runtime import ProductionRuntimeClient
from services.runtime_tts import RuntimeTTSError, RuntimeTTSService

SCRIPT = {
    "meta": {"title": "Progress"},
    "voices": {"旁白": {}},
    "chapters": [{
        "id": "001",
        "title": "第一章",
        "segments": [{"id": "001-001", "role": "旁白", "text": "测试"}],
    }],
}


@pytest.fixture
def runtime_project(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)
    return data_dir


def _done_record(task_id: str, *, task_type: str = "supplement") -> TaskRecord:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return TaskRecord(
        task_id=task_id,
        task_type=task_type,
        project="book",
        status="done",
        artifact_dir="",
        source="web",
        scope={},
        options={},
        progress={"total": 2, "completed": 2, "percent": 100.0},
        idempotency_key="k",
        created_at=now,
        updated_at=now,
    )


def _failed_record(task_id: str) -> TaskRecord:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = _done_record(task_id)
    record.status = "error"
    record.error_summary = "合成失败"
    record.created_at = now
    record.updated_at = now
    return record


# ── service 层：_submit 在 done/error 后发送终态 phase ────────────────────

def test_submit_emits_terminal_done_progress(runtime_project, monkeypatch):
    phases: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ProductionRuntimeClient, "ensure_running", staticmethod(lambda: None)
    )

    def _fake_wait(cls, task_id, timeout, progress_cb=None):
        return _done_record(task_id)

    monkeypatch.setattr(RuntimeTTSService, "_wait", classmethod(_fake_wait))
    artifact_dir = os.path.join(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "cache", create=True), "sup")
    result = RuntimeTTSService._submit(
        project_name="book",
        task_type="supplement",
        artifact_dir=artifact_dir,
        options={"lines": ["甲", "乙"]},
        total=2,
        timeout=30,
        progress_cb=lambda phase, message: phases.append((phase, message)),
    )
    assert result.status == "done"
    assert phases[-1][0] == "done"
    assert "完成" in phases[-1][1]


def test_submit_emits_terminal_error_progress(runtime_project, monkeypatch):
    phases: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ProductionRuntimeClient, "ensure_running", staticmethod(lambda: None)
    )

    def _fake_wait(cls, task_id, timeout, progress_cb=None):
        raise RuntimeTTSError(_failed_record(task_id))

    monkeypatch.setattr(RuntimeTTSService, "_wait", classmethod(_fake_wait))
    artifact_dir = os.path.join(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "cache", create=True), "sup")
    with pytest.raises(RuntimeTTSError):
        RuntimeTTSService._submit(
            project_name="book",
            task_type="supplement",
            artifact_dir=artifact_dir,
            options={"lines": ["甲"]},
            total=1,
            timeout=30,
            progress_cb=lambda phase, message: phases.append((phase, message)),
        )
    assert phases[-1][0] == "error"
    assert "失败" in phases[-1][1]


def test_submit_keeps_engine_loading_progress_before_terminal(runtime_project, monkeypatch):
    """冷启动加载进度能力不能被删除：终态前 engine_loading 仍会上报。"""
    phases: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ProductionRuntimeClient, "ensure_running", staticmethod(lambda: None)
    )

    def _fake_wait(cls, task_id, timeout, progress_cb=None):
        if progress_cb is not None:
            progress_cb("engine_loading", "正在加载 IndexTTS 2.5…")
        return _done_record(task_id)

    monkeypatch.setattr(RuntimeTTSService, "_wait", classmethod(_fake_wait))
    artifact_dir = os.path.join(project_paths.project_dir(ProjectRepository.get_project_dir("book"), "cache", create=True), "sup")
    RuntimeTTSService._submit(
        project_name="book",
        task_type="supplement",
        artifact_dir=artifact_dir,
        options={"lines": ["甲"]},
        total=1,
        timeout=30,
        progress_cb=lambda phase, message: phases.append((phase, message)),
    )
    assert any(phase == "engine_loading" for phase, _ in phases)
    assert phases[-1][0] == "done"


# ── UI 层：do_supplement_synth 结束时清除残留 loading 进度 ────────────────

def _fake_synthesize_lines(*, role, lines, speaker_audio, overrides=None,
                           num_beams=2, task=None, progress_cb=None,
                           fail_all=False):
    """返回与 SupplementService.synthesize_lines 相同结构的 dict 列表。

    ``fail_all=True`` 时全部句子失败（wav_path=None + error 文案），用于
    0/N 成功的终态文案断言。默认模拟冷启动竞态：只上报 engine_loading
    （不报 engine_ready，即 `_wait` 在 init_done 行可见前就轮到 done 的
    竞态窗口），验证终态 markdown 不再残留「正在加载…」进行时。
    """
    if progress_cb is not None:
        progress_cb("submitted", "已提交补录任务，正在等待运行时…")
        progress_cb("engine_loading", "正在加载 IndexTTS 2.5（2.5）…")
    results = []
    for index, text in enumerate(lines):
        if fail_all:
            results.append({
                "index": index, "text": text, "wav_path": None,
                "status": "failed", "error": f"❌ 句{index + 1}: 合成失败",
            })
            continue
        wav_path = os.path.join(task.task_dir, f"{index + 1:03d}.wav")
        with open(wav_path, "wb") as fh:
            fh.write(b"RIFF\x00" * 32)
        results.append({
            "index": index, "text": text, "wav_path": wav_path,
            "status": "ok", "error": "",
        })
    if task is not None:
        from services.supplement import SupplementItemResult
        task.items = [
            SupplementItemResult(
                index=item["index"], text=item["text"],
                wav_path=item["wav_path"], status=item["status"],
                error=item["error"],
            )
            for item in results
        ]
    return results


class FakeProgress:
    def __init__(self) -> None:
        self.calls: list[tuple[float | None, str | None]] = []

    def __call__(self, frac: float | None = None, desc: str | None = None) -> None:
        self.calls.append((frac, desc))


def test_do_supplement_synth_clears_loading_progress(tmp_path, monkeypatch):
    import app

    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)

    monkeypatch.setattr(app.SupplementService, "synthesize_lines", _fake_synthesize_lines)
    progress = FakeProgress()
    session = SimpleNamespace(
        project="book",
        script=SCRIPT,
        bindings={"旁白": os.path.join(str(tmp_path), "speaker.wav")},
    )
    wavs, md = app.do_supplement_synth(
        "旁白", "paste", "你好。世界。", "", [],
        "(按默认)", 1.0, 1.0, 2, True, None, session,
        progress=progress,
    )
    assert len(wavs) == 2
    assert "补合成完成（2/2 成功）" in md
    # MEDIUM（QA）：成功终态 markdown 不得残留「正在加载…」进行时——
    # engine_note 必须是过去时/中性表述（或省略）。
    assert "正在加载" not in md
    assert "本次已加载 IndexTTS 2.5（2.5）" in md
    # 终态进度必须到达 1.0 且文案不含「正在加载」
    assert progress.calls[-1][0] == 1.0
    assert progress.calls[-1][1] == "✅ 补录完成（2/2 成功）"
    assert "正在加载" not in (progress.calls[-1][1] or "")
    # 过程 progress（engine_loading）能力仍在：非终态阶段已上报加载文案
    assert any(desc and "正在加载" in desc for _, desc in progress.calls[:-1])


def test_do_supplement_synth_zero_success_uses_neutral_wording(tmp_path, monkeypatch):
    """LOW（QA）：0/N 成功时终态 md 与 progress desc 不得用 ✅ 误导文案。"""
    import app

    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)

    def _fail_all(*args, **kwargs):
        return _fake_synthesize_lines(*args, fail_all=True, **kwargs)

    monkeypatch.setattr(app.SupplementService, "synthesize_lines", _fail_all)
    progress = FakeProgress()
    session = SimpleNamespace(
        project="book",
        script=SCRIPT,
        bindings={"旁白": os.path.join(str(tmp_path), "speaker.wav")},
    )
    wavs, md = app.do_supplement_synth(
        "旁白", "paste", "你好。世界。", "", [],
        "(按默认)", 1.0, 1.0, 2, True, None, session,
        progress=progress,
    )
    assert wavs == []
    # md 标题为中性/失败文案，不带 ✅；逐句仍保留失败原因
    assert "补合成完成（0/2 成功，全部失败）" in md
    assert "### 🎙 补合成完成（0/2 成功）" not in md
    assert "❌ 句1: 合成失败" in md
    # progress desc 用 ❌，不用 ✅
    assert progress.calls[-1][0] == 1.0
    assert progress.calls[-1][1] == "❌ 补录完成（0/2 成功）"
    # 终态 markdown 仍不得残留「正在加载」进行时
    assert "正在加载" not in md


def test_do_supplement_synth_error_clears_loading_progress(tmp_path, monkeypatch):
    import app

    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", data_dir)
    ProjectRepository.WORKSPACE_ROOT = os.path.join(data_dir, "projects")
    ProjectRepository.LEGACY_ROOT = os.path.join(data_dir, "legacy")
    ProjectRepository._INITIALIZED = True
    ProjectRepository.create_project_from_data("book", SCRIPT)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("引擎加载失败")

    monkeypatch.setattr(app.SupplementService, "synthesize_lines", _boom)
    progress = FakeProgress()
    session = SimpleNamespace(
        project="book",
        script=SCRIPT,
        bindings={"旁白": os.path.join(str(tmp_path), "speaker.wav")},
    )
    wavs, md = app.do_supplement_synth(
        "旁白", "paste", "你好。世界。", "", [],
        "(按默认)", 1.0, 1.0, 2, True, None, session,
        progress=progress,
    )
    assert wavs == []
    assert "补合成异常" in md
    assert progress.calls[-1][0] == 1.0
    assert "补合成失败" in (progress.calls[-1][1] or "")
    assert "正在加载" not in (progress.calls[-1][1] or "")
