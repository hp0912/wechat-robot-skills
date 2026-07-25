#!/usr/bin/env python3

from __future__ import annotations

import sys
from typing import Any

from _pdf_common import (
    SkillArgumentParser,
    input_pdf,
    run_cli,
    selected_page_window,
)

DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_TABLES = 20
DEFAULT_MAX_CELLS = 2000


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="按页提取 PDF 表格")
    parser.add_argument("--input", required=True, help="本地 PDF 路径")
    parser.add_argument("--start-page", type=int, default=1, help="起始页，1-based")
    parser.add_argument("--end-page", type=int, help="结束页，1-based，默认到末页")
    parser.add_argument(
        "--start-table",
        type=int,
        default=0,
        help="起始页内从第几个表格继续，0-based",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"单次最多处理页数，默认 {DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "--max-tables",
        type=int,
        default=DEFAULT_MAX_TABLES,
        help=f"单次最多返回表格数，默认 {DEFAULT_MAX_TABLES}",
    )
    parser.add_argument(
        "--max-cells",
        type=int,
        default=DEFAULT_MAX_CELLS,
        help=f"单次最多返回单元格数，默认 {DEFAULT_MAX_CELLS}",
    )
    args = parser.parse_args(argv)
    if args.start_table < 0:
        raise ValueError("start-table 不能小于 0")
    if args.max_tables < 1:
        raise ValueError("max-tables 必须大于 0")
    if args.max_cells < 1:
        raise ValueError("max-cells 必须大于 0")
    return args


def _clean_table(table) -> list[list[str]]:
    return [
        ["" if cell is None else str(cell).replace("\x00", "") for cell in row]
        for row in table
    ]


def _extract(args) -> dict[str, Any]:
    import pdfplumber

    path = input_pdf(args.input)
    result_pages: list[dict[str, Any]] = []
    table_count = 0
    cell_count = 0
    truncated = False
    next_table = 0

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        start_page, actual_end, next_page = selected_page_window(
            page_count,
            args.start_page,
            args.end_page,
            args.max_pages,
        )

        for page_number in range(start_page, actual_end + 1):
            page_tables = []
            all_tables = pdf.pages[page_number - 1].extract_tables() or []
            table_offset = args.start_table if page_number == start_page else 0
            if table_offset > len(all_tables):
                raise ValueError(
                    f"start-table 超过第 {page_number} 页表格数量 {len(all_tables)}"
                )
            for table_index, table in enumerate(
                all_tables[table_offset:],
                start=table_offset,
            ):
                cleaned = _clean_table(table)
                cells = sum(len(row) for row in cleaned)
                if cells > args.max_cells and table_count == 0:
                    raise ValueError(
                        f"第 {page_number} 页第 {table_index} 个表格有 {cells} "
                        "个单元格，请提高 max-cells"
                    )
                if (
                    table_count >= args.max_tables
                    or cell_count + cells > args.max_cells
                ):
                    truncated = True
                    next_page = page_number
                    next_table = table_index
                    break
                page_tables.append(cleaned)
                table_count += 1
                cell_count += cells

            result_pages.append({"page": page_number, "tables": page_tables})
            if truncated:
                break

    return {
        "path": str(path),
        "page_count": page_count,
        "start_page": start_page,
        "end_page": result_pages[-1]["page"],
        "window_end_page": actual_end,
        "table_count": table_count,
        "cell_count": cell_count,
        "truncated": truncated,
        "pages": result_pages,
        "has_more": next_page is not None,
        "next_page": next_page,
        "next_table": next_table,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _extract(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
