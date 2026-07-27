#!/usr/bin/env python3

from __future__ import annotations

import argparse
from typing import Any

from _pptx_common import (
    OOXML_PRESENTATION_SUFFIXES,
    SkillArgumentParser,
    input_file,
    output_directory,
    run_cli,
    safe_extract_presentation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="安全解包 PowerPoint OOXML 文件。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    source = input_file(args.input, OOXML_PRESENTATION_SUFFIXES)
    destination = output_directory(args.output_dir)
    if any(destination.iterdir()):
        raise ValueError("output-dir 必须为空目录")
    archive = safe_extract_presentation(source, destination)
    return {
        "source": str(source),
        "path": str(destination),
        "archive": archive,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
