#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Any

from _pptx_common import (
    PRESENTATION_INPUT_SUFFIXES,
    SkillArgumentParser,
    input_file,
    output_file,
    publish_file,
    run_cli,
    run_soffice_convert,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="转换 PowerPoint 演示文稿格式。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from pptx import Presentation

    args = build_parser().parse_args()
    source = input_file(args.input, PRESENTATION_INPUT_SUFFIXES)
    destination = output_file(
        args.output,
        {".pptx", ".pdf"},
        overwrite=args.overwrite,
    )
    target_suffix = destination.suffix.lower()
    if source.suffix.lower() == ".ppt" and target_suffix != ".pptx":
        raise ValueError("旧版 .ppt 必须先转换为 .pptx，再转换为 PDF")

    with tempfile.TemporaryDirectory(prefix="pptx-convert-") as temp_name:
        temp_dir = Path(temp_name)
        staged_source = temp_dir / f"source{source.suffix.lower()}"
        shutil.copy2(source, staged_source)
        office_output = {"stdout": "", "stderr": ""}
        if target_suffix == ".pptx" and source.suffix.lower() == ".pptx":
            converted = temp_dir / "converted.pptx"
            shutil.copy2(staged_source, converted)
        else:
            converted, office_output = run_soffice_convert(
                staged_source,
                target_format=target_suffix.lstrip("."),
                output_dir=temp_dir / "converted",
                timeout=args.timeout,
                filter_name=(
                    "Impress MS PowerPoint 2007 XML"
                    if target_suffix == ".pptx"
                    else None
                ),
            )
        if target_suffix == ".pptx":
            presentation = Presentation(str(converted))
            page_count = len(presentation.slides)
            if page_count < 1:
                raise ValueError("转换后的演示文稿不包含页面")
        else:
            from pypdf import PdfReader

            page_count = len(PdfReader(str(converted)).pages)
            if page_count < 1:
                raise ValueError("转换后的 PDF 不包含页面")
        staged_output = temp_dir / f"publish{target_suffix}"
        shutil.copy2(converted, staged_output)
        publish_file(staged_output, destination, overwrite=args.overwrite)
    return {
        "source": str(source),
        "path": str(destination),
        "format": target_suffix.lstrip("."),
        "page_count": page_count,
        "size_bytes": destination.stat().st_size,
        "office_stdout": office_output["stdout"],
        "office_stderr": office_output["stderr"],
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
