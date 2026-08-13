"""TTS engine switcher UI contracts (GPU-free and framework-light)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ui import settings_handlers

ROOT = Path(__file__).resolve().parents[1]


def test_settings_page_exposes_two_engine_radio_and_independent_model_status():
    source = (ROOT / "ui/pages/settings_page.py").read_text(encoding="utf-8")

    assert "s_tts_engine = gr.Radio(" in source
    assert "IndexTTS 2 Legacy（回滚）模型目录" in source
    assert "IndexTTS 2.5（推荐）模型目录" in source
    assert '"s_legacy_model_status"' in source
    assert '"s_indextts25_model_status"' in source
    assert '"s_tts_runtime_engine"' in source
    assert '"s_tts_frozen_engine"' in source


def test_settings_wiring_calls_clear_apply_handler_and_refreshes_statuses():
    source = (ROOT / "ui/wiring/settings_wiring.py").read_text(encoding="utf-8")

    assert "settings_handlers.apply_tts_engine" in source
    assert "settings_handlers.refresh_tts_engine_ui" in source
    for component in (
        'page["s_tts_status"]',
        'page["s_legacy_model_status"]',
        'page["s_indextts25_model_status"]',
        'page["s_tts_runtime_engine"]',
        'page["s_tts_frozen_engine"]',
    ):
        assert component in source


def test_legacy_single_model_config_defaults_to_rollback_engine(monkeypatch):
    monkeypatch.setattr(
        settings_handlers,
        "_read_raw_config",
        lambda: {"model_dir": "/legacy-only"},
    )
    monkeypatch.delenv("AUDIOBOOK_STUDIO_ENGINE", raising=False)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_ENGINE_VERSION", raising=False)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_VERSION", raising=False)

    assert settings_handlers.get_tts_engine_settings()["engine"] == settings_handlers.TTS_ENGINE_LEGACY


def test_model_directory_readiness_is_independent(tmp_path):
    legacy = tmp_path / "legacy"
    recommended = tmp_path / "recommended"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("version: 2\n", encoding="utf-8")

    assert settings_handlers._model_ready(str(legacy), "v2") is True
    assert settings_handlers._model_ready(str(recommended), "v2.5") is False
    assert "✅ 已就绪" in settings_handlers._ready_message(str(legacy), "v2")
    assert "未就绪" in settings_handlers._ready_message(str(recommended), "v2.5")


def test_active_tts_task_rejects_switch_with_accurate_task_label(monkeypatch):
    for task_type, label in (
        ("synthesis", "合成"),
        ("voice_preview", "试听"),
        ("supplement", "补录"),
        ("export", "导出"),
    ):
        calls: list[str] = []
        record = SimpleNamespace(
            task_id=f"task-{task_type}",
            task_type=task_type,
            status="running",
            options={},
            startup={},
        )
        monkeypatch.setattr(settings_handlers, "_active_tts_tasks", lambda record=record: [record])
        def persist(*_args, calls=calls):
            calls.append("persist")

        monkeypatch.setattr(settings_handlers, "_persist_tts_engine_settings", persist)

        result = settings_handlers.apply_tts_engine(
            settings_handlers.TTS_ENGINE_25,
            "/legacy",
            "/recommended",
        )

        assert label in result[0]
        assert "正在运行，请等待" in result[0]
        assert "无法切换 TTS 引擎" in result[0]
        assert "再切换 TTS 引擎" in result[0]
        assert calls == []


def test_idle_apply_persists_then_requests_controlled_recycle(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(settings_handlers, "_active_tts_tasks", list)
    monkeypatch.setattr(
        settings_handlers,
        "_persist_tts_engine_settings",
        lambda engine, legacy, recommended: calls.append(
            ("persist", f"{engine}|{legacy}|{recommended}")
        ),
    )
    monkeypatch.setattr(
        settings_handlers,
        "_request_runtime_recycle",
        lambda engine: calls.append(("recycle", engine)) or "已提交受控 runtime recycle 请求",
    )
    monkeypatch.setattr(
        settings_handlers,
        "_tts_output_values",
        lambda message, *_args, **_kwargs: (message, "legacy", "recommended", "runtime", "frozen"),
    )

    result = settings_handlers.apply_tts_engine(
        settings_handlers.TTS_ENGINE_25,
        "~/models/legacy",
        "~/models/25",
    )

    assert calls == [
        (
            "persist",
            f"indextts25|{Path.home() / 'models' / 'legacy'}|{Path.home() / 'models' / '25'}",
        ),
        ("recycle", "indextts25"),
    ]
    assert "IndexTTS 2.5（推荐）" in result[0]
    assert "受控 runtime recycle" in result[0]


def test_runtime_and_task_frozen_engine_are_both_visible(monkeypatch):
    record = SimpleNamespace(
        task_id="task-frozen-25",
        task_type="synthesis",
        status="running",
        options={"engine_snapshot": {"engine_version": "2.5"}},
        startup={},
    )
    monkeypatch.setattr(settings_handlers, "_active_tts_tasks", lambda: [record])
    monkeypatch.setattr(
        settings_handlers,
        "_runtime_snapshots",
        lambda: [{
            "engine_version": "2.5",
            "engine_state": "ready",
            "runtime_state": "running",
        }],
    )

    runtime = settings_handlers._runtime_engine_message()
    frozen = settings_handlers._frozen_engine_message()

    assert "实际 runtime engine：**IndexTTS 2.5（推荐）**" in runtime
    assert "引擎状态：已就绪" in runtime
    assert "任务冻结 engine：**IndexTTS 2.5（推荐）**" in frozen
    assert "task-frozen-25" in frozen


def test_runtime_engine_status_can_be_read_from_mainline_profile_fields(monkeypatch):
    monkeypatch.setattr(
        settings_handlers,
        "_runtime_snapshots",
        lambda: [{
            "engine_identity": "indextts:2",
            "engine_state": "loading",
            "runtime_state": "starting",
        }],
    )

    message = settings_handlers._runtime_engine_message()

    assert "实际 runtime engine：**IndexTTS 2 Legacy / 回滚**" in message
    assert "引擎状态：加载中" in message
    assert "runtime：启动中" in message


def test_compat_persistence_keeps_unrelated_config_and_selected_profile(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"ai_provider": {"default_provider": "local"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_handlers.ConfigRepository, "CONFIG_PATH", str(config_path))

    settings_handlers._persist_tts_engine_settings(
        settings_handlers.TTS_ENGINE_LEGACY,
        str(tmp_path / "legacy"),
        str(tmp_path / "recommended"),
    )
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert saved["ai_provider"]["default_provider"] == "local"
    assert saved["tts_engine"] == "legacy"
    assert saved["engine_version"] == "2"
    assert saved["model_dir"] == str(tmp_path / "legacy")
    assert saved["tts_model_dirs"]["indextts25"] == str(tmp_path / "recommended")


def test_version_specific_model_dirs_survive_selected_v25_reload(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(settings_handlers.ConfigRepository, "CONFIG_PATH", str(config_path))
    settings_handlers._persist_tts_engine_settings(
        settings_handlers.TTS_ENGINE_25,
        str(tmp_path / "legacy"),
        str(tmp_path / "recommended"),
    )
    state = settings_handlers.get_tts_engine_settings()
    assert state["engine"] == settings_handlers.TTS_ENGINE_25
    assert state["legacy_model_dir"] == str(tmp_path / "legacy")
    assert state["indextts25_model_dir"] == str(tmp_path / "recommended")
