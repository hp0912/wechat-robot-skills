#!/usr/bin/env python3

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Any, Optional

from _pptx_common import (
    OOXML_PRESENTATION_SUFFIXES,
    SkillArgumentParser,
    input_file,
    inspect_archive,
    parse_xml_bytes,
    run_cli,
)


EMU_PER_INCH = 914400


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="分段检查演示文稿的结构、文本和媒体。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--start-slide", type=int, default=1)
    parser.add_argument("--max-slides", type=int, default=30)
    parser.add_argument("--max-shapes", type=int, default=200)
    parser.add_argument("--max-table-cells", type=int, default=500)
    parser.add_argument("--max-chars", type=int, default=100000)
    parser.add_argument("--include-runs", action="store_true")
    return parser


def _inches(value: Any) -> Optional[float]:
    try:
        return round(int(value) / EMU_PER_INCH, 4)
    except (TypeError, ValueError):
        return None


def _iter_shapes(shapes: Any):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _shape_text_char_count(shape: Any) -> int:
    texts: list[str] = []
    if getattr(shape, "has_text_frame", False):
        texts.append(shape.text_frame.text)
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            texts.extend(cell.text for cell in row.cells)
    return sum(
        1
        for text in texts
        for character in text
        if character.isalnum()
    )


def _contains_picture(shape: Any) -> bool:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        return True
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        return any(_contains_picture(child) for child in shape.shapes)
    return False


def _slide_media_profile(
    slide: Any,
    slide_width: int,
    slide_height: int,
) -> dict[str, Any]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    shapes = list(_iter_shapes(slide.shapes))
    image_count = sum(
        1 for shape in shapes
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
    )
    chart_count = sum(
        1 for shape in shapes
        if getattr(shape, "has_chart", False)
    )
    native_text_char_count = sum(
        _shape_text_char_count(shape) for shape in shapes
    )
    image_area = 0.0
    slide_area = slide_width * slide_height
    if slide_area > 0:
        for shape in slide.shapes:
            if not _contains_picture(shape):
                continue
            try:
                raw_left = int(shape.left)
                raw_top = int(shape.top)
                left = max(0, raw_left)
                top = max(0, raw_top)
                right = min(slide_width, raw_left + int(shape.width))
                bottom = min(slide_height, raw_top + int(shape.height))
            except (AttributeError, TypeError, ValueError):
                continue
            if right > left and bottom > top:
                image_area += (right - left) * (bottom - top) / slide_area
    return {
        "image_count": image_count,
        "chart_count": chart_count,
        "image_area_ratio": round(min(1.0, image_area), 4),
        "native_text_char_count": native_text_char_count,
        "has_images": image_count > 0,
    }


def _run_payload(run: Any) -> dict[str, Any]:
    font = run.font
    hyperlink = None
    try:
        hyperlink = run.hyperlink.address
    except (AttributeError, KeyError, ValueError):
        pass
    return {
        "text": run.text,
        "bold": font.bold,
        "italic": font.italic,
        "underline": font.underline,
        "font_name": font.name,
        "font_size_pt": round(font.size.pt, 2) if font.size is not None else None,
        "hyperlink": hyperlink,
    }


def _paragraph_payload(paragraph: Any, *, include_runs: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": paragraph.text,
        "level": paragraph.level,
        "alignment": str(paragraph.alignment) if paragraph.alignment else None,
    }
    if include_runs:
        payload["runs"] = [_run_payload(run) for run in paragraph.runs]
    return payload


def _text_frame_payload(text_frame: Any, *, include_runs: bool) -> dict[str, Any]:
    return {
        "text": text_frame.text,
        "paragraphs": [
            _paragraph_payload(paragraph, include_runs=include_runs)
            for paragraph in text_frame.paragraphs
        ],
    }


def _chart_payload(shape: Any, *, max_points: int = 100) -> dict[str, Any]:
    chart = shape.chart
    series_payload: list[dict[str, Any]] = []
    for series in list(chart.series)[:50]:
        values: list[Any] = []
        try:
            values = list(series.values)[:max_points]
        except (AttributeError, TypeError, ValueError):
            pass
        series_payload.append(
            {
                "name": getattr(series, "name", None),
                "point_count": len(values),
                "values": values,
            }
        )
    categories: list[str] = []
    try:
        categories = [str(item.label) for item in chart.plots[0].categories][:max_points]
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return {
        "chart_type": str(chart.chart_type),
        "has_title": chart.has_title,
        "series": series_payload,
        "categories": categories,
    }


def _shape_payload(
    shape: Any,
    *,
    include_runs: bool,
    max_table_cells: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": shape.name,
        "shape_type": str(shape.shape_type),
        "x": _inches(shape.left),
        "y": _inches(shape.top),
        "w": _inches(shape.width),
        "h": _inches(shape.height),
    }
    if getattr(shape, "has_text_frame", False):
        payload["text_frame"] = _text_frame_payload(
            shape.text_frame,
            include_runs=include_runs,
        )
    if getattr(shape, "has_table", False):
        rows: list[list[str]] = []
        cell_count = 0
        truncated = False
        for row in shape.table.rows:
            values: list[str] = []
            for cell in row.cells:
                if cell_count >= max_table_cells:
                    truncated = True
                    break
                values.append(cell.text)
                cell_count += 1
            if values:
                rows.append(values)
            if truncated:
                break
        payload["table"] = {
            "row_count": len(shape.table.rows),
            "column_count": len(shape.table.columns),
            "rows": rows,
            "truncated": truncated,
        }
    if getattr(shape, "has_chart", False):
        payload["chart"] = _chart_payload(shape)
    if shape.shape_type == 13:
        try:
            payload["image"] = {
                "content_type": shape.image.content_type,
                "filename": shape.image.filename,
                "size_bytes": len(shape.image.blob),
            }
        except (AttributeError, KeyError, ValueError):
            payload["image"] = {"readable": False}
    return payload


def _slide_notes(slide: Any) -> str:
    try:
        text_frame = slide.notes_slide.notes_text_frame
        return text_frame.text if text_frame is not None else ""
    except (AttributeError, KeyError, ValueError):
        return ""


def _comment_summary(path: Path) -> dict[str, Any]:
    comment_parts: list[str] = []
    authors: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith("ppt/comments/comment") and name.endswith(".xml"):
                comment_parts.append(name)
            if name in {
                "ppt/commentAuthors.xml",
                "ppt/authors.xml",
            }:
                root = parse_xml_bytes(archive.read(name), label=name)
                for node in root.iter():
                    author = node.attrib.get("name")
                    if author:
                        authors.add(author)
    return {
        "part_count": len(comment_parts),
        "authors": sorted(authors),
    }


def _external_relationships(path: Path) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".rels"):
                continue
            root = parse_xml_bytes(archive.read(name), label=name)
            for node in root:
                if node.attrib.get("TargetMode") != "External":
                    continue
                relationships.append(
                    {
                        "part": name,
                        "type": node.attrib.get("Type", ""),
                        "target": node.attrib.get("Target", ""),
                    }
                )
    return relationships[:200]


def main() -> dict[str, Any]:
    from pptx import Presentation

    args = build_parser().parse_args()
    if args.start_slide < 1:
        raise ValueError("start-slide 必须大于 0")
    if args.max_slides < 1 or args.max_slides > 100:
        raise ValueError("max-slides 必须在 1 到 100 之间")
    if args.max_shapes < 1 or args.max_shapes > 1000:
        raise ValueError("max-shapes 必须在 1 到 1000 之间")
    if args.max_table_cells < 1 or args.max_table_cells > 10000:
        raise ValueError("max-table-cells 必须在 1 到 10000 之间")
    if args.max_chars < 1000 or args.max_chars > 1_000_000:
        raise ValueError("max-chars 必须在 1000 到 1000000 之间")

    source = input_file(args.input, OOXML_PRESENTATION_SUFFIXES)
    archive = inspect_archive(source)
    if archive["missing_required_parts"]:
        raise ValueError(
            "演示文稿缺少必要部件："
            + "、".join(archive["missing_required_parts"])
        )
    presentation = Presentation(str(source))
    slide_count = len(presentation.slides)
    if args.start_slide > slide_count and slide_count > 0:
        raise ValueError(f"start-slide 超出页面总数 {slide_count}")

    end_slide = min(slide_count, args.start_slide + args.max_slides - 1)
    payload_slides: list[dict[str, Any]] = []
    char_count = 0
    truncated_by_chars = False
    for slide_number in range(args.start_slide, end_slide + 1):
        slide = presentation.slides[slide_number - 1]
        title = slide.shapes.title.text if slide.shapes.title is not None else ""
        media_profile = _slide_media_profile(
            slide,
            int(presentation.slide_width),
            int(presentation.slide_height),
        )
        shapes: list[dict[str, Any]] = []
        for shape in list(slide.shapes)[: args.max_shapes]:
            item = _shape_payload(
                shape,
                include_runs=args.include_runs,
                max_table_cells=args.max_table_cells,
            )
            item_chars = len(str(item))
            if char_count + item_chars > args.max_chars:
                truncated_by_chars = True
                break
            shapes.append(item)
            char_count += item_chars
        notes = _slide_notes(slide)
        if char_count + len(notes) > args.max_chars:
            notes = notes[: max(0, args.max_chars - char_count)]
            truncated_by_chars = True
        char_count += len(notes)
        payload_slides.append(
            {
                "number": slide_number,
                "title": title,
                "layout": getattr(slide.slide_layout, "name", None),
                "shape_count": len(slide.shapes),
                "shapes_truncated": len(slide.shapes) > args.max_shapes,
                "shapes": shapes,
                "speaker_notes": notes,
                "media": media_profile,
            }
        )
        if truncated_by_chars:
            break

    last_slide = payload_slides[-1]["number"] if payload_slides else args.start_slide - 1
    next_slide = last_slide + 1 if last_slide < slide_count else None
    properties = presentation.core_properties
    return {
        "source": str(source),
        "slide_count": slide_count,
        "slide_size": {
            "width_inches": _inches(presentation.slide_width),
            "height_inches": _inches(presentation.slide_height),
        },
        "properties": {
            "title": properties.title,
            "subject": properties.subject,
            "author": properties.author,
            "keywords": properties.keywords,
            "comments": properties.comments,
            "last_modified_by": properties.last_modified_by,
        },
        "selection": {
            "start_slide": args.start_slide,
            "end_slide": last_slide,
            "has_more": next_slide is not None,
            "next_slide": next_slide,
            "truncated_by_chars": truncated_by_chars,
        },
        "slides": payload_slides,
        "image_slides": [
            slide["number"]
            for slide in payload_slides
            if slide["media"]["has_images"]
        ],
        "comments": _comment_summary(source),
        "external_relationships": _external_relationships(source),
        "archive": archive,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
