#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from _pptx_common import (
    OOXML_PRESENTATION_SUFFIXES,
    SkillArgumentParser,
    input_file,
    load_json_argument,
    output_file,
    parse_xml_bytes,
    publish_file,
    run_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="按受控操作编辑 PowerPoint 演示文稿。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--spec")
    parser.add_argument("--spec-file")
    parser.add_argument("--allow-external-links", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _external_relationship_count(path: Path) -> int:
    count = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".rels"):
                continue
            root = parse_xml_bytes(archive.read(name), label=name)
            count += sum(
                1
                for node in root
                if node.attrib.get("TargetMode") == "External"
            )
    return count


def _package_risks(path: Path) -> list[str]:
    risks: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(name.startswith("ppt/comments/") for name in names):
            risks.append("comments")
        if any(
            name.endswith((".bin", ".vbaProject"))
            for name in names
        ):
            risks.append("binary_embedded_objects_or_macros")
        if any(name.startswith("ppt/activeX/") for name in names):
            risks.append("activex")
    return risks


def _iter_shapes(shapes: Any) -> Iterable[Any]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _iter_text_frames(slide: Any, *, include_notes: bool) -> Iterable[Any]:
    for shape in _iter_shapes(slide.shapes):
        if getattr(shape, "has_text_frame", False):
            yield shape.text_frame
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame
    if include_notes:
        try:
            text_frame = slide.notes_slide.notes_text_frame
            if text_frame is not None:
                yield text_frame
        except (AttributeError, KeyError, ValueError):
            pass


def _find_spans(
    text: str,
    needle: str,
    *,
    match_case: bool,
    whole_word: bool,
    limit: Optional[int],
) -> list[tuple[int, int]]:
    flags = 0 if match_case else re.IGNORECASE
    escaped = re.escape(needle)
    if whole_word:
        escaped = rf"(?<!\w){escaped}(?!\w)"
    matches = list(re.finditer(escaped, text, flags))
    if limit is not None:
        matches = matches[:limit]
    return [(match.start(), match.end()) for match in matches]


def _run_at_offset(runs: list[Any], offset: int, *, end: bool = False) -> tuple[int, int]:
    cursor = 0
    for index, run in enumerate(runs):
        next_cursor = cursor + len(run.text)
        if offset < next_cursor or (end and offset == next_cursor):
            return index, offset - cursor
        cursor = next_cursor
    if not runs:
        raise ValueError("段落没有可编辑的文本 Run")
    return len(runs) - 1, len(runs[-1].text)


def _replace_in_paragraph(
    paragraph: Any,
    needle: str,
    replacement: str,
    *,
    match_case: bool,
    whole_word: bool,
    limit: Optional[int],
) -> int:
    runs = list(paragraph.runs)
    if not runs:
        return 0
    text = "".join(run.text for run in runs)
    spans = _find_spans(
        text,
        needle,
        match_case=match_case,
        whole_word=whole_word,
        limit=limit,
    )
    for start, end in reversed(spans):
        start_index, start_offset = _run_at_offset(runs, start)
        end_index, end_offset = _run_at_offset(runs, end, end=True)
        if start_index == end_index:
            original = runs[start_index].text
            runs[start_index].text = (
                original[:start_offset] + replacement + original[end_offset:]
            )
            continue
        prefix = runs[start_index].text[:start_offset]
        suffix = runs[end_index].text[end_offset:]
        runs[start_index].text = prefix + replacement
        for index in range(start_index + 1, end_index):
            runs[index].text = ""
        runs[end_index].text = suffix
    return len(spans)


def _selected_slides(presentation: Any, indexes: Optional[list[Any]]) -> list[Any]:
    if indexes is None:
        return list(presentation.slides)
    if not isinstance(indexes, list) or not indexes:
        raise ValueError("slides 必须是非空页码数组")
    selected: list[Any] = []
    for value in indexes:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("slides 中的页码必须是整数")
        if value < 1 or value > len(presentation.slides):
            raise ValueError(f"页码超出范围：{value}")
        selected.append(presentation.slides[value - 1])
    return selected


def _replace_text(presentation: Any, operation: dict[str, Any]) -> dict[str, Any]:
    needle = operation.get("find")
    replacement = operation.get("replace")
    if not isinstance(needle, str) or not needle:
        raise ValueError("replace_text.find 必须是非空字符串")
    if not isinstance(replacement, str):
        raise ValueError("replace_text.replace 必须是字符串")
    match_case = bool(operation.get("match_case", True))
    whole_word = bool(operation.get("whole_word", False))
    required = bool(operation.get("required", True))
    include_notes = bool(operation.get("include_notes", False))
    limit_value = operation.get("count")
    if limit_value is not None:
        if (
            not isinstance(limit_value, int)
            or isinstance(limit_value, bool)
            or limit_value < 1
            or limit_value > 10000
        ):
            raise ValueError("replace_text.count 必须是 1 到 10000 的整数")
    remaining = limit_value
    changed = 0
    for slide in _selected_slides(presentation, operation.get("slides")):
        for text_frame in _iter_text_frames(slide, include_notes=include_notes):
            for paragraph in text_frame.paragraphs:
                per_paragraph_limit = remaining
                replacements = _replace_in_paragraph(
                    paragraph,
                    needle,
                    replacement,
                    match_case=match_case,
                    whole_word=whole_word,
                    limit=per_paragraph_limit,
                )
                changed += replacements
                if remaining is not None:
                    remaining -= replacements
                    if remaining <= 0:
                        break
            if remaining is not None and remaining <= 0:
                break
        if remaining is not None and remaining <= 0:
            break
    if required and changed == 0:
        raise ValueError(f"未找到必须替换的文本：{needle}")
    return {"type": "replace_text", "replacement_count": changed}


def _set_properties(presentation: Any, operation: dict[str, Any]) -> dict[str, Any]:
    properties = operation.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("set_properties.properties 必须是对象")
    allowed = {
        "title",
        "subject",
        "author",
        "keywords",
        "comments",
        "category",
        "content_status",
        "identifier",
        "language",
        "last_modified_by",
        "revision",
        "version",
    }
    changed: list[str] = []
    for key, value in properties.items():
        if key not in allowed:
            raise ValueError(f"set_properties 不支持字段：{key}")
        if key == "revision":
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
            ):
                raise ValueError("set_properties.revision 必须是正整数")
        elif value is not None and not isinstance(value, str):
            raise ValueError(f"set_properties.{key} 必须是字符串或 null")
        setattr(presentation.core_properties, key, value)
        changed.append(key)
    return {"type": "set_properties", "fields": changed}


def _delete_slides(presentation: Any, operation: dict[str, Any]) -> dict[str, Any]:
    indexes = operation.get("slides")
    if not isinstance(indexes, list) or not indexes:
        raise ValueError("delete_slides.slides 必须是非空页码数组")
    normalized: set[int] = set()
    for value in indexes:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("delete_slides.slides 中的页码必须是整数")
        if value < 1 or value > len(presentation.slides):
            raise ValueError(f"要删除的页码超出范围：{value}")
        normalized.add(value)
    if len(normalized) >= len(presentation.slides):
        raise ValueError("不能删除演示文稿中的全部页面")
    slide_ids = presentation.slides._sldIdLst
    for index in sorted(normalized, reverse=True):
        slide_id = slide_ids[index - 1]
        relationship_id = slide_id.rId
        slide_ids.remove(slide_id)
        presentation.part.drop_rel(relationship_id)
    return {"type": "delete_slides", "deleted": sorted(normalized)}


def _reorder_slides(presentation: Any, operation: dict[str, Any]) -> dict[str, Any]:
    order = operation.get("order")
    expected = list(range(1, len(presentation.slides) + 1))
    if (
        not isinstance(order, list)
        or any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in order
        )
        or sorted(order) != expected
    ):
        raise ValueError(
            "reorder_slides.order 必须完整且不重复地列出当前全部页码"
        )
    slide_ids = presentation.slides._sldIdLst
    original = list(slide_ids)
    for slide_id in original:
        slide_ids.remove(slide_id)
    for number in order:
        slide_ids.append(original[number - 1])
    return {"type": "reorder_slides", "order": order}


def _apply_operation(presentation: Any, operation: dict[str, Any]) -> dict[str, Any]:
    operation_type = operation.get("type")
    if operation_type == "replace_text":
        return _replace_text(presentation, operation)
    if operation_type == "set_properties":
        return _set_properties(presentation, operation)
    if operation_type == "delete_slides":
        return _delete_slides(presentation, operation)
    if operation_type == "reorder_slides":
        return _reorder_slides(presentation, operation)
    raise ValueError(f"不支持的编辑操作：{operation_type}")


def main() -> dict[str, Any]:
    from pptx import Presentation

    args = build_parser().parse_args()
    source = input_file(args.input, OOXML_PRESENTATION_SUFFIXES)
    destination = output_file(args.output, {".pptx"}, overwrite=args.overwrite)
    spec = load_json_argument(args.spec, args.spec_file, label="编辑说明")
    operations = spec.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("编辑说明的 operations 必须是非空数组")
    if len(operations) > 1000:
        raise ValueError("编辑操作不能超过 1000 项")

    external_relationships = _external_relationship_count(source)
    if external_relationships and not args.allow_external_links:
        raise ValueError(
            "输入演示文稿包含外部链接；如用户接受外部链接可能变化的风险，"
            "请显式传 --allow-external-links"
        )
    risks = _package_risks(source)
    if risks:
        raise ValueError(
            "输入演示文稿包含 python-pptx 不能可靠保留的内容："
            + "、".join(risks)
            + "；请改用 unpack_presentation.py 做最小化 OOXML 编辑"
        )

    presentation = Presentation(str(source))
    results: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"operations[{index}] 必须是对象")
        results.append(_apply_operation(presentation, operation))

    with tempfile.TemporaryDirectory(prefix="pptx-edit-") as temp_name:
        staged = Path(temp_name) / "edited.pptx"
        presentation.save(str(staged))
        reopened = Presentation(str(staged))
        slide_count = len(reopened.slides)
        publish_file(staged, destination, overwrite=args.overwrite)
    return {
        "source": str(source),
        "path": str(destination),
        "slide_count": slide_count,
        "external_relationship_count": external_relationships,
        "operations": results,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
