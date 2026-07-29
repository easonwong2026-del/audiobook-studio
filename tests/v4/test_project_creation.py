from pathlib import Path

from repositories.project_v4_repository import ProjectV4Repository
from services.v4_project_creation import V4ProjectCreationService


def test_project_creation_is_local_and_allows_unresolved(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("开头。“未知角色。”结尾。", encoding="utf-8")
    projects = tmp_path / "projects"
    result = V4ProjectCreationService(
        ProjectV4Repository(projects)
    ).create_from_source(source, "新项目", title="作品名", author="作者")
    assert result.unresolved_segments == 1
    assert result.project_path.parent == projects
    assert (result.project_path / "project.json").is_file()
    assert (result.project_path / "production/voices.json").is_file()
    assert (result.project_path / "production/tts_profile.json").is_file()
    manifest = ProjectV4Repository(projects).load_manifest(result.project_path)
    assert (manifest.title, manifest.author) == ("作品名", "作者")
    assert not any(path.name.startswith(".tmp_v4_") for path in projects.iterdir())
    assert isinstance(result.project_path, Path)
