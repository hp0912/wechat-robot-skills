#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Any

from _docx_common import (
    SkillArgumentParser,
    inspect_archive,
    output_file,
    pack_docx_directory,
    publish_file,
    run_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="把安全解包并编辑后的 OOXML 目录重新打包为 DOCX。"
    )
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    source_dir = Path(args.input_dir).expanduser().resolve()
    destination = output_file(
        args.output,
        {".docx"},
        overwrite=args.overwrite,
    )
    if destination == source_dir or source_dir in destination.parents:
        raise ValueError("输出 DOCX 不能放在待打包目录内部")
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".docx",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        archive = pack_docx_directory(source_dir, temp_path)
        if archive["missing_required_parts"]:
            raise ValueError(
                "打包结果缺少必要部件："
                + "、".join(archive["missing_required_parts"])
            )
        Document(str(temp_path))
        publish_file(temp_path, destination, overwrite=args.overwrite)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {
        "path": str(destination),
        "input_dir": str(source_dir),
        "archive": inspect_archive(destination),
        "requires_validation": True,
        "requires_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
