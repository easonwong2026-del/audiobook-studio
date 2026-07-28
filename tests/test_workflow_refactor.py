"""v3.3.1 工作流重构集成测试。

覆盖完整的创建→项目扫描→打开项目→快照加载流程。
使用 LocalDirectorProvider（无需网络/GPU/Keyring），所有目录隔离在 tmp_path 内。
"""
from __future__ import annotations

import json

import pytest


SAMPLE_TXT = """第一章 初识

李明站在窗前，望着远方的山峦。

"你来了。"身后传来一个低沉的声音。

李明回头，看见一位老者缓缓走进房间。"我等了很久了。"

老者点了点头，走到桌前坐下。"现在开始吧。"
"""

LOCAL_CONFIG = {
    "provider": "local",
    "model": "",
    "api_key": "",
    "base_url": "",
    "timeout": 180,
}


def _patch_provider(monkeypatch):
    """强制使用 Local Provider，不影响真实 config.json。"""
    monkeypatch.setattr("services.ai_settings._get_secret", lambda *a: None)
    monkeypatch.setattr(
        "services.ai_settings.AiSettingsService.get_effective_provider_config",
        lambda *a, **kw: dict(LOCAL_CONFIG),
    )


def _setup_test_data_dir(monkeypatch, data_dir):
    """设置隔离的数据目录，不 reload 全局模块以免污染其他测试。"""
    proj_root = str(data_dir / "projects")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(data_dir))
    # 直接设定 ProjectRepository 的工作区根目录
    from repositories.project_repo import ProjectRepository
    ProjectRepository.WORKSPACE_ROOT = proj_root
    # 确保 config 相关函数返回隔离路径
    monkeypatch.setattr("lib.config.get_data_dir", lambda: str(data_dir))
    monkeypatch.setattr("lib.config.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("lib.config.get_preview_dir", lambda: str(data_dir / "preview"))


def test_create_txt_project_and_open(tmp_path, monkeypatch):
    """从 TXT 创建项目 → 项目出现在扫描列表中 → 打开后快照正常。"""
    from services.project_creation import ProjectCreationService
    from repositories.project_repo import ProjectRepository
    from lib import script_loader

    data_dir = tmp_path / "Workspace"
    data_dir.mkdir()
    _setup_test_data_dir(monkeypatch, data_dir)
    _patch_provider(monkeypatch)

    txt_path = tmp_path / "test_novel.txt"
    txt_path.write_text(SAMPLE_TXT, encoding="utf-8")

    result = ProjectCreationService.create_from_source(
        project_name="demo_project",
        source_path=str(txt_path),
        title="测试作品",
        author="测试者",
        provider_name="local",
    )

    assert result.project_name == "demo_project"
    assert result.chapter_count >= 1
    assert result.segment_count >= 1
    assert result.role_count >= 1
    assert result.title == "测试作品"

    projects = ProjectRepository.scan_projects()
    assert "demo_project" in projects

    meta, script, bindings = ProjectRepository.load_project("demo_project")
    assert meta.project_name == "demo_project"
    assert meta.total_chapters >= 1
    assert meta.total_segments >= 1
    chapters = script.get("chapters", [])
    assert len(chapters) >= 1
    assert "voices" in script
    assert len(script["voices"]) >= 1
    assert isinstance(bindings, dict)
    assert "bindings" in bindings
    assert isinstance(bindings["bindings"], dict)

    snap = ProjectRepository.load_snapshot("demo_project")
    assert snap.meta.project_name == "demo_project"
    assert snap.script is not None
    assert "chapters" in snap.script
    for ch in snap.script.get("chapters", []):
        for seg in ch.get("segments", []):
            seg_id = seg["id"]
            assert seg_id in snap.meta.segments_status, (
                f"segment {seg_id} 未出现在 segments_status 中"
            )

    script_obj = script_loader.from_dict(snap.script)
    errors = script_loader.validate_script(script_obj)
    assert not errors, "剧本校验失败：" + "; ".join(errors)


def test_create_fails_with_duplicate_name(tmp_path, monkeypatch):
    """项目名重复时明确报错。"""
    from services.project_creation import ProjectCreationService

    data_dir = tmp_path / "dup_dir"
    data_dir.mkdir()
    _setup_test_data_dir(monkeypatch, data_dir)
    _patch_provider(monkeypatch)

    txt_path = tmp_path / "dup.txt"
    txt_path.write_text(SAMPLE_TXT, encoding="utf-8")

    ProjectCreationService.create_from_source(
        "dup", str(txt_path), provider_name="local")

    with pytest.raises((ValueError, FileExistsError), match="已存在|exist"):
        ProjectCreationService.create_from_source(
            "dup", str(txt_path), provider_name="local")


def test_ai_provider_param_passthrough():
    """确认 create_provider 现在支持透传 api_key / base_url / timeout。"""
    from ai.providers import create_provider
    from ai.providers.base import ScriptAnalysisProvider

    p = create_provider("local", api_key="xxx", base_url="http://h")
    assert isinstance(p, ScriptAnalysisProvider)

    p = create_provider("openai", api_key="sk-test", base_url="https://custom",
                        timeout=60)
    assert isinstance(p, ScriptAnalysisProvider)
    assert p.api_key == "sk-test"
    assert p.base_url == "https://custom"
    assert p.timeout == 60.0

    p = create_provider("deepseek", api_key="ds-test", base_url="https://ds.local",
                        timeout=120)
    assert isinstance(p, ScriptAnalysisProvider)
    assert p.api_key == "ds-test"
    assert p.base_url == "https://ds.local"
    assert p.timeout == 120.0


def test_settings_save_and_read(monkeypatch, tmp_path):
    """AiSettingsService 保存/读取非敏感配置往返一致。（使用隔离配置文件）"""
    import services.ai_settings

    # 使用临时配置文件，避免修改真实 config.json
    temp_config = tmp_path / "config.json"
    monkeypatch.setattr(services.ai_settings, "_CONFIG_PATH", str(temp_config))

    cfg = {"default_provider": "local", "local_model": "", "timeout": 120}
    services.ai_settings.AiSettingsService.save_provider_config(cfg)

    read_back = services.ai_settings.AiSettingsService.get_provider_config()
    assert read_back["default_provider"] == "local"
    assert read_back["timeout"] == 120


def test_create_from_structured_script(tmp_path, monkeypatch):
    """从合法 JSON 创建项目的快速通道。"""
    from services.project_creation import ProjectCreationService
    from repositories.project_repo import ProjectRepository

    data_dir = tmp_path / "json_dir"
    data_dir.mkdir()
    _setup_test_data_dir(monkeypatch, data_dir)
    monkeypatch.setattr("services.ai_settings._get_secret", lambda *a: None)

    script = {
        "version": "3.0",
        "meta": {"title": "JSON测试", "author": "测试", "total_segments": 2},
        "voices": {
            "旁白": {"description": "叙事声"},
            "小明": {"description": "少年"},
        },
        "chapters": [{
            "id": 1,
            "title": "第一章",
            "segments": [
                {"id": "1-001", "speaker": "旁白", "role": "旁白",
                 "text": "故事开始。", "emotion": "neutral",
                 "emotion_strength": 0.4, "emo_alpha": 0.4, "speech_rate": 1.0,
                 "delivery": {"speed": 1.0, "pitch": 0, "intensity": 0.4, "breath": "light"},
                 "pause_before": 0, "pause_after": 600, "pauses": []},
                {"id": "1-002", "speaker": "小明", "role": "小明",
                 "text": "你好！", "emotion": "happy",
                 "emotion_strength": 0.6, "emo_alpha": 0.6, "speech_rate": 1.05,
                 "delivery": {"speed": 1.05, "pitch": 0, "intensity": 0.6, "breath": "light"},
                 "pause_before": 200, "pause_after": 800, "pauses": []},
            ],
        }],
    }
    json_path = tmp_path / "test_script.json"
    json_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    result = ProjectCreationService.create_from_structured_script(
        "json_proj", str(json_path))

    assert result.project_name == "json_proj"
    assert result.chapter_count == 1
    assert result.segment_count == 2
    assert result.role_count == 2

    meta, _, _ = ProjectRepository.load_project("json_proj")
    assert meta.project_name == "json_proj"
    assert meta.total_segments == 2
