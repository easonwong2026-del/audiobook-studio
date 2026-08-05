"""The V3 import/workbench path must not require AI or network services."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from repositories.project_repo import ProjectRepository
from services.project import ProjectService
from services.structured_script_import import StructuredScriptImportService

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "tests" / "fixtures" / "structured_script_valid.json"


@pytest.fixture
def isolated_projects(tmp_path):
    original = (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    )
    ProjectRepository.WORKSPACE_ROOT = str(tmp_path / "projects")
    ProjectRepository.LEGACY_ROOT = str(tmp_path / "legacy")
    ProjectRepository._INITIALIZED = True
    yield tmp_path
    (
        ProjectRepository.WORKSPACE_ROOT,
        ProjectRepository.LEGACY_ROOT,
        ProjectRepository._INITIALIZED,
    ) = original


def _deny_network(*_args, **_kwargs):
    raise AssertionError("V3 JSON project flow attempted a network connection")


def test_json_project_flow_stays_offline(monkeypatch, isolated_projects):
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", _deny_network)
    monkeypatch.setattr(urllib.request, "urlopen", _deny_network)

    result = StructuredScriptImportService.create("offline-project", str(VALID))
    snapshot = ProjectService.open_project_as_snapshot(result.project_name)
    assert snapshot.meta.total_segments == 4
    assert set(snapshot.bindings) == {"旁白", "小雨"}

    reference = isolated_projects / "reference.wav"
    reference.write_bytes(b"RIFF")
    bound_path = ProjectService.bind_voice(result.project_name, "旁白", str(reference))
    assert Path(bound_path).is_file()
    assert ProjectService.open_project_as_snapshot(result.project_name).bindings["旁白"]


def test_importing_app_does_not_require_network(tmp_path):
    script = """
import socket

def deny(*args, **kwargs):
    raise AssertionError('app import attempted a network connection')

socket.create_connection = deny
socket.getaddrinfo = deny
import app
print('offline-app-import-ok')
"""
    env = os.environ.copy()
    env["AUDIOBOOK_STUDIO_DATA_DIR"] = str(tmp_path / "data")
    env["AUDIOBOOK_STUDIO_LEGACY_DIR"] = str(tmp_path / "legacy")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "offline-app-import-ok" in completed.stdout
