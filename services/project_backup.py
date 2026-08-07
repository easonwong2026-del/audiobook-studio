"""Portable, integrity-checked project backup and restore."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import time
import uuid
import zipfile
from typing import Any

from lib import config, script_loader
from repositories.project_repo import ProjectRepository, sanitize_project_name
from repositories.project_storage_repo import ProjectStorageRepository


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def _zip_relative(name: str) -> str:
    """Normalize and validate a ZIP member name against Zip Slip."""
    normalized = str(name or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError(f"备份包含不安全路径：{name}")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError(f"备份包含上级目录路径：{name}")
    safe = posixpath.normpath("/".join(parts))
    if safe in {"", ".", ".."} or safe.startswith("../"):
        raise ValueError(f"备份包含不安全路径：{name}")
    return safe


def _hash_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class ProjectBackupService:
    """Create and restore ``.audiobook-project.zip`` packages."""

    @staticmethod
    def _files(project_dir: str) -> list[tuple[str, str]]:
        files: list[tuple[str, str]] = []
        for root, dirs, names in os.walk(project_dir, followlinks=False):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
            for name in names:
                path = os.path.join(root, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                relative = os.path.relpath(path, project_dir).replace(os.sep, "/")
                files.append((_zip_relative(relative), path))
        files.sort(key=lambda item: item[0])
        return files

    @staticmethod
    def create_backup(project_name: str, target_dir: str | None = None) -> str:
        safe_name, project_dir = ProjectStorageRepository._resolve_project(project_name)
        data_root = os.path.realpath(config.get_data_dir())
        output_dir = os.path.abspath(os.path.expanduser(target_dir)) if target_dir else os.path.join(data_root, "backups")
        if not output_dir or os.path.islink(output_dir):
            raise ValueError("备份目标目录无效")
        os.makedirs(output_dir, exist_ok=True)
        archive_path = os.path.normpath(
            os.path.join(output_dir, f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.audiobook-project.zip")
        )
        if _inside(archive_path, project_dir):
            raise ValueError("备份文件不能写入项目自身目录")

        manifest_files: list[dict[str, Any]] = []
        files = ProjectBackupService._files(project_dir)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, path in files:
                digest, size = _hash_file(path)
                archive.write(path, arcname=relative)
                manifest_files.append({"path": relative, "sha256": digest, "size": size})
            manifest = {
                "format": "audiobook-studio-project",
                "format_version": 1,
                "project_name": safe_name,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "file_count": len(manifest_files),
                "total_bytes": sum(item["size"] for item in manifest_files),
                "files": manifest_files,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return archive_path

    @staticmethod
    def _validate_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise ValueError("备份缺少 manifest.json")
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"备份包含符号链接：{info.filename}")
            if info.is_dir():
                continue
            _zip_relative(info.filename)
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"manifest.json 无法解析：{exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != "audiobook-studio-project":
            raise ValueError("不是有效的 Audiobook Studio 项目备份")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("备份清单缺少 files")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        members = set(names)
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("备份清单包含无效条目")
            path = _zip_relative(item.get("path"))
            if path in seen or path not in members or path == "manifest.json":
                raise ValueError(f"备份清单中的文件无效：{path}")
            digest = str(item.get("sha256") or "")
            size = item.get("size")
            if len(digest) != 64 or not isinstance(size, int) or size < 0:
                raise ValueError(f"备份清单缺少校验信息：{path}")
            seen.add(path)
            normalized.append({"path": path, "sha256": digest, "size": size})
        if manifest.get("file_count") != len(normalized):
            raise ValueError("备份文件数量与清单不一致")
        return manifest, normalized

    @staticmethod
    def restore_backup(archive_path: str, project_name: str | None = None) -> str:
        source = os.path.abspath(os.path.expanduser(str(archive_path or "")))
        if not os.path.isfile(source):
            raise FileNotFoundError("备份文件不存在")
        workspace = os.path.abspath(ProjectRepository.WORKSPACE_ROOT or config.get_projects_root())
        os.makedirs(workspace, exist_ok=True)
        temporary = os.path.join(workspace, f".tmp_restore_{uuid.uuid4().hex}")
        try:
            with zipfile.ZipFile(source, "r") as archive:
                manifest, files = ProjectBackupService._validate_manifest(archive)
                requested = project_name or manifest.get("project_name")
                safe_name = sanitize_project_name(str(requested or ""))
                if safe_name != str(requested or ""):
                    raise ValueError("恢复项目名称无效")
                inspection = ProjectRepository.inspect_project_slot(safe_name)
                if inspection.status != "available":
                    raise FileExistsError(f"恢复目标已存在或不可用：{inspection.path}")
                final_dir = os.path.join(workspace, safe_name)
                if not _inside(final_dir, workspace) or final_dir == workspace:
                    raise ValueError("恢复目标路径不安全")
                os.makedirs(temporary, exist_ok=False)
                for item in files:
                    relative = item["path"]
                    target = os.path.abspath(os.path.join(temporary, *relative.split("/")))
                    if not _inside(target, temporary):
                        raise ValueError(f"恢复路径越界：{relative}")
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(relative, "r") as input_file, open(target, "wb") as output_file:
                        while True:
                            chunk = input_file.read(1024 * 1024)
                            if not chunk:
                                break
                            output_file.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    if size != item["size"] or digest.hexdigest() != item["sha256"]:
                        raise ValueError(f"文件校验失败：{relative}")

            required = ("project.json", "structured_script.json", "voice_bindings.json")
            if not all(os.path.isfile(os.path.join(temporary, marker)) for marker in required):
                raise ValueError("备份缺少项目核心文件")
            with open(os.path.join(temporary, "project.json"), encoding="utf-8") as file:
                meta = json.load(file)
            if not isinstance(meta, dict) or meta.get("project_name") != safe_name:
                raise ValueError("project.json 的项目名与恢复目标不一致")
            with open(os.path.join(temporary, "structured_script.json"), encoding="utf-8") as file:
                script = script_loader.from_dict(json.load(file))
            errors = script_loader.validate_script(script)
            if errors:
                raise ValueError("恢复项目剧本校验失败：" + "；".join(errors[:3]))
            os.replace(temporary, final_dir)
            return os.path.normpath(final_dir)
        except Exception:
            if os.path.lexists(temporary):
                shutil.rmtree(temporary, ignore_errors=True)
            raise


__all__ = ["ProjectBackupService"]
