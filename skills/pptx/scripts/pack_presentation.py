#!/usr/bin/env python3

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any

from _pptx_common import (
    SkillArgumentParser,
    output_file,
    pack_presentation_directory,
    publish_file,
    run_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="把 OOXML 目录安全打包为 PowerPoint 文件。")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from pptx import Presentation

    args = build_parser().parse_args()
    source_dir = Path(args.input_dir).expanduser().resolve()
    destination = output_file(
        args.output,
        {".pptx", ".potx", ".ppsx"},
        overwrite=args.overwrite,
    )
    with tempfile.TemporaryDirectory(prefix="pptx-pack-") as temp_name:
        staged = Path(temp_name) / destination.name
        archive = pack_presentation_directory(source_dir, staged)
        try:
            presentation = Presentation(str(staged))
            slide_count = len(presentation.slides)
        except Exception as exc:
            raise ValueError("打包结果不能被 python-pptx 打开") from exc
        publish_file(staged, destination, overwrite=args.overwrite)
    return {
        "source_dir": str(source_dir),
        "path": str(destination),
        "slide_count": slide_count,
        "archive": archive,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
