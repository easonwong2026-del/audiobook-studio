"""项目创建编排服务：从原始书稿或结构化 JSON 创建项目。

职责：
  1. 校验项目名称和输入文件
  2. 调用 TextImporter + ScriptDirectorService 分析书稿
  3. 执行结构化校验
  4. 委托 ProjectRepository 创建项目
  5. 失败时清理临时产物

不负责：管理 UI 状态、显示 Gradio 组件、管理声音绑定。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
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
    def _cleanup_tmp(tmp_dir: str) -> None:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def create_from_source(
        project_name: str,
        source_path: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> ProjectCreationResult:
        """从原始书稿创建项目。

        AI 分析完成后调用 ``ProjectRepository.create_project``，
        不维护第二套 project.json / voice_bindings.json 创建逻辑。
        """
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

        tmp_root = os.path.join(config.get_data_dir(), ".tmp")
        os.makedirs(tmp_root, exist_ok=True)
        tmp_dir = os.path.join(tmp_root, f"create_{uuid.uuid4().hex}")
        warnings: list[str] = []
        script_path = ""

        try:
            # 1. 读取原始文本
            text = load_text(source_path)

            # 2. 获取 AI 配置（含密钥、基地址、超时）
            ai_config = (
                AiSettingsService.get_effective_provider_config(provider_name)
                if provider_name
                else AiSettingsService.get_effective_provider_config()
            )

            # 3. 创建 Provider（密钥 / base_url / timeout 显式透传）
            provider = create_provider(
                ai_config["provider"],
                model=ai_config.get("model") or None,
                api_key=ai_config.get("api_key") or None,
                base_url=ai_config.get("base_url") or None,
                timeout=int(ai_config.get("timeout", 180)),
            )
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

            # 6. 写入临时 structured_script.json（仅用于传给 ProjectRepository）
            os.makedirs(tmp_dir, exist_ok=True)
            script_path = os.path.join(tmp_dir, "structured_script.json")
            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False, indent=2)

            # 7. 委托 ProjectRepository 创建项目
            ProjectRepository.create_project(safe_name, script_path)

            chapters = script.get("chapters", [])
            roles = script.get("voices", {})
            total_segments = script.get("meta", {}).get(
                "total_segments",
                sum(len(ch.get("segments", [])) for ch in chapters),
            )

            result = ProjectCreationResult(
                project_name=safe_name,
                title=script.get("meta", {}).get("title", safe_name),
                chapter_count=len(chapters),
                segment_count=total_segments,
                role_count=len(roles),
                warnings=warnings,
            )
            return result
        except Exception:
            raise
        finally:
            # 清理临时 script 文件（项目目录已由 ProjectRepository 创建）
            ProjectCreationService._cleanup_tmp(tmp_dir)

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

        script = script_loader.load_script(script_path)
        errors = script_loader.validate_script(script)
        if errors:
            raise ValueError("剧本校验失败：\n" + "\n".join(f"- {e}" for e in errors))

        # load_script 返回 Script 对象；重新读原始 JSON 获取统计信息
        with open(script_path, encoding="utf-8") as f:
            raw = json.load(f)
        ProjectRepository.create_project(safe_name, script_path)
        return ProjectCreationResult(
            project_name=safe_name,
            title=raw.get("meta", {}).get("title", safe_name),
            chapter_count=len(raw.get("chapters", [])),
            segment_count=raw.get("meta", {}).get(
                "total_segments",
                sum(len(ch.get("segments", [])) for ch in raw.get("chapters", [])),
            ),
            role_count=len(raw.get("voices", {})),
        )
