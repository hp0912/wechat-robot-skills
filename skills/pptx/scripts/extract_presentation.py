#!/usr/bin/env python3

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any

from _pptx_common import (
    OOXML_PRESENTATION_SUFFIXES,
    SkillArgumentParser,
    input_file,
    output_file,
    publish_file,
    run_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="把演示文稿正文提取为 Markdown。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-chars", type=int, default=2_000_000)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from markitdown import MarkItDown

    args = build_parser().parse_args()
    if args.max_chars < 1000 or args.max_chars > 10_000_000:
        raise ValueError("max-chars 必须在 1000 到 10000000 之间")
    source = input_file(args.input, OOXML_PRESENTATION_SUFFIXES)
    destination = output_file(args.output, {".md"}, overwrite=args.overwrite)

    result = MarkItDown(enable_plugins=False).convert(str(source))
    markdown = result.text_content or ""
    truncated = len(markdown) > args.max_chars
    if truncated:
        markdown = markdown[: args.max_chars]
        markdown += "\n\n<!-- 内容因 max-chars 限制而截断 -->\n"
    with tempfile.TemporaryDirectory(prefix="pptx-extract-") as temp_name:
        staged = Path(temp_name) / "presentation.md"
        staged.write_text(markdown, encoding="utf-8")
        publish_file(staged, destination, overwrite=args.overwrite)
    return {
        "source": str(source),
        "path": str(destination),
        "characters": len(markdown),
        "truncated": truncated,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
