#!/usr/bin/env python3
"""V3 本地环境、项目和导出验收入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import segment_cache
from services.environment_diagnostics import (
    diagnostics_to_markdown,
    run_environment_diagnostics,
)
from services.structured_script_import import (
    StructuredScriptImportService,
)


def check_environment() -> tuple[int, dict]:
    report = run_environment_diagnostics()
    print(diagnostics_to_markdown(report))
    return (2 if report["status"] == "error" else 0), report


def _project_dir(name: str) -> Path:
    from repositories.project_repo import ProjectRepository

    return Path(ProjectRepository.get_project_dir(name))


def find_existing_segment_audio(segments_dir: Path, segment: dict) -> Path | None:
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
            preview = StructuredScriptImportService.inspect(str(script_path), name)
            report["errors"].extend(preview.errors)
            report["warnings"].extend(preview.warnings)
            raw = preview.raw
            segments = [
                seg for chapter in raw.get("chapters", []) if isinstance(chapter, dict)
                for seg in chapter.get("segments", []) if isinstance(seg, dict)
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
        except Exception as exc:  # noqa: BLE001 - acceptance must report every failure
            report["errors"].append(f"读取项目失败：{type(exc).__name__}: {exc}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (2 if report["errors"] else 0), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--environment", action="store_true")
    group.add_argument("--project")
    group.add_argument("--export-check", metavar="PROJECT")
    args = parser.parse_args(argv)
    if args.environment:
        return check_environment()[0]
    if args.project:
        return check_project(args.project)[0]
    code, report = check_project(args.export_check)
    return 0 if code == 0 and report.get("export_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
