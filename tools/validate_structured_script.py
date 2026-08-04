#!/usr/bin/env python3
"""Offline validator for the external Agent ``structured_script.json`` contract."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.structured_script_import import (
    StructuredScriptImportService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线检查外部 Agent 生成的 structured_script.json"
    )
    parser.add_argument("file", type=Path, help="JSON 文件路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        preview = StructuredScriptImportService.inspect(str(args.file))
    except (OSError, ValueError, TypeError) as exc:
        print("INVALID")
        print(f"错误：{exc}")
        return 2

    print("VALID" if preview.valid else "INVALID")
    print(f"作品：{preview.title}")
    print(f"作者：{preview.author}")
    print(f"章节：{preview.chapter_count}")
    print(f"片段：{preview.segment_count}")
    print(f"角色：{preview.role_count}")
    print(f"警告：{len(preview.warnings)}")
    print(f"错误：{len(preview.errors)}")
    if preview.warnings:
        print("\n警告：")
        for item in preview.warnings:
            print(f"- {item}")
    if preview.errors:
        print("\n错误：")
        for item in preview.errors:
            print(f"- {item}")
    return 0 if preview.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
