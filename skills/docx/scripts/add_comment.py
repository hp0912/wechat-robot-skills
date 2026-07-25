#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional

from _docx_common import (
    DOCX_INPUT_SUFFIXES,
    SkillArgumentParser,
    input_file,
    inspect_archive,
    output_file,
    publish_file,
    run_cli,
)


def _iter_table_paragraphs(table: Any) -> Iterable[Any]:
    for row in table.rows:
        for cell in row.cells:
            yield from cell.paragraphs
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested)


def _paragraphs(document: Any, scope: str) -> list[Any]:
    if scope not in {"body", "tables", "headers", "footers", "all"}:
        raise ValueError("scope 仅支持 body、tables、headers、footers、all")
    result: list[Any] = []
    if scope in {"body", "all"}:
        result.extend(document.paragraphs)
    if scope in {"tables", "all"}:
        for table in document.tables:
            result.extend(_iter_table_paragraphs(table))
    if scope in {"headers", "all"}:
        for section in document.sections:
            result.extend(section.header.paragraphs)
    if scope in {"footers", "all"}:
        for section in document.sections:
            result.extend(section.footer.paragraphs)
    deduplicated: list[Any] = []
    seen: set[int] = set()
    for paragraph in result:
        identity = id(paragraph._p)
        if identity not in seen:
            seen.add(identity)
            deduplicated.append(paragraph)
    return deduplicated


def _split_run(run: Any, offset: int) -> tuple[Optional[Any], Optional[Any]]:
    from docx.text.run import Run

    text = run.text
    if offset <= 0:
        return None, run
    if offset >= len(text):
        return run, None
    original = deepcopy(run._r)
    left_text = text[:offset]
    right_text = text[offset:]
    run.text = left_text
    original.text = right_text
    run._r.addnext(original)
    return run, Run(original, run._parent)


def _selected_runs(paragraph: Any, start: int, end: int) -> list[Any]:
    runs = paragraph.runs
    spans: list[tuple[int, int]] = []
    offset = 0
    for run in runs:
        spans.append((offset, offset + len(run.text)))
        offset += len(run.text)
    start_index = next(
        index for index, (_, run_end) in enumerate(spans) if start < run_end
    )
    end_index = next(
        index
        for index, (run_start, run_end) in enumerate(spans)
        if end <= run_end and end > run_start
    )
    start_run = runs[start_index]
    end_run = runs[end_index]
    start_offset = start - spans[start_index][0]
    end_offset = end - spans[end_index][0]

    if start_run is end_run:
        match_with_prefix, _ = _split_run(start_run, end_offset)
        if match_with_prefix is None:
            raise ValueError("无法为零长度文本添加批注")
        _, selected = _split_run(match_with_prefix, start_offset)
        if selected is None:
            raise ValueError("无法定位批注文本")
        return [selected]

    _, start_selected = _split_run(start_run, start_offset)
    end_selected, _ = _split_run(end_run, end_offset)
    if start_selected is None or end_selected is None:
        raise ValueError("无法定位跨 Run 的批注文本")
    refreshed = paragraph.runs
    start_position = next(
        index for index, run in enumerate(refreshed) if run._r is start_selected._r
    )
    end_position = next(
        index for index, run in enumerate(refreshed) if run._r is end_selected._r
    )
    return refreshed[start_position : end_position + 1]


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="在 Word 文档中的精确文本范围添加批注。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--find", required=True, help="要锚定批注的文本")
    parser.add_argument("--comment", required=True, help="批注正文")
    parser.add_argument("--author", default="AI")
    parser.add_argument("--initials", default="AI")
    parser.add_argument(
        "--scope",
        default="all",
        choices=["body", "tables", "headers", "footers", "all"],
    )
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument(
        "--occurrence",
        type=int,
        default=1,
        help="为全文第几个匹配添加批注，从 1 开始",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    if not args.find:
        raise ValueError("find 不能为空")
    if not args.comment:
        raise ValueError("comment 不能为空")
    if args.occurrence < 1 or args.occurrence > 10_000:
        raise ValueError("occurrence 必须在 1 到 10000 之间")
    source = input_file(args.input, DOCX_INPUT_SUFFIXES)
    destination = output_file(
        args.output,
        {".docx"},
        overwrite=args.overwrite,
    )
    if source == destination:
        raise ValueError("输出路径不能与输入文件相同")

    document = Document(str(source))
    flags = re.IGNORECASE if args.ignore_case else 0
    pattern = re.compile(re.escape(args.find), flags)
    seen = 0
    selected_paragraph: Optional[Any] = None
    selected_span: Optional[tuple[int, int]] = None
    for paragraph in _paragraphs(document, args.scope):
        for match in pattern.finditer(paragraph.text):
            seen += 1
            if seen == args.occurrence:
                selected_paragraph = paragraph
                selected_span = match.span()
                break
        if selected_paragraph is not None:
            break
    if selected_paragraph is None or selected_span is None:
        raise ValueError(
            f"全文只找到 {seen} 个匹配，无法选择第 {args.occurrence} 个"
        )
    runs = _selected_runs(
        selected_paragraph,
        selected_span[0],
        selected_span[1],
    )
    document.add_comment(
        runs,
        text=args.comment,
        author=args.author,
        initials=args.initials,
    )

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".docx",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        document.save(temp_path)
        archive = inspect_archive(temp_path)
        Document(str(temp_path))
        publish_file(temp_path, destination, overwrite=args.overwrite)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {
        "path": str(destination),
        "source": str(source),
        "matched_occurrence": args.occurrence,
        "anchored_text": args.find,
        "author": args.author,
        "comment_count": len(document.comments),
        "archive": archive,
        "requires_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
