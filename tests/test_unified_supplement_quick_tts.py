"""统一补录 / Quick TTS UI dispatch 与跨模式状态回归。"""
from __future__ import annotations

from types import SimpleNamespace


def test_project_mode_uses_shared_dispatch_and_keeps_override(monkeypatch, tmp_path):
    import app

    wav = tmp_path / "project.wav"
    wav.write_bytes(b"RIFF")
    captured = {}

    def _fake_project(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return [str(wav)], "project done"

    monkeypatch.setattr(app, "_synthesize_project_utility", _fake_project)
    result = app.do_utility_tts_synth(
        "project_role",
        "旁白",
        None,
        "项目补录文本",
        "happy",
        0.7,
        1.2,
        3,
        True,
        "替换声音",
        SimpleNamespace(project="book"),
    )

    assert result == ([str(wav)], "project done", "project_role", "book")
    assert captured["args"][:3] == ("旁白", "paste", "项目补录文本")
    assert captured["args"][9] is True
    assert captured["args"][10] == "替换声音"
    assert captured["kwargs"]["progress"] is None


def test_library_mode_does_not_require_project_and_forwards_shared_controls(
    monkeypatch, tmp_path,
):
    import app

    voice = tmp_path / "library.wav"
    voice.write_bytes(b"RIFF")
    wav = tmp_path / "quick.wav"
    wav.write_bytes(b"RIFF")
    captured = {}

    monkeypatch.setattr(app, "_lib_path", lambda _: str(voice))

    def _fake_quick(**kwargs):
        captured.update(kwargs)
        return str(wav)

    monkeypatch.setattr(app.QuickTTSService, "synthesize", staticmethod(_fake_quick))
    result = app.do_utility_tts_synth(
        "library_voice",
        None,
        "全局声音",
        "Quick TTS 文本",
        "sad",
        0.6,
        0.9,
        3,
        True,
        "ignored",
        SimpleNamespace(project="book"),
    )

    assert result == ([str(wav)], f"### 🎙 临时配音完成\n- ✅ 已生成：`{wav}`\n> 临时配音不走项目书架；试听或导出后产物位于 Quick TTS 目录。", "library_voice", "")
    assert captured["speaker_audio"] == str(voice)
    assert captured["num_beams"] == 3
    assert captured["overrides"] == {
        "emotion": "sad",
        "emo_alpha": 0.6,
        "speech_rate": 0.9,
    }


def test_mode_switch_clears_shared_artifact_state(monkeypatch):
    import app

    monkeypatch.setattr(app.QuickTTSService, "exports_root", staticmethod(lambda: "/tmp/quick-exports"))
    outputs = app.reset_utility_mode("library_voice", None)

    assert outputs[0]["visible"] is False
    assert outputs[1]["visible"] is True
    assert outputs[2] == []
    assert outputs[3] == ""
    assert outputs[4] == ""
    assert outputs[5] is None
    assert outputs[6] is None
    assert outputs[7] == ""
    assert "临时配音" in outputs[8]
    assert outputs[9] == "**保存位置：** `/tmp/quick-exports`"


def test_shared_preview_and_export_reject_stale_cross_mode_result():
    import app

    assert app.play_utility_preview("library_voice", "project_role", "", ["old.wav"], None) is None
    output, message = app.do_utility_export(
        "project_role",
        "library_voice",
        "",
        "mp3",
        "192k",
        "stale",
        ["old.wav"],
        "旁白",
        None,
    )
    assert output is None
    assert "另一种声音来源" in message
    assert app.open_utility_folder(
        "project_role", "library_voice", "", ["old.wav"], None
    ) == "❌ 当前音频来自另一种声音来源，请先重新生成"


def test_json_import_feeds_shared_synth_state(monkeypatch):
    import app

    monkeypatch.setattr(
        app,
        "do_supplement_parse_json",
        lambda *_: (app.gr.update(value="旁白"), "旁白", ["第一句", "第二句"], "### ✅ JSON 解析成功"),
    )
    role, text, split, status = app.do_utility_parse_json("input.json", None)

    assert role["value"] == "旁白"
    assert text == "第一句\n第二句"
    assert split["value"] is False
    assert status == "### ✅ JSON 解析成功"


def test_shared_export_and_open_dispatch_use_selected_mode(monkeypatch):
    import app

    monkeypatch.setattr(app, "do_quick_tts_export", lambda *args: ("quick.wav", "quick"))
    monkeypatch.setattr(app, "open_quick_tts_folder", lambda: "quick-folder")
    result = app.do_utility_export(
        "library_voice", "library_voice", "", "mp3", "192k", "quick",
        ["quick.wav"], None, SimpleNamespace(project="book"),
    )
    assert result == ("quick.wav", "quick")
    assert app.open_utility_folder(
        "library_voice", "library_voice", "", ["quick.wav"],
        SimpleNamespace(project="book"),
    ) == "quick-folder"

    monkeypatch.setattr(app, "do_supplement_export", lambda *args: ("project.wav", "project"))
    monkeypatch.setattr(app, "open_supplement_folder", lambda *args: "project-folder")
    result = app.do_utility_export(
        "project_role", "project_role", "book", "mp3", "192k", "project",
        ["project.wav"], "旁白", SimpleNamespace(project="book"),
    )
    assert result == ("project.wav", "project")
    assert app.open_utility_folder(
        "project_role", "project_role", "book", ["project.wav"],
        SimpleNamespace(project="book"),
    ) == "project-folder"


def test_cross_project_stale_project_result_is_rejected(monkeypatch, tmp_path):
    import app

    wav = tmp_path / "project-a.wav"
    wav.write_bytes(b"RIFF")
    current = SimpleNamespace(project="book-b")

    assert app.play_utility_preview(
        "project_role", "project_role", "book-a", [str(wav)], current
    ) is None

    output, message = app.do_utility_export(
        "project_role", "project_role", "book-a", "mp3", "192k", "stale", [str(wav)],
        "旁白", current,
    )
    assert output is None
    assert "当前项目已变化" in message

    monkeypatch.setattr(app, "open_supplement_folder", lambda *_: "opened")
    message = app.open_utility_folder(
        "project_role", "project_role", "book-a", [str(wav)], current
    )
    assert "当前项目已变化" in message
