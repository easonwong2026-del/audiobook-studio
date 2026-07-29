#!/usr/bin/env python3
"""v3.3.1 真实环境验收入口（默认不产生 API 消费）。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import config, script_loader, segment_cache
from services.ai_settings import AiSettingsService
from services.environment_diagnostics import diagnostics_to_markdown, run_environment_diagnostics
from services.script_consistency import check_script_consistency


def check_environment() -> tuple[int, dict]:
    report = run_environment_diagnostics()
    print(diagnostics_to_markdown(report))
    return (2 if report["status"] == "error" else 0), report


def _project_dir(name: str) -> Path:
    from repositories.project_repo import ProjectRepository
    return Path(ProjectRepository.get_project_dir(name))


def find_existing_segment_audio(
    segments_dir: Path,
    segment: dict,
) -> Path | None:
    """按正式导出链路的缓存参数查找当前 segment 音频。"""
    delivery = segment.get("delivery") if isinstance(segment.get("delivery"), dict) else {}
    found = segment_cache.find_segment_wav(
        str(segments_dir),
        str(segment.get("id", "")),
        str(segment.get("text", "")),
        str(segment.get("role") or segment.get("speaker") or ""),
        str(segment.get("emotion") or "neutral"),
        segment.get("emo_alpha", segment.get("emotion_strength", delivery.get("intensity", 1.0))),
        segment.get("speech_rate", delivery.get("speed", 1.0)),
        segment.get("pinyin_hints"),
        segment_cache.director_metadata_for(segment),
    )
    return Path(found) if found else None


def check_project(name: str) -> tuple[int, dict]:
    project = _project_dir(name)
    required = ["project.json", "structured_script.json", "voice_bindings.json", "segments", "output"]
    missing = [item for item in required if not (project / item).exists()]
    report = {"project": name, "path": str(project), "missing": missing, "errors": [], "warnings": []}
    if missing:
        report["errors"].append("缺少必需项目内容：" + ", ".join(missing))
    script_path = project / "structured_script.json"
    if script_path.is_file():
        try:
            raw = json.loads(script_path.read_text(encoding="utf-8"))
            validation = script_loader.validate_script(script_loader.from_dict(raw))
            if validation:
                report["errors"].extend(validation)
            consistency = check_script_consistency(raw)
            report["errors"].extend(i["message"] for i in consistency["issues"] if i["severity"] == "error")
            report["warnings"].extend(i["message"] for i in consistency["issues"] if i["severity"] == "warning")
            segments = [
                seg for chapter in raw.get("chapters", [])
                for seg in chapter.get("segments", [])
            ]
            roles = set(raw.get("voices", {}))
            bindings_path = project / "voice_bindings.json"
            bindings_raw = json.loads(bindings_path.read_text(encoding="utf-8")) if bindings_path.is_file() else {}
            bindings = bindings_raw.get("bindings", bindings_raw)
            bound = sorted(role for role in roles if bindings.get(role))
            missing_voices = sorted(roles - set(bound))
            segments_dir = project / "segments"
            found_audio = {
                str(seg.get("id", ""))
                for seg in segments
                if find_existing_segment_audio(segments_dir, seg)
            }
            segment_ids = {str(seg.get("id", "")) for seg in segments}
            missing_audio = sorted(segment_ids - found_audio)
            report.update({
                "segment_count": len(segments),
                "bound_role_count": len(bound),
                "missing_voice_roles": missing_voices,
                "synthesized_segment_count": len(found_audio),
                "missing_audio_segments": missing_audio,
                "export_ready": not report["errors"] and not missing_voices and not missing_audio,
            })
            if missing_voices:
                report["warnings"].append("缺失音色角色：" + ", ".join(missing_voices))
            if missing_audio:
                report["warnings"].append(f"缺失 {len(missing_audio)} 个段落音频")
        except Exception as exc:
            report["errors"].append(f"读取项目失败：{type(exc).__name__}: {exc}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (2 if report["errors"] else 0), report


def check_provider(provider: str, allow_real_request: bool, timeout: float) -> tuple[int, dict]:
    if provider not in {"openai", "deepseek"}:
        report = {"provider": provider, "status": "error", "message": "仅支持 openai / deepseek"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2, report
    configured = AiSettingsService.has_api_key(provider)
    cfg = AiSettingsService.get_effective_provider_config(provider)
    report = {
        "provider": provider,
        "model": cfg.get("model") or "Provider 默认模型",
        "base_url": cfg.get("base_url") or "Provider 默认地址",
        "key_configured": configured,
        "key_source": AiSettingsService.get_api_key_source(provider),
        "real_request_sent": False,
    }
    if not configured:
        report.update(status="error", message="Provider API Key 未配置")
    elif not allow_real_request:
        report.update(status="ok", message="配置存在；未传 --allow-real-request，因此未发送网络请求")
    else:
        print(
            f"即将对 {provider} / {report['model']} 执行连接与认证检查；"
            "不验证具体模型推理能力。",
            file=sys.stderr,
        )
        api_key = cfg.get("api_key", "")
        try:
            result = AiSettingsService.check_connection(
                provider,
                api_key=api_key,
                base_url=cfg.get("base_url", ""),
                timeout=timeout,
            )
        except Exception as exc:
            result = f"❌ 连接失败：{type(exc).__name__}: {exc}"
        if api_key:
            result = result.replace(api_key, "***")
        report.update(
            status="ok" if result.startswith("✅") else "error",
            message=result,
            real_request_sent=True,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (0 if report["status"] == "ok" else 2), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--environment", action="store_true")
    group.add_argument("--project")
    group.add_argument("--provider", choices=["openai", "deepseek"])
    group.add_argument("--export-check", metavar="PROJECT")
    parser.add_argument("--allow-real-request", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.environment:
        return check_environment()[0]
    if args.project:
        return check_project(args.project)[0]
    if args.export_check:
        code, report = check_project(args.export_check)
        return 0 if code == 0 and report.get("export_ready") else 2
    return check_provider(args.provider, args.allow_real_request, args.timeout)[0]


if __name__ == "__main__":
    raise SystemExit(main())
