#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _docx_common import (
    DOCX_INPUT_SUFFIXES,
    SkillArgumentParser,
    input_file,
    safe_extract_docx,
    run_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="安全解包 DOCX，拒绝路径穿越、符号链接和压缩炸弹。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    source = input_file(args.input, DOCX_INPUT_SUFFIXES)
    destination = Path(args.output_dir).expanduser().resolve()
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"输出路径不是目录：{destination}")
        if any(destination.iterdir()):
            raise FileExistsError(f"输出目录已存在且不为空：{destination}")
    else:
        destination.mkdir(parents=True)
    archive = safe_extract_docx(source, destination)
    return {
        "source": str(source),
        "output_dir": str(destination),
        "archive": archive,
        "document_xml": str(destination / "word" / "document.xml"),
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
