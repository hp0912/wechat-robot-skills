#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from _docx_common import (
    DOCUMENT_OUTPUT_SUFFIXES,
    WORD_INPUT_SUFFIXES,
    SkillArgumentParser,
    find_program,
    input_file,
    output_file,
    publish_file,
    run_cli,
    run_program,
    run_soffice_convert,
)


def _plain_text(document: Any) -> str:
    chunks: list[str] = []
    for paragraph in document.paragraphs:
        chunks.append(paragraph.text)
    for table_index, table in enumerate(document.tables, start=1):
        chunks.append(f"\n[表格 {table_index}]")
        for row in table.rows:
            chunks.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(chunks).rstrip() + "\n"


def _markdown(document: Any) -> str:
    chunks: list[str] = []
    for paragraph in document.paragraphs:
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name.startswith("Heading "):
            try:
                level = int(style_name.split()[-1])
            except ValueError:
                level = 1
            chunks.append(f"{'#' * min(max(level, 1), 6)} {paragraph.text}")
        elif style_name.startswith("List Bullet"):
            chunks.append(f"- {paragraph.text}")
        elif style_name.startswith("List Number"):
            chunks.append(f"1. {paragraph.text}")
        else:
            chunks.append(paragraph.text)
        chunks.append("")
    for table in document.tables:
        rows = [[cell.text.replace("|", "\\|") for cell in row.cells] for row in table.rows]
        if not rows:
            continue
        chunks.append("| " + " | ".join(rows[0]) + " |")
        chunks.append("| " + " | ".join("---" for _ in rows[0]) + " |")
        for row in rows[1:]:
            chunks.append("| " + " | ".join(row) + " |")
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n"


def _extract_text(
    source: Path,
    destination: Path,
    *,
    markdown: bool,
    track_changes: str,
    timeout: int,
) -> str:
    pandoc = shutil.which("pandoc")
    if pandoc:
        target = "gfm" if markdown else "plain"
        completed = run_program(
            [
                pandoc,
                f"--track-changes={track_changes}",
                "-t",
                target,
                "-o",
                str(destination),
                str(source),
            ],
            timeout=timeout,
        )
        return "pandoc"

    from docx import Document

    if track_changes != "accept":
        raise FileNotFoundError(
            "运行环境缺少 pandoc；纯 Python 回退只支持 --track-changes accept"
        )
    document = Document(str(source))
    text = _markdown(document) if markdown else _plain_text(document)
    destination.write_text(text, encoding="utf-8")
    return "python-docx"


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="把 Word 文档转换为 DOCX、PDF、Markdown 或纯文本。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--track-changes",
        default="accept",
        choices=["accept", "reject", "all"],
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    source = input_file(args.input, WORD_INPUT_SUFFIXES)
    destination = output_file(
        args.output,
        DOCUMENT_OUTPUT_SUFFIXES,
        overwrite=args.overwrite,
    )
    if source == destination:
        raise ValueError("输出路径不能与输入文件相同")
    source_suffix = source.suffix.lower()
    output_suffix = destination.suffix.lower()
    engine = ""
    office_output: dict[str, str] = {"stdout": "", "stderr": ""}

    with tempfile.TemporaryDirectory(prefix="docx-convert-") as temp_name:
        temp_dir = Path(temp_name)
        working_source = source
        if source_suffix in {".doc", ".dotx"}:
            converted, office_output = run_soffice_convert(
                source,
                target_format="docx",
                output_dir=temp_dir / "docx",
                timeout=args.timeout,
                filter_name="Office Open XML Text",
            )
            working_source = converted
            source_suffix = ".docx"

        descriptor, temp_output_name = tempfile.mkstemp(
            prefix="converted-",
            suffix=output_suffix,
            dir=str(destination.parent),
        )
        os.close(descriptor)
        temp_output = Path(temp_output_name)
        temp_output.unlink()
        try:
            if output_suffix == ".docx":
                if source.suffix.lower() == ".docx":
                    raise ValueError("输入和输出都是 .docx，无需转换")
                shutil.copy2(working_source, temp_output)
                engine = "libreoffice"
            elif output_suffix == ".pdf":
                converted, office_output = run_soffice_convert(
                    working_source,
                    target_format="pdf",
                    output_dir=temp_dir / "pdf",
                    timeout=args.timeout,
                )
                shutil.copy2(converted, temp_output)
                engine = "libreoffice"
            elif output_suffix in {".txt", ".md"}:
                engine = _extract_text(
                    working_source,
                    temp_output,
                    markdown=output_suffix == ".md",
                    track_changes=args.track_changes,
                    timeout=args.timeout,
                )
            else:
                raise ValueError("不支持的目标格式")
            publish_file(temp_output, destination, overwrite=args.overwrite)
        finally:
            if temp_output.exists():
                temp_output.unlink()

    return {
        "path": str(destination),
        "source": str(source),
        "source_format": source.suffix.lower(),
        "output_format": output_suffix,
        "engine": engine,
        "track_changes": args.track_changes,
        "office_stdout": office_output["stdout"],
        "office_stderr": office_output["stderr"],
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
