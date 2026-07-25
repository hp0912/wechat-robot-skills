#!/usr/bin/env python3

from __future__ import annotations

import sys
from typing import Any, Optional

from _pdf_common import (
    SkillArgumentParser,
    input_pdf,
    run_cli,
    selected_page_window,
)

DEFAULT_MAX_PAGES = 8
DEFAULT_MAX_CHARS = 24000


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="按页分段提取 PDF 文本")
    parser.add_argument("--input", required=True, help="本地 PDF 路径")
    parser.add_argument("--start-page", type=int, default=1, help="起始页，1-based")
    parser.add_argument("--end-page", type=int, help="结束页，1-based，默认到末页")
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="起始页文本字符偏移量，用于继续读取被截断的单页",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"单次最多处理页数，默认 {DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"单次最多返回文本字符数，默认 {DEFAULT_MAX_CHARS}",
    )
    parser.add_argument(
        "--layout",
        action="store_true",
        help="尽量保留版面空格；普通总结通常不要启用",
    )
    args = parser.parse_args(argv)
    if args.start_offset < 0:
        raise ValueError("start-offset 不能小于 0")
    if args.max_chars < 1:
        raise ValueError("max-chars 必须大于 0")
    return args


def _extract(args) -> dict[str, Any]:
    import pdfplumber

    path = input_pdf(args.input)
    pages_output: list[dict[str, Any]] = []
    used_chars = 0
    next_page: Optional[int] = None
    next_offset = 0

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        start_page, actual_end, window_next = selected_page_window(
            page_count,
            args.start_page,
            args.end_page,
            args.max_pages,
        )

        for page_number in range(start_page, actual_end + 1):
            page = pdf.pages[page_number - 1]
            text = page.extract_text(layout=args.layout) or ""
            text = text.replace("\x00", "")
            offset = args.start_offset if page_number == start_page else 0
            if offset > len(text):
                raise ValueError(
                    f"start-offset 超过第 {page_number} 页文本长度 {len(text)}"
                )
            remaining = text[offset:]
            budget = args.max_chars - used_chars

            if budget <= 0:
                next_page = page_number
                next_offset = offset
                break
            if len(remaining) > budget:
                if pages_output and budget < min(500, len(remaining)):
                    next_page = page_number
                    next_offset = offset
                    break
                excerpt = remaining[:budget]
                pages_output.append(
                    {
                        "page": page_number,
                        "text": excerpt,
                        "char_count": len(text),
                        "offset_start": offset,
                        "offset_end": offset + len(excerpt),
                        "complete": False,
                    }
                )
                used_chars += len(excerpt)
                next_page = page_number
                next_offset = offset + len(excerpt)
                break

            pages_output.append(
                {
                    "page": page_number,
                    "text": remaining,
                    "char_count": len(text),
                    "offset_start": offset,
                    "offset_end": len(text),
                    "complete": True,
                }
            )
            used_chars += len(remaining)

        if next_page is None:
            next_page = window_next

    return {
        "path": str(path),
        "page_count": page_count,
        "start_page": start_page,
        "end_page": pages_output[-1]["page"] if pages_output else None,
        "window_end_page": actual_end,
        "returned_chars": used_chars,
        "pages": pages_output,
        "has_more": next_page is not None,
        "next_page": next_page,
        "next_offset": next_offset,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _extract(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
