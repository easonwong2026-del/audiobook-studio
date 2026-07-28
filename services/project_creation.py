"""项目创建编排服务：从原始书稿或结构化 JSON 创建项目。

职责：
  1. 校验项目名称和输入文件
  2. 调用 TextImporter + ScriptDirectorService 分析书稿
  3. 执行结构化校验
  4. 原子创建项目目录（失败时清理半成品）
  5. 返回创建结果

不负责：
  - 管理 UI 状态
  - 显示 Gradio 组件
  - 管理声音绑定
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ai.providers import create_provider
from lib import config, script_loader
from lib.text_importer import load_text
from repositories.project_repo import ProjectRepository
from services.ai_settings import AiSettingsService
from services.script_director import ScriptDirectorService

logger = logging.getLogger(__name__)


@dataclass
class ProjectCreationResult:
    project_name: str
    title: str
    chapter_count: int
    segment_count: int
    role_count: int
    warnings: list[str] = field(default_factory=list)


class ProjectCreationService:
    """统一编排普通创建和 JSON 创建。"""

    @staticmethod
    def _safe_name(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)

    @staticmethod
    def create_from_source(
        project_name: str,
        source_path: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> ProjectCreationResult:
        """从原始书稿创建项目（原子操作）。"""
        if not project_name or not project_name.strip():
            raise ValueError("项目名称不能为空")
        if not source_path or not os.path.isfile(source_path):
            raise ValueError(f"书稿文件不存在：{source_path}")

        safe_name = ProjectCreationService._safe_name(project_name.strip())
        data_root = config.get_projects_root()
        project_dir = os.path.join(data_root, safe_name)

        source_ext = Path(source_path).suffix.lower()
        if source_ext not in (".txt", ".docx", ".epub"):
            raise ValueError(f"不支持的文件格式：{source_ext}；请使用 .txt / .docx / .epub")

        if os.path.exists(project_dir):
            raise ValueError(f"项目「{project_name}」已存在，请使用其他名称")

        # 准备临时目录
        tmp_root = os.path.join(config.get_data_dir(), ".tmp")
        os.makedirs(tmp_root, exist_ok=True)
        tmp_dir = os.path.join(tmp_root, f"create_{uuid.uuid4().hex}")
        warnings: list[str] = []

        try:
            # 1. 读取原始文本
            text = load_text(source_path)

            # 2. 获取 AI 配置
            if provider_name:
                ai_config = AiSettingsService.get_effective_provider_config(provider_name)
            else:
                ai_config = AiSettingsService.get_effective_provider_config()

            # 3. 创建 Provider 和导演服务
            provider = create_provider(ai_config["provider"], model=ai_config.get("model") or None)
            director = ScriptDirectorService(provider)

            # 4. AI 分析
            script = director.analyze_text(
                text,
                title=title or Path(source_path).stem,
                author=author or "",
            )

            # 5. 校验结构化剧本
            script_obj = script_loader.from_dict(script)
            errors = script_loader.validate_script(script_obj)
            if errors:
                raise ValueError("剧本分析校验失败：\n" + "\n".join(f"- {e}" for e in errors))

            # 6. 写入临时项目目录
            os.makedirs(tmp_dir, exist_ok=True)
            script_path = os.path.join(tmp_dir, "structured_script.json")
            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False, indent=2)

            # 7. 创建项目（ProjectRepository.create_project 会处理原子移动）
            os.makedirs(os.path.join(tmp_dir, "segments"), exist_ok=True)
            os.makedirs(os.path.join(tmp_dir, "voices"), exist_ok=True)
            os.makedirs(os.path.join(tmp_dir, "output"), exist_ok=True)

            meta = {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "total_segments": script.get("meta", {}).get("total_segments", 0),
            }
            project_meta_path = os.path.join(tmp_dir, "project.json")
            with open(project_meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "name": safe_name,
                    "title": script.get("meta", {}).get("title", title or safe_name),
                    "created_at": meta["created_at"],
                    "chapters": len(script.get("chapters", [])),
                    "segments": meta["total_segments"],
                    "roles": list((script.get("voices") or {}).keys()),
                    "script": str(script_path),
                    "segments_dir": str(os.path.join(tmp_dir, "segments")),
                    "voices_dir": str(os.path.join(tmp_dir, "voices")),
                    "output_dir": str(os.path.join(tmp_dir, "output")),
                }, f, ensure_ascii=False, indent=2)

            # 7. 原子移动到正式项目目录
            os.makedirs(os.path.dirname(project_dir), exist_ok=True)
            shutil.copytree(tmp_dir, project_dir, dirs_exist_ok=True)

            chapters = script.get("chapters", [])
            roles = script.get("voices", {})
            total_segments = meta["total_segments"]

            return ProjectCreationResult(
                project_name=safe_name,
                title=script.get("meta", {}).get("title", safe_name),
                chapter_count=len(chapters),
                segment_count=total_segments,
                role_count=len(roles),
                warnings=warnings,
            )
        except Exception:
            # 清理临时目录
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        finally:
            # 确保临时目录被删除
            if os.path.exists(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

    @staticmethod
    def create_from_structured_script(
        project_name: str,
        script_path: str,
    ) -> ProjectCreationResult:
        """从结构化 JSON 创建项目。"""
        if not project_name or not project_name.strip():
            raise ValueError("项目名称不能为空")
        if not script_path or not os.path.isfile(script_path):
            raise ValueError(f"剧本文件不存在：{script_path}")

        safe_name = ProjectCreationService._safe_name(project_name.strip())
        data_root = config.get_projects_root()
        project_dir = os.path.join(data_root, safe_name)

        if os.path.exists(project_dir):
            raise ValueError(f"项目「{project_name}」已存在，请使用其他名称")

        # 加载并校验剧本
        script = script_loader.load_script(script_path)
        errors = script_loader.validate_script(script)
        if errors:
            raise ValueError("剧本校验失败：\n" + "\n".join(f"- {e}" for e in errors))

        # 直接通过已有 ProjectRepository 创建
        ProjectRepository.create_project(safe_name, script_path)
        result = ProjectCreationResult(
            project_name=safe_name,
            title=script.get("meta", {}).get("title", safe_name),
            chapter_count=len(script.get("chapters", [])),
            segment_count=script.get("meta", {}).get("total_segments",
                        sum(len(ch.get("segments", [])) for ch in script.get("chapters", []))),
            role_count=len(script.get("voices", {})),
        )
        return result
