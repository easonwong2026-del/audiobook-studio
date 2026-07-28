#!/usr/bin/env python3
"""小说文件 → structured_script.json v3 命令行入口。"""
from __future__ import annotations

import argparse
from pathlib import Path

from ai.providers import create_provider
from services.script_director import ScriptDirectorService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 AI 剧本导演 Pipeline 分析小说并生成 structured_script v3",
    )
    parser.add_argument("input", help="TXT、DOCX 或 EPUB 小说文件")
    parser.add_argument(
        "-o",
        "--output",
        help="输出 JSON 路径；默认与输入文件同目录，文件名为 structured_script.json",
    )
    parser.add_argument("--title", default="", help="作品标题；默认使用输入文件名")
    parser.add_argument("--author", default="", help="作者")
    parser.add_argument(
        "--provider",
        choices=["local", "openai", "deepseek"],
        default="local",
        help="分析 Provider；默认 local",
    )
    parser.add_argument(
        "--model",
        default="",
        help="远程模型；留空使用 Provider 默认值或环境变量配置",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = Path(args.input)
    output = Path(args.output) if args.output else source.with_name("structured_script.json")
    provider = create_provider(args.provider, model=args.model or None)
    script = ScriptDirectorService(provider).analyze_file(
        str(source),
        output_path=str(output),
        title=args.title,
        author=args.author,
    )
    print(
        f"已生成 {output}："
        f"{len(script['chapters'])} 章，{script['meta']['total_segments']} 段，"
        f"{len(script['voices'])} 个角色，Provider={provider.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
