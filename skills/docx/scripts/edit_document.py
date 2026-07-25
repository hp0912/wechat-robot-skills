#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from _document_builder import (
    add_blocks,
    apply_core_properties,
    apply_header_footer,
    apply_page_settings,
    expect_list,
    expect_object,
    set_update_fields,
)
from _docx_common import (
    DOCX_INPUT_SUFFIXES,
    SkillArgumentParser,
    W_NS,
    input_file,
    inspect_archive,
    load_json_argument,
    output_file,
    parse_xml_bytes,
    publish_file,
    run_cli,
)


MAX_OPERATIONS = 200
MAX_REPLACEMENTS = 10_000


def _has_revisions(source: Path) -> bool:
    revision_tags = {
        f"{{{W_NS}}}{name}"
        for name in (
            "ins",
            "del",
            "moveFrom",
            "moveTo",
            "pPrChange",
            "rPrChange",
            "tblPrChange",
            "trPrChange",
            "tcPrChange",
            "sectPrChange",
        )
    }
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = parse_xml_bytes(archive.read(name), label=name)
            if any(element.tag in revision_tags for element in root.iter()):
                return True
    return False


def _iter_table_paragraphs(table: Any) -> Iterable[Any]:
    for row in table.rows:
        for cell in row.cells:
            yield from cell.paragraphs
            for nested_table in cell.tables:
                yield from _iter_table_paragraphs(nested_table)


def _iter_scope_paragraphs(document: Any, scope: str) -> list[Any]:
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
            for table in section.header.tables:
                result.extend(_iter_table_paragraphs(table))
    if scope in {"footers", "all"}:
        for section in document.sections:
            result.extend(section.footer.paragraphs)
            for table in section.footer.tables:
                result.extend(_iter_table_paragraphs(table))
    deduplicated: list[Any] = []
    seen: set[int] = set()
    for paragraph in result:
        identity = id(paragraph._p)
        if identity not in seen:
            seen.add(identity)
            deduplicated.append(paragraph)
    return deduplicated


def _matches(
    text: str,
    find: str,
    *,
    match_case: bool,
    whole_word: bool,
) -> list[re.Match[str]]:
    pattern = re.escape(find)
    if whole_word:
        pattern = rf"(?<!\w){pattern}(?!\w)"
    flags = 0 if match_case else re.IGNORECASE
    return list(re.finditer(pattern, text, flags))


def _replace_in_paragraph(
    paragraph: Any,
    *,
    find: str,
    replacement: str,
    match_case: bool,
    whole_word: bool,
    remaining: int,
) -> int:
    runs = paragraph.runs
    if not runs:
        return 0
    texts = [run.text for run in runs]
    full_text = "".join(texts)
    found = _matches(
        full_text,
        find,
        match_case=match_case,
        whole_word=whole_word,
    )
    if remaining >= 0:
        found = found[:remaining]
    if not found:
        return 0

    spans: list[tuple[int, int]] = []
    offset = 0
    for text in texts:
        spans.append((offset, offset + len(text)))
        offset += len(text)

    for match in reversed(found):
        start, end = match.span()
        start_index = next(
            index
            for index, (_, run_end) in enumerate(spans)
            if start < run_end or (start == 0 and run_end == 0)
        )
        end_index = next(
            index
            for index, (run_start, run_end) in enumerate(spans)
            if end <= run_end and end > run_start
        )
        start_run_start, _ = spans[start_index]
        end_run_start, _ = spans[end_index]
        start_offset = start - start_run_start
        end_offset = end - end_run_start
        if start_index == end_index:
            current = runs[start_index].text
            runs[start_index].text = (
                current[:start_offset] + replacement + current[end_offset:]
            )
        else:
            start_text = runs[start_index].text
            end_text = runs[end_index].text
            runs[start_index].text = start_text[:start_offset] + replacement
            for index in range(start_index + 1, end_index):
                runs[index].text = ""
            runs[end_index].text = end_text[end_offset:]
    return len(found)


def _op_replace_text(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    find = str(op.get("find", ""))
    if not find:
        raise ValueError("replace_text.find 不能为空")
    replacement = str(op.get("replace", ""))
    limit = int(op.get("count", 0))
    if limit < 0 or limit > MAX_REPLACEMENTS:
        raise ValueError(f"replace_text.count 必须在 0 到 {MAX_REPLACEMENTS} 之间")
    scope = str(op.get("scope", "all"))
    paragraphs = _iter_scope_paragraphs(document, scope)
    total = 0
    affected: list[int] = []
    for index, paragraph in enumerate(paragraphs):
        remaining = -1 if limit == 0 else limit - total
        if remaining == 0:
            break
        count = _replace_in_paragraph(
            paragraph,
            find=find,
            replacement=replacement,
            match_case=bool(op.get("match_case", True)),
            whole_word=bool(op.get("whole_word", False)),
            remaining=remaining,
        )
        if count:
            total += count
            if len(affected) < 100:
                affected.append(index)
    if bool(op.get("required", True)) and total == 0:
        raise ValueError(f"未找到要替换的文本：{find!r}")
    return {
        "type": "replace_text",
        "replacement_count": total,
        "affected_paragraph_indexes": affected,
    }


def _op_append_blocks(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    counts = add_blocks(document, document, op.get("blocks"))
    return {"type": "append_blocks", "counts": counts}


def _find_body_paragraph(document: Any, text: str, match: str) -> Any:
    for paragraph in document.paragraphs:
        if match == "exact" and paragraph.text == text:
            return paragraph
        if match == "contains" and text in paragraph.text:
            return paragraph
    raise ValueError(f"未找到锚点段落：{text!r}")


def _op_insert_blocks_after(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    anchor_text = str(op.get("find", ""))
    if not anchor_text:
        raise ValueError("insert_blocks_after.find 不能为空")
    match = str(op.get("match", "contains"))
    if match not in {"exact", "contains"}:
        raise ValueError("insert_blocks_after.match 仅支持 exact、contains")
    anchor = _find_body_paragraph(document, anchor_text, match)
    body = document.element.body
    before_elements = set(body.iterchildren())
    counts = add_blocks(document, document, op.get("blocks"))
    new_elements = [
        child
        for child in body.iterchildren()
        if child not in before_elements and child.tag != f"{{{W_NS}}}sectPr"
    ]
    insertion_point = anchor._p
    for element in new_elements:
        body.remove(element)
        insertion_point.addnext(element)
        insertion_point = element
    return {"type": "insert_blocks_after", "counts": counts}


def _op_remove_paragraphs(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    text = str(op.get("text", ""))
    if not text:
        raise ValueError("remove_paragraphs.text 不能为空")
    match = str(op.get("match", "contains"))
    if match not in {"exact", "contains"}:
        raise ValueError("remove_paragraphs.match 仅支持 exact、contains")
    limit = int(op.get("count", 0))
    removed = 0
    for paragraph in list(document.paragraphs):
        matched = paragraph.text == text if match == "exact" else text in paragraph.text
        if not matched:
            continue
        paragraph._element.getparent().remove(paragraph._element)
        removed += 1
        if limit and removed >= limit:
            break
    if bool(op.get("required", True)) and removed == 0:
        raise ValueError(f"未找到要删除的段落：{text!r}")
    return {"type": "remove_paragraphs", "removed_count": removed}


def _op_set_paragraph_style(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    style = str(op.get("style", ""))
    if not style:
        raise ValueError("set_paragraph_style.style 不能为空")
    if style not in document.styles:
        raise ValueError(f"文档样式不存在：{style}")
    indexes = op.get("indexes")
    changed = 0
    if indexes is not None:
        for raw_index in expect_list(indexes, "set_paragraph_style.indexes"):
            index = int(raw_index)
            if index < 0 or index >= len(document.paragraphs):
                raise ValueError(f"段落索引超出范围：{index}")
            document.paragraphs[index].style = style
            changed += 1
    else:
        text = str(op.get("contains", ""))
        if not text:
            raise ValueError("需要提供 indexes 或 contains")
        for paragraph in document.paragraphs:
            if text in paragraph.text:
                paragraph.style = style
                changed += 1
    if bool(op.get("required", True)) and changed == 0:
        raise ValueError("没有段落应用到指定样式")
    return {"type": "set_paragraph_style", "changed_count": changed}


def _op_set_properties(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    apply_core_properties(document, op.get("properties"))
    return {"type": "set_properties"}


def _op_set_page(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    selection = op.get("section", "all")
    if selection == "all":
        sections = list(document.sections)
    else:
        index = int(selection)
        if index < 0 or index >= len(document.sections):
            raise ValueError(f"section 索引超出范围：{index}")
        sections = [document.sections[index]]
    for section in sections:
        apply_page_settings(section, op.get("page"))
    return {"type": "set_page", "section_count": len(sections)}


def _op_set_header_footer(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    apply_header_footer(
        document,
        op.get("header"),
        op.get("footer"),
    )
    return {"type": "set_header_footer", "section_count": len(document.sections)}


def _op_remove_tables(document: Any, op: dict[str, Any]) -> dict[str, Any]:
    indexes = sorted(
        {int(item) for item in expect_list(op.get("indexes"), "remove_tables.indexes")},
        reverse=True,
    )
    for index in indexes:
        if index < 0 or index >= len(document.tables):
            raise ValueError(f"表格索引超出范围：{index}")
        table = document.tables[index]
        table._element.getparent().remove(table._element)
    return {"type": "remove_tables", "removed_count": len(indexes)}


def _apply_operation(document: Any, raw_op: Any) -> dict[str, Any]:
    op = expect_object(raw_op, "operations[]")
    kind = str(op.get("type", ""))
    handlers = {
        "replace_text": _op_replace_text,
        "append_blocks": _op_append_blocks,
        "insert_blocks_after": _op_insert_blocks_after,
        "remove_paragraphs": _op_remove_paragraphs,
        "set_paragraph_style": _op_set_paragraph_style,
        "set_properties": _op_set_properties,
        "set_page": _op_set_page,
        "set_header_footer": _op_set_header_footer,
        "remove_tables": _op_remove_tables,
    }
    handler = handlers.get(kind)
    if handler is None:
        raise ValueError(
            f"不支持的操作 {kind!r}；可选：{'、'.join(sorted(handlers))}"
        )
    return handler(document, op)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="依据受控 JSON 操作编辑现有 Word DOCX 文档。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--spec", help="内联 JSON 编辑说明")
    parser.add_argument("--spec-file", help="JSON 编辑说明文件")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-existing-revisions",
        action="store_true",
        help="明确允许编辑含现有修订的文档",
    )
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    source = input_file(args.input, DOCX_INPUT_SUFFIXES)
    destination = output_file(
        args.output,
        {".docx"},
        overwrite=args.overwrite,
    )
    if source == destination:
        raise ValueError("输出路径不能与输入文件相同；请保留原始文档")
    spec = load_json_argument(args.spec, args.spec_file, label="编辑说明")
    unknown = set(spec) - {"operations"}
    if unknown:
        raise ValueError(f"编辑说明包含未知顶层字段：{sorted(unknown)}")
    operations = expect_list(spec.get("operations"), "operations")
    if not operations:
        raise ValueError("operations 不能为空")
    if len(operations) > MAX_OPERATIONS:
        raise ValueError(f"operations 不能超过 {MAX_OPERATIONS} 项")

    has_revisions = _has_revisions(source)
    if has_revisions and not args.allow_existing_revisions:
        raise ValueError(
            "输入文档含修订记录。为避免把未接受修订静默改坏，"
            "请先使用 accept_changes.py 生成干净副本，"
            "或在用户明确要求保留现有修订时传 --allow-existing-revisions"
        )

    document = Document(str(source))
    results: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        try:
            results.append(_apply_operation(document, operation))
        except Exception as exc:
            raise ValueError(f"第 {index + 1} 个操作失败：{exc}") from exc
    set_update_fields(document)

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
        if archive["missing_required_parts"]:
            raise ValueError(
                "编辑结果缺少必要部件："
                + "、".join(archive["missing_required_parts"])
            )
        Document(str(temp_path))
        publish_file(temp_path, destination, overwrite=args.overwrite)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "path": str(destination),
        "source": str(source),
        "operation_count": len(operations),
        "operation_results": results,
        "paragraph_count": len(document.paragraphs),
        "table_count": len(document.tables),
        "section_count": len(document.sections),
        "source_had_revisions": has_revisions,
        "archive": archive,
        "requires_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
