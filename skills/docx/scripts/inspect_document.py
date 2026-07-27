#!/usr/bin/env python3

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Any, Optional

from _docx_common import (
    DOCX_INPUT_SUFFIXES,
    NS,
    SkillArgumentParser,
    W_NS,
    input_file,
    inspect_archive,
    parse_xml_bytes,
    run_cli,
)


def _length_inches(value: Any) -> Optional[float]:
    if value is None:
        return None
    return round(float(value.inches), 4)


def _paragraph_payload(
    paragraph: Any,
    index: int,
    include_runs: bool,
    accepted_text: Optional[str] = None,
) -> dict[str, Any]:
    style = paragraph.style
    visible_text = paragraph.text if accepted_text is None else accepted_text
    payload: dict[str, Any] = {
        "index": index,
        "text": visible_text,
        "style": style.name if style else None,
        "alignment": (
            str(paragraph.alignment) if paragraph.alignment is not None else None
        ),
    }
    if visible_text != paragraph.text:
        payload["python_docx_text"] = paragraph.text
    if include_runs:
        payload["runs"] = [
            {
                "text": run.text,
                "bold": run.bold,
                "italic": run.italic,
                "underline": run.underline,
                "font": run.font.name,
                "size_pt": run.font.size.pt if run.font.size else None,
                "style": run.style.name if run.style else None,
            }
            for run in paragraph.runs[:200]
        ]
        payload["runs_truncated"] = max(0, len(paragraph.runs) - 200)
    return payload


def _accepted_body_paragraphs(source: Path) -> list[str]:
    def rendered_text(element: Any, deleted: bool = False) -> str:
        if element.tag in {
            f"{{{W_NS}}}del",
            f"{{{W_NS}}}moveFrom",
        }:
            deleted = True
        if deleted:
            return ""
        if element.tag == f"{{{W_NS}}}t":
            return element.text or ""
        if element.tag == f"{{{W_NS}}}tab":
            return "\t"
        if element.tag in {
            f"{{{W_NS}}}br",
            f"{{{W_NS}}}cr",
        }:
            return "\n"
        return "".join(rendered_text(child, deleted) for child in list(element))

    with zipfile.ZipFile(source) as archive:
        root = parse_xml_bytes(
            archive.read("word/document.xml"),
            label="word/document.xml",
        )
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        return []
    return [
        rendered_text(paragraph)
        for paragraph in list(body)
        if paragraph.tag == f"{{{W_NS}}}p"
    ]


def _table_payload(table: Any, index: int, max_cells: int) -> tuple[dict[str, Any], int]:
    rows: list[list[str]] = []
    used = 0
    truncated = False
    for row in table.rows:
        row_values: list[str] = []
        for cell in row.cells:
            if used >= max_cells:
                truncated = True
                break
            row_values.append(cell.text)
            used += 1
        rows.append(row_values)
        if truncated:
            break
    return (
        {
            "index": index,
            "style": table.style.name if table.style else None,
            "row_count": len(table.rows),
            "column_count": len(table.columns),
            "rows": rows,
            "truncated": truncated,
        },
        used,
    )


def _revision_summary(source: Path) -> dict[str, Any]:
    counts = {
        "insertions": 0,
        "deletions": 0,
        "moves_from": 0,
        "moves_to": 0,
        "property_changes": 0,
    }
    authors: set[str] = set()
    property_change_tags = {
        f"{{{W_NS}}}{name}"
        for name in (
            "pPrChange",
            "rPrChange",
            "tblPrChange",
            "tblGridChange",
            "trPrChange",
            "tcPrChange",
            "sectPrChange",
            "numberingChange",
        )
    }
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = parse_xml_bytes(archive.read(name), label=name)
            for element in root.iter():
                is_revision = False
                if element.tag == f"{{{W_NS}}}ins":
                    counts["insertions"] += 1
                    is_revision = True
                elif element.tag == f"{{{W_NS}}}del":
                    counts["deletions"] += 1
                    is_revision = True
                elif element.tag == f"{{{W_NS}}}moveFrom":
                    counts["moves_from"] += 1
                    is_revision = True
                elif element.tag == f"{{{W_NS}}}moveTo":
                    counts["moves_to"] += 1
                    is_revision = True
                elif element.tag in property_change_tags:
                    counts["property_changes"] += 1
                    is_revision = True
                if is_revision:
                    author = element.attrib.get(f"{{{W_NS}}}author")
                    if author:
                        authors.add(author)
    return {
        **counts,
        "total": sum(counts.values()),
        "authors": sorted(authors),
    }


def _comments(document: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    comments = getattr(document, "comments", None)
    if comments is None:
        return result
    for comment in comments:
        result.append(
            {
                "comment_id": comment.comment_id,
                "author": comment.author,
                "initials": comment.initials,
                "text": comment.text,
                "timestamp": comment.timestamp,
            }
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="分段读取 Word 文档的正文、表格、样式、批注和修订信息。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--start-paragraph", type=int, default=0)
    parser.add_argument("--max-paragraphs", type=int, default=80)
    parser.add_argument("--start-table", type=int, default=0)
    parser.add_argument("--max-tables", type=int, default=10)
    parser.add_argument("--max-table-cells", type=int, default=1000)
    parser.add_argument("--max-chars", type=int, default=40_000)
    parser.add_argument("--include-runs", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    if args.start_paragraph < 0 or args.start_table < 0:
        raise ValueError("start-paragraph 和 start-table 不能为负数")
    if args.max_paragraphs < 1 or args.max_paragraphs > 300:
        raise ValueError("max-paragraphs 必须在 1 到 300 之间")
    if args.max_tables < 0 or args.max_tables > 50:
        raise ValueError("max-tables 必须在 0 到 50 之间")
    if args.max_table_cells < 1 or args.max_table_cells > 10_000:
        raise ValueError("max-table-cells 必须在 1 到 10000 之间")
    if args.max_chars < 1000 or args.max_chars > 200_000:
        raise ValueError("max-chars 必须在 1000 到 200000 之间")

    source = input_file(args.input, DOCX_INPUT_SUFFIXES)
    archive = inspect_archive(source)
    if archive["missing_required_parts"]:
        raise ValueError(
            "文档缺少必要部件：" + "、".join(archive["missing_required_parts"])
        )
    document = Document(str(source))
    all_paragraphs = document.paragraphs
    accepted_paragraphs = _accepted_body_paragraphs(source)
    paragraphs: list[dict[str, Any]] = []
    char_count = 0
    next_paragraph: Optional[int] = None
    for index in range(args.start_paragraph, len(all_paragraphs)):
        if len(paragraphs) >= args.max_paragraphs:
            next_paragraph = index
            break
        payload = _paragraph_payload(
            all_paragraphs[index],
            index,
            args.include_runs,
            (
                accepted_paragraphs[index]
                if index < len(accepted_paragraphs)
                else None
            ),
        )
        payload_chars = len(payload["text"])
        if paragraphs and char_count + payload_chars > args.max_chars:
            next_paragraph = index
            break
        paragraphs.append(payload)
        char_count += payload_chars

    tables: list[dict[str, Any]] = []
    cells_remaining = args.max_table_cells
    next_table: Optional[int] = None
    table_end = min(len(document.tables), args.start_table + args.max_tables)
    for index in range(args.start_table, table_end):
        if cells_remaining <= 0:
            next_table = index
            break
        payload, used = _table_payload(
            document.tables[index],
            index,
            cells_remaining,
        )
        tables.append(payload)
        cells_remaining -= used
        if payload["truncated"]:
            next_table = index
            break
    if next_table is None and table_end < len(document.tables):
        next_table = table_end

    sections = []
    for index, section in enumerate(document.sections):
        sections.append(
            {
                "index": index,
                "orientation": str(section.orientation),
                "page_width_inches": _length_inches(section.page_width),
                "page_height_inches": _length_inches(section.page_height),
                "margins_inches": {
                    "top": _length_inches(section.top_margin),
                    "bottom": _length_inches(section.bottom_margin),
                    "left": _length_inches(section.left_margin),
                    "right": _length_inches(section.right_margin),
                    "header": _length_inches(section.header_distance),
                    "footer": _length_inches(section.footer_distance),
                },
                "header_text": "\n".join(
                    paragraph.text for paragraph in section.header.paragraphs
                ),
                "footer_text": "\n".join(
                    paragraph.text for paragraph in section.footer.paragraphs
                ),
            }
        )

    properties = document.core_properties
    revisions = _revision_summary(source)
    comments = _comments(document)
    total_text = "\n".join(
        accepted_paragraphs[index]
        if index < len(accepted_paragraphs)
        else paragraph.text
        for index, paragraph in enumerate(all_paragraphs)
    )
    return {
        "path": str(source),
        "format": source.suffix.lower(),
        "archive": archive,
        "properties": {
            "title": properties.title,
            "subject": properties.subject,
            "author": properties.author,
            "last_modified_by": properties.last_modified_by,
            "category": properties.category,
            "keywords": properties.keywords,
            "comments": properties.comments,
            "created": properties.created,
            "modified": properties.modified,
            "language": properties.language,
        },
        "paragraph_count": len(all_paragraphs),
        "table_count": len(document.tables),
        "section_count": len(document.sections),
        "inline_image_count": len(document.inline_shapes),
        "media_part_count": archive["media_count"],
        "has_images": archive["media_count"] > 0,
        "character_count": len(total_text),
        "word_count_estimate": len(total_text.split()),
        "sections": sections,
        "comments": comments,
        "tracked_changes": revisions,
        "paragraphs": paragraphs,
        "tables": tables,
        "has_more": next_paragraph is not None or next_table is not None,
        "next_paragraph": next_paragraph,
        "next_table": next_table,
        "needs_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
