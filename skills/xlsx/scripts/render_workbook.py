#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from _xlsx_common import (
    TABULAR_INPUT_SUFFIXES,
    SkillArgumentParser,
    find_program,
    input_file,
    output_directory,
    publish_file,
    run_cli,
    run_program,
    run_soffice_convert,
)


def _pdf_page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="通过 LibreOffice 和 Poppler 把工作簿渲染为逐页 PNG。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--include-pdf", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    if args.start_page < 1:
        raise ValueError("start-page 必须大于 0")
    if args.end_page is not None and args.end_page < args.start_page:
        raise ValueError("end-page 不能小于 start-page")
    if args.max_pages < 1 or args.max_pages > 100:
        raise ValueError("max-pages 必须在 1 到 100 之间")
    if args.dpi < 72 or args.dpi > 300:
        raise ValueError("dpi 必须在 72 到 300 之间")

    source = input_file(args.input, TABULAR_INPUT_SUFFIXES)
    if source.suffix.lower() in {".csv", ".tsv"}:
        raise ValueError("CSV/TSV 请先用 convert_workbook.py 转为 .xlsx 后再渲染")
    destination_dir = output_directory(args.output_dir)

    with tempfile.TemporaryDirectory(prefix="xlsx-render-") as temp_name:
        temp_dir = Path(temp_name)
        staged_input = temp_dir / f"workbook{source.suffix.lower()}"
        shutil.copy2(source, staged_input)
        pdf_path, office_output = run_soffice_convert(
            staged_input,
            target_format="pdf",
            output_dir=temp_dir / "pdf",
            timeout=args.timeout,
        )
        page_count = _pdf_page_count(pdf_path)
        if page_count < 1:
            raise ValueError("LibreOffice 生成的 PDF 没有页面")
        if args.start_page > page_count:
            raise ValueError(f"start-page 超出页面总数 {page_count}")
        requested_end = page_count if args.end_page is None else args.end_page
        if requested_end > page_count:
            raise ValueError(f"end-page 超出页面总数 {page_count}")
        actual_end = min(
            requested_end,
            args.start_page + args.max_pages - 1,
        )

        pdftoppm = find_program("pdftoppm")
        raw_prefix = temp_dir / "raw-page"
        run_program(
            [
                pdftoppm,
                "-png",
                "-r",
                str(args.dpi),
                "-f",
                str(args.start_page),
                "-l",
                str(actual_end),
                str(pdf_path),
                str(raw_prefix),
            ],
            timeout=args.timeout,
        )
        raw_pages = sorted(
            temp_dir.glob("raw-page-*.png"),
            key=lambda path: int(path.stem.rsplit("-", 1)[1]),
        )
        expected_count = actual_end - args.start_page + 1
        if len(raw_pages) != expected_count:
            raise RuntimeError(
                f"Poppler 应生成 {expected_count} 页，实际生成 {len(raw_pages)} 页"
            )

        destinations = [
            destination_dir / f"page-{page_number:04d}.png"
            for page_number in range(args.start_page, actual_end + 1)
        ]
        if args.include_pdf:
            destinations.append(destination_dir / "workbook.pdf")
        if not args.overwrite:
            existing = [str(path) for path in destinations if path.exists()]
            if existing:
                raise FileExistsError(
                    "以下渲染目标已存在：" + "、".join(existing)
                )

        output_paths: list[str] = []
        for page_number, raw_page in zip(
            range(args.start_page, actual_end + 1),
            raw_pages,
        ):
            destination = destination_dir / f"page-{page_number:04d}.png"
            publish_file(raw_page, destination, overwrite=args.overwrite)
            output_paths.append(str(destination))

        pdf_output: Optional[str] = None
        if args.include_pdf:
            destination_pdf = destination_dir / "workbook.pdf"
            staged_pdf = temp_dir / "publish.pdf"
            shutil.copy2(pdf_path, staged_pdf)
            publish_file(
                staged_pdf,
                destination_pdf,
                overwrite=args.overwrite,
            )
            pdf_output = str(destination_pdf)

    next_page = actual_end + 1 if actual_end < requested_end else None
    return {
        "source": str(source),
        "page_count": page_count,
        "start_page": args.start_page,
        "end_page": actual_end,
        "rendered_pages": output_paths,
        "pdf": pdf_output,
        "has_more": next_page is not None,
        "next_page": next_page,
        "dpi": args.dpi,
        "office_stdout": office_output["stdout"],
        "office_stderr": office_output["stderr"],
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
