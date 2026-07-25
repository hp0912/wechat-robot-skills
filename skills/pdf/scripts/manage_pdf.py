#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from _pdf_common import (
    SkillArgumentParser,
    input_pdf,
    new_temp_pdf,
    output_directory,
    output_pdf,
    parse_page_spec,
    publish_temp_file,
    run_cli,
)


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="合并、拆分或旋转 PDF 页面")
    operations = parser.add_subparsers(dest="operation", required=True)

    merge = operations.add_parser("merge", help="合并多个 PDF")
    merge.add_argument("--input", action="append", required=True, help="输入 PDF，可重复")
    merge.add_argument("--output", required=True, help="合并后的 PDF")
    merge.add_argument("--overwrite", action="store_true")

    split = operations.add_parser("split", help="按页码范围拆分 PDF")
    split.add_argument("--input", required=True)
    split.add_argument("--output-dir", required=True)
    split.add_argument(
        "--range",
        dest="ranges",
        action="append",
        help="页码或页码范围，如 1-3；可重复。缺省时每页一个文件",
    )
    split.add_argument("--overwrite", action="store_true")

    rotate = operations.add_parser("rotate", help="旋转指定页面")
    rotate.add_argument("--input", required=True)
    rotate.add_argument("--output", required=True)
    rotate.add_argument("--pages", help="页码列表，如 1,3-5；缺省时旋转全部页面")
    rotate.add_argument("--degrees", type=int, choices=(90, 180, 270), required=True)
    rotate.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def _metadata(reader) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in (reader.metadata or {}).items()
        if value is not None
    }


def _merge(args) -> dict[str, Any]:
    from pypdf import PdfReader, PdfWriter

    inputs = [input_pdf(value) for value in args.input]
    if len(inputs) < 2:
        raise ValueError("merge 至少需要两个 --input")
    output = output_pdf(args.output, args.overwrite)
    if output in inputs:
        raise ValueError("输出文件不能与输入文件相同")
    temporary = new_temp_pdf(output)
    writer = PdfWriter()

    try:
        with ExitStack() as stack:
            readers = []
            for path in inputs:
                stream = stack.enter_context(path.open("rb"))
                reader = PdfReader(stream, strict=False)
                if reader.is_encrypted:
                    raise ValueError(f"PDF 已加密，无法合并：{path}")
                readers.append(reader)
                for page in reader.pages:
                    writer.add_page(page)
            if readers:
                writer.add_metadata(_metadata(readers[0]))
            with temporary.open("wb") as stream:
                writer.write(stream)
        page_count = len(writer.pages)
        publish_temp_file(temporary, output, args.overwrite)
    finally:
        writer.close()
        temporary.unlink(missing_ok=True)

    return {
        "operation": "merge",
        "inputs": [str(path) for path in inputs],
        "output": str(output),
        "page_count": page_count,
    }


def _split(args) -> dict[str, Any]:
    from pypdf import PdfReader, PdfWriter

    path = input_pdf(args.input)
    destination = output_directory(args.output_dir)
    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法拆分")
        page_count = len(reader.pages)
        if args.ranges:
            groups = [
                (value, parse_page_spec(value, page_count))
                for value in args.ranges
            ]
        else:
            groups = [(str(page), [page]) for page in range(1, page_count + 1)]

        outputs = []
        for label, pages in groups:
            safe_label = re.sub(r"[^0-9_-]+", "_", label.replace(",", "_"))
            output = destination / f"{path.stem}_pages_{safe_label}.pdf"
            if output.exists() and not args.overwrite:
                raise FileExistsError(f"目标文件已存在：{output}")
            outputs.append((output, pages))

        created = []
        for output, pages in outputs:
            temporary = new_temp_pdf(output)
            writer = PdfWriter()
            try:
                for page_number in pages:
                    writer.add_page(reader.pages[page_number - 1])
                writer.add_metadata(_metadata(reader))
                with temporary.open("wb") as output_stream:
                    writer.write(output_stream)
                publish_temp_file(temporary, output, args.overwrite)
                created.append(
                    {
                        "path": str(output),
                        "pages": pages,
                        "page_count": len(pages),
                    }
                )
            finally:
                writer.close()
                temporary.unlink(missing_ok=True)

    return {
        "operation": "split",
        "input": str(path),
        "output_dir": str(destination),
        "source_page_count": page_count,
        "files": created,
    }


def _rotate(args) -> dict[str, Any]:
    from pypdf import PdfReader, PdfWriter

    path = input_pdf(args.input)
    output = output_pdf(args.output, args.overwrite)
    if output == path:
        raise ValueError("输出文件不能与输入文件相同")
    temporary = new_temp_pdf(output)
    writer = PdfWriter()

    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            if reader.is_encrypted:
                raise ValueError("PDF 已加密，无法旋转")
            page_count = len(reader.pages)
            selected = set(parse_page_spec(args.pages, page_count))
            for page_number, page in enumerate(reader.pages, start=1):
                if page_number in selected:
                    page.rotate(args.degrees)
                writer.add_page(page)
            writer.add_metadata(_metadata(reader))
            with temporary.open("wb") as output_stream:
                writer.write(output_stream)
        publish_temp_file(temporary, output, args.overwrite)
    finally:
        writer.close()
        temporary.unlink(missing_ok=True)

    return {
        "operation": "rotate",
        "input": str(path),
        "output": str(output),
        "page_count": page_count,
        "rotated_pages": sorted(selected),
        "degrees": args.degrees,
    }


def _execute(args) -> dict[str, Any]:
    if args.operation == "merge":
        return _merge(args)
    if args.operation == "split":
        return _split(args)
    if args.operation == "rotate":
        return _rotate(args)
    raise ValueError(f"不支持的操作：{args.operation}")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _execute(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
