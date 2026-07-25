#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional

from _docx_common import W_NS, input_file, qn


MAX_BLOCKS = 1_000
MAX_TABLE_CELLS = 20_000
MAX_IMAGES = 100
ALIGNMENTS = {
    "left": 0,
    "center": 1,
    "right": 2,
    "justify": 3,
    "distribute": 4,
}
VERTICAL_ALIGNMENTS = {
    "top": 0,
    "center": 1,
    "bottom": 3,
}
HEX_COLOR_LENGTHS = {6, 8}


def expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    return value


def expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是数组")
    return value


def color(value: Any, label: str) -> str:
    text = str(value).strip().lstrip("#")
    if len(text) not in HEX_COLOR_LENGTHS:
        raise ValueError(f"{label} 必须是 6 位或 8 位十六进制颜色")
    try:
        int(text, 16)
    except ValueError as exc:
        raise ValueError(f"{label} 必须是十六进制颜色") from exc
    return text.upper()[-6:]


def set_run_font_name(run: Any, name: str, east_asia: Optional[str] = None) -> None:
    run.font.name = name
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("ascii"), name)
    fonts.set(qn("hAnsi"), name)
    fonts.set(qn("eastAsia"), east_asia or name)


def apply_run_style(run: Any, raw_spec: Any, defaults: Optional[dict[str, Any]] = None) -> None:
    from docx.enum.text import WD_UNDERLINE
    from docx.shared import Pt, RGBColor

    spec = {**(defaults or {}), **expect_object(raw_spec, "run")}
    allowed = {
        "text",
        "bold",
        "italic",
        "underline",
        "strike",
        "color",
        "highlight",
        "font",
        "east_asia_font",
        "size",
        "superscript",
        "subscript",
        "small_caps",
        "all_caps",
        "style",
        "hyperlink",
    }
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(f"run 包含未知字段：{sorted(unknown)}")
    if "bold" in spec:
        run.bold = bool(spec["bold"])
    if "italic" in spec:
        run.italic = bool(spec["italic"])
    if "underline" in spec:
        underline = spec["underline"]
        if underline in {True, "single"}:
            run.underline = True
        elif underline in {False, None}:
            run.underline = False
        elif underline == "double":
            run.underline = WD_UNDERLINE.DOUBLE
        else:
            raise ValueError("underline 仅支持 true、false、single、double")
    if "strike" in spec:
        run.font.strike = bool(spec["strike"])
    if "color" in spec:
        run.font.color.rgb = RGBColor.from_string(color(spec["color"], "run.color"))
    if "font" in spec or "east_asia_font" in spec:
        font_name = str(spec.get("font", "Arial"))
        set_run_font_name(run, font_name, str(spec.get("east_asia_font", font_name)))
    if "size" in spec:
        size = float(spec["size"])
        if size < 1 or size > 200:
            raise ValueError("run.size 必须在 1 到 200 之间")
        run.font.size = Pt(size)
    for key in ("superscript", "subscript", "small_caps", "all_caps"):
        if key in spec:
            setattr(run.font, key, bool(spec[key]))
    if "style" in spec:
        run.style = str(spec["style"])


def _add_hyperlink(
    paragraph: Any,
    text: str,
    url: str,
    raw_spec: dict[str, Any],
) -> Any:
    from docx.oxml import OxmlElement
    from docx.opc.constants import RELATIONSHIP_TYPE

    relationship_id = paragraph.part.relate_to(
        url,
        RELATIONSHIP_TYPE.HYPERLINK,
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(f"{{http://schemas.openxmlformats.org/officeDocument/2006/relationships}}id", relationship_id)
    run = paragraph.add_run(text)
    apply_run_style(run, raw_spec)
    run_properties = run._element.get_or_add_rPr()
    if run_properties.find(qn("color")) is None:
        color_element = OxmlElement("w:color")
        color_element.set(qn("val"), "0563C1")
        run_properties.append(color_element)
    if run_properties.find(qn("u")) is None:
        underline = OxmlElement("w:u")
        underline.set(qn("val"), "single")
        run_properties.append(underline)
    paragraph._p.remove(run._element)
    hyperlink.append(run._element)
    paragraph._p.append(hyperlink)
    return run


def add_runs(
    paragraph: Any,
    *,
    text: Optional[str] = None,
    runs: Optional[list[Any]] = None,
    defaults: Optional[dict[str, Any]] = None,
) -> None:
    if runs is not None and text is not None:
        raise ValueError("段落不能同时提供 text 和 runs")
    if runs is None:
        run = paragraph.add_run("" if text is None else str(text))
        if defaults:
            apply_run_style(run, defaults)
        return
    for index, raw_run in enumerate(runs):
        if isinstance(raw_run, str):
            run = paragraph.add_run(raw_run)
            if defaults:
                apply_run_style(run, defaults)
            continue
        spec = expect_object(raw_run, f"runs[{index}]")
        run_text = str(spec.get("text", ""))
        if spec.get("hyperlink"):
            _add_hyperlink(
                paragraph,
                run_text,
                str(spec["hyperlink"]),
                {**(defaults or {}), **spec},
            )
        else:
            run = paragraph.add_run(run_text)
            apply_run_style(run, spec, defaults)


def apply_paragraph_format(paragraph: Any, raw_spec: Any) -> None:
    from docx.shared import Inches, Pt

    spec = expect_object(raw_spec, "paragraph")
    if "style" in spec:
        paragraph.style = str(spec["style"])
    if "alignment" in spec:
        alignment = str(spec["alignment"]).lower()
        if alignment not in ALIGNMENTS:
            raise ValueError(f"段落对齐方式无效：{alignment}")
        paragraph.alignment = ALIGNMENTS[alignment]
    paragraph_format = paragraph.paragraph_format
    points = {
        "space_before": "space_before",
        "space_after": "space_after",
        "line_spacing": "line_spacing",
    }
    for source, target in points.items():
        if source in spec:
            value = float(spec[source])
            if value < 0 or value > 500:
                raise ValueError(f"{source} 超出合理范围")
            setattr(paragraph_format, target, Pt(value))
    inches = {
        "left_indent": "left_indent",
        "right_indent": "right_indent",
        "first_line_indent": "first_line_indent",
    }
    for source, target in inches.items():
        if source in spec:
            setattr(paragraph_format, target, Inches(float(spec[source])))
    for key in (
        "keep_with_next",
        "keep_together",
        "page_break_before",
        "widow_control",
    ):
        if key in spec:
            setattr(paragraph_format, key, bool(spec[key]))


def add_paragraph_from_spec(
    container: Any,
    raw_spec: Any,
    *,
    style: Optional[str] = None,
    default_run_style: Optional[dict[str, Any]] = None,
) -> Any:
    spec = (
        {"text": raw_spec}
        if isinstance(raw_spec, str)
        else expect_object(raw_spec, "paragraph")
    )
    paragraph = container.add_paragraph()
    if style:
        paragraph.style = style
    add_runs(
        paragraph,
        text=str(spec["text"]) if "text" in spec else None,
        runs=spec.get("runs"),
        defaults=default_run_style,
    )
    apply_paragraph_format(paragraph, spec)
    return paragraph


def _set_cell_shading(cell: Any, fill: str) -> None:
    from docx.oxml import OxmlElement

    properties = cell._tc.get_or_add_tcPr()
    existing = properties.find(qn("shd"))
    if existing is not None:
        properties.remove(existing)
    shading = OxmlElement("w:shd")
    shading.set(qn("fill"), color(fill, "table.shading"))
    properties.append(shading)


def _set_cell_width(cell: Any, width_inches: float) -> None:
    from docx.oxml import OxmlElement

    cell.width = _inches(width_inches)
    properties = cell._tc.get_or_add_tcPr()
    width = properties.find(qn("tcW"))
    if width is None:
        width = OxmlElement("w:tcW")
        properties.append(width)
    width.set(qn("w"), str(round(width_inches * 1440)))
    width.set(qn("type"), "dxa")


def _inches(value: float) -> Any:
    from docx.shared import Inches

    return Inches(float(value))


def _repeat_table_row(row: Any) -> None:
    from docx.oxml import OxmlElement

    properties = row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("val"), "true")
    properties.append(repeat)


def _prevent_row_split(row: Any) -> None:
    from docx.oxml import OxmlElement

    properties = row._tr.get_or_add_trPr()
    cannot_split = OxmlElement("w:cantSplit")
    properties.append(cannot_split)


def add_table(container: Any, raw_spec: Any) -> Any:
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT

    spec = expect_object(raw_spec, "table")
    rows = expect_list(spec.get("rows"), "table.rows")
    if not rows:
        raise ValueError("table.rows 不能为空")
    normalized_rows = [expect_list(row, "table.rows[]") for row in rows]
    column_count = max((len(row) for row in normalized_rows), default=0)
    if column_count < 1:
        raise ValueError("表格必须至少有一列")
    if len(rows) * column_count > MAX_TABLE_CELLS:
        raise ValueError("单个表格超过单元格数量限制")
    table = container.add_table(rows=len(rows), cols=column_count)
    table.style = str(spec.get("style", "Table Grid"))
    table.autofit = bool(spec.get("autofit", False))
    alignment = str(spec.get("alignment", "center")).lower()
    if alignment not in {"left", "center", "right"}:
        raise ValueError("table.alignment 仅支持 left、center、right")
    table.alignment = {
        "left": WD_TABLE_ALIGNMENT.LEFT,
        "center": WD_TABLE_ALIGNMENT.CENTER,
        "right": WD_TABLE_ALIGNMENT.RIGHT,
    }[alignment]

    widths = spec.get("column_widths")
    if widths is not None:
        widths = expect_list(widths, "table.column_widths")
        if len(widths) != column_count:
            raise ValueError("table.column_widths 数量必须等于列数")
        widths = [float(item) for item in widths]

    header_rows = int(spec.get("header_rows", 1))
    if header_rows < 0 or header_rows > len(rows):
        raise ValueError("table.header_rows 超出范围")
    header_fill = str(spec.get("header_fill", "1F4E78"))
    header_font_color = str(spec.get("header_font_color", "FFFFFF"))
    vertical = str(spec.get("vertical_alignment", "center")).lower()
    if vertical not in VERTICAL_ALIGNMENTS:
        raise ValueError("table.vertical_alignment 无效")

    for row_index, (row, values) in enumerate(zip(table.rows, normalized_rows)):
        _prevent_row_split(row)
        if row_index < header_rows:
            _repeat_table_row(row)
        for column_index, cell in enumerate(row.cells):
            if widths:
                _set_cell_width(cell, widths[column_index])
            cell.vertical_alignment = {
                0: WD_CELL_VERTICAL_ALIGNMENT.TOP,
                1: WD_CELL_VERTICAL_ALIGNMENT.CENTER,
                3: WD_CELL_VERTICAL_ALIGNMENT.BOTTOM,
            }[VERTICAL_ALIGNMENTS[vertical]]
            if column_index >= len(values):
                continue
            value = values[column_index]
            paragraph = cell.paragraphs[0]
            if isinstance(value, dict):
                value_spec = expect_object(value, "table cell")
                add_runs(
                    paragraph,
                    text=str(value_spec["text"]) if "text" in value_spec else None,
                    runs=value_spec.get("runs"),
                )
                apply_paragraph_format(paragraph, value_spec)
            else:
                add_runs(paragraph, text="" if value is None else str(value))
            if row_index < header_rows:
                _set_cell_shading(cell, header_fill)
                for run in paragraph.runs:
                    run.bold = True
                    from docx.shared import RGBColor

                    run.font.color.rgb = RGBColor.from_string(
                        color(header_font_color, "header_font_color")
                    )

    for raw_merge in spec.get("merges", []):
        merge = expect_object(raw_merge, "table.merges[]")
        start = expect_list(merge.get("start"), "table.merge.start")
        end = expect_list(merge.get("end"), "table.merge.end")
        if len(start) != 2 or len(end) != 2:
            raise ValueError("table merge 坐标必须是 [row, column]")
        table.cell(int(start[0]), int(start[1])).merge(
            table.cell(int(end[0]), int(end[1]))
        )
    return table


def _add_page_number(
    paragraph: Any,
    *,
    prefix: str = "",
    suffix: str = "",
) -> None:
    from docx.oxml import OxmlElement

    if prefix:
        paragraph.add_run(prefix)
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("fldCharType"), "separate")
    value = OxmlElement("w:t")
    value.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("fldCharType"), "end")
    run._r.extend([begin, instruction, separate, value, end])
    if suffix:
        paragraph.add_run(suffix)


def _add_toc(container: Any, raw_spec: Any) -> Any:
    from docx.oxml import OxmlElement

    spec = expect_object(raw_spec, "toc")
    levels = str(spec.get("levels", "1-3"))
    if spec.get("title"):
        title = container.add_paragraph(str(spec["title"]))
        title.style = str(spec.get("title_style", "Heading 1"))
        title.paragraph_format.keep_with_next = True
    paragraph = container.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instruction.text = f'TOC \\o "{levels}" \\h \\z \\u'
    separate = OxmlElement("w:fldChar")
    separate.set(qn("fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "目录将在打开文档时更新"
    end = OxmlElement("w:fldChar")
    end.set(qn("fldCharType"), "end")
    run._r.extend([begin, instruction, separate, placeholder, end])
    return paragraph


def _add_horizontal_rule(container: Any, raw_spec: Any) -> Any:
    from docx.oxml import OxmlElement

    spec = expect_object(raw_spec, "horizontal_rule")
    paragraph = container.add_paragraph()
    properties = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("val"), str(spec.get("style", "single")))
    bottom.set(qn("sz"), str(int(spec.get("size", 6))))
    bottom.set(qn("space"), str(int(spec.get("space", 1))))
    bottom.set(qn("color"), color(spec.get("color", "808080"), "rule.color"))
    borders.append(bottom)
    properties.append(borders)
    return paragraph


def _add_image(container: Any, raw_spec: Any) -> Any:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    spec = expect_object(raw_spec, "image")
    image_path = input_file(
        str(spec.get("path", "")),
        {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"},
    )
    paragraph = container.add_paragraph()
    alignment = str(spec.get("alignment", "center")).lower()
    if alignment not in ALIGNMENTS:
        raise ValueError("image.alignment 无效")
    paragraph.alignment = ALIGNMENTS[alignment]
    run = paragraph.add_run()
    kwargs: dict[str, Any] = {}
    if "width_inches" in spec:
        kwargs["width"] = _inches(float(spec["width_inches"]))
    if "height_inches" in spec:
        kwargs["height"] = _inches(float(spec["height_inches"]))
    run.add_picture(str(image_path), **kwargs)
    if spec.get("caption"):
        caption = container.add_paragraph(str(spec["caption"]))
        caption.style = str(spec.get("caption_style", "Caption"))
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return paragraph


def _add_section(document: Any, raw_spec: Any) -> Any:
    from docx.enum.section import WD_SECTION

    spec = expect_object(raw_spec, "section_break")
    break_type = str(spec.get("break_type", "new_page")).lower()
    mapping = {
        "continuous": WD_SECTION.CONTINUOUS,
        "new_column": WD_SECTION.NEW_COLUMN,
        "new_page": WD_SECTION.NEW_PAGE,
        "even_page": WD_SECTION.EVEN_PAGE,
        "odd_page": WD_SECTION.ODD_PAGE,
    }
    if break_type not in mapping:
        raise ValueError(f"section_break.break_type 无效：{break_type}")
    section = document.add_section(mapping[break_type])
    apply_page_settings(section, spec)
    return section


def add_blocks(document: Any, container: Any, raw_blocks: Any) -> dict[str, int]:
    from docx.enum.text import WD_BREAK

    blocks = expect_list(raw_blocks, "blocks")
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"blocks 不能超过 {MAX_BLOCKS} 项")
    counts = {
        "paragraphs": 0,
        "tables": 0,
        "images": 0,
        "page_breaks": 0,
        "sections": 0,
    }
    for index, raw_block in enumerate(blocks):
        block = expect_object(raw_block, f"blocks[{index}]")
        kind = str(block.get("type", "paragraph"))
        if kind == "paragraph":
            add_paragraph_from_spec(container, block)
            counts["paragraphs"] += 1
        elif kind == "heading":
            level = int(block.get("level", 1))
            if level < 1 or level > 9:
                raise ValueError("heading.level 必须在 1 到 9 之间")
            paragraph = container.add_paragraph(style=f"Heading {level}")
            add_runs(
                paragraph,
                text=str(block["text"]) if "text" in block else None,
                runs=block.get("runs"),
            )
            apply_paragraph_format(paragraph, block)
            paragraph.paragraph_format.keep_with_next = True
            counts["paragraphs"] += 1
        elif kind in {"bullet_list", "numbered_list"}:
            items = expect_list(block.get("items"), f"{kind}.items")
            base_style = "List Bullet" if kind == "bullet_list" else "List Number"
            level = int(block.get("level", 0))
            style = base_style if level == 0 else f"{base_style} {min(level + 1, 3)}"
            for item in items:
                add_paragraph_from_spec(container, item, style=style)
                counts["paragraphs"] += 1
        elif kind == "table":
            add_table(container, block)
            counts["tables"] += 1
        elif kind == "image":
            if counts["images"] >= MAX_IMAGES:
                raise ValueError(f"图片数量不能超过 {MAX_IMAGES}")
            _add_image(container, block)
            counts["images"] += 1
            counts["paragraphs"] += 1
        elif kind == "page_break":
            paragraph = container.add_paragraph()
            paragraph.add_run().add_break(WD_BREAK.PAGE)
            counts["page_breaks"] += 1
            counts["paragraphs"] += 1
        elif kind == "horizontal_rule":
            _add_horizontal_rule(container, block)
            counts["paragraphs"] += 1
        elif kind == "toc":
            _add_toc(container, block)
            counts["paragraphs"] += 1
        elif kind == "section_break":
            if container is not document:
                raise ValueError("页眉或页脚中不能添加 section_break")
            _add_section(document, block)
            counts["sections"] += 1
        elif kind == "spacer":
            paragraph = container.add_paragraph()
            paragraph.paragraph_format.space_after = _points(
                float(block.get("points", 6))
            )
            counts["paragraphs"] += 1
        else:
            raise ValueError(f"不支持的 block.type：{kind}")
    return counts


def _points(value: float) -> Any:
    from docx.shared import Pt

    return Pt(value)


def apply_page_settings(section: Any, raw_spec: Any) -> None:
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm, Inches, Mm

    spec = expect_object(raw_spec, "page")
    size = str(spec.get("size", "A4")).upper()
    if size == "A4":
        width, height = Mm(210), Mm(297)
    elif size == "LETTER":
        width, height = Inches(8.5), Inches(11)
    elif size == "LEGAL":
        width, height = Inches(8.5), Inches(14)
    else:
        if "width_inches" not in spec or "height_inches" not in spec:
            raise ValueError("自定义纸张必须提供 width_inches 和 height_inches")
        width = Inches(float(spec["width_inches"]))
        height = Inches(float(spec["height_inches"]))
    orientation = str(spec.get("orientation", "portrait")).lower()
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("page.orientation 只能是 portrait 或 landscape")
    if orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = height, width
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = width, height

    margin_defaults = {
        "top": 0.85,
        "bottom": 0.85,
        "left": 0.9,
        "right": 0.9,
        "header": 0.35,
        "footer": 0.35,
    }
    margins = expect_object(spec.get("margins", {}), "page.margins")
    for key, default in margin_defaults.items():
        value = float(margins.get(key, default))
        if value < 0 or value > 5:
            raise ValueError(f"page.margins.{key} 超出合理范围")
        target = f"{key}_margin" if key in {"top", "bottom", "left", "right"} else f"{key}_distance"
        setattr(section, target, Inches(value))
    if "gutter" in margins:
        section.gutter = Inches(float(margins["gutter"]))


def apply_document_defaults(document: Any, raw_spec: Any) -> None:
    from docx.shared import Pt, RGBColor

    spec = expect_object(raw_spec, "default_font")
    name = str(spec.get("name", "Arial"))
    east_asia = str(spec.get("east_asia", "Noto Sans CJK SC"))
    size = float(spec.get("size", 10.5))
    normal = document.styles["Normal"]
    normal.font.name = name
    normal.font.size = Pt(size)
    normal_fonts = normal._element.get_or_add_rPr().get_or_add_rFonts()
    normal_fonts.set(qn("ascii"), name)
    normal_fonts.set(qn("hAnsi"), name)
    normal_fonts.set(qn("eastAsia"), east_asia)
    normal.paragraph_format.space_after = Pt(float(spec.get("space_after", 6)))
    normal.paragraph_format.line_spacing = float(spec.get("line_spacing", 1.15))
    if "color" in spec:
        normal.font.color.rgb = RGBColor.from_string(
            color(spec["color"], "default_font.color")
        )


def apply_named_styles(document: Any, raw_spec: Any) -> None:
    from docx.shared import Pt, RGBColor

    styles = expect_object(raw_spec, "styles")
    for style_name, raw_style in styles.items():
        if style_name not in document.styles:
            raise ValueError(f"文档样式不存在：{style_name}")
        spec = expect_object(raw_style, f"styles.{style_name}")
        style = document.styles[style_name]
        if "font" in spec:
            font_name = str(spec["font"])
            style.font.name = font_name
            fonts = style._element.get_or_add_rPr().get_or_add_rFonts()
            fonts.set(qn("ascii"), font_name)
            fonts.set(qn("hAnsi"), font_name)
        if "east_asia_font" in spec:
            style._element.get_or_add_rPr().get_or_add_rFonts().set(
                qn("eastAsia"),
                str(spec["east_asia_font"]),
            )
        if "size" in spec:
            style.font.size = Pt(float(spec["size"]))
        if "bold" in spec:
            style.font.bold = bool(spec["bold"])
        if "italic" in spec:
            style.font.italic = bool(spec["italic"])
        if "color" in spec:
            style.font.color.rgb = RGBColor.from_string(
                color(spec["color"], f"styles.{style_name}.color")
            )
        if "space_before" in spec:
            style.paragraph_format.space_before = Pt(float(spec["space_before"]))
        if "space_after" in spec:
            style.paragraph_format.space_after = Pt(float(spec["space_after"]))
        if "keep_with_next" in spec:
            style.paragraph_format.keep_with_next = bool(spec["keep_with_next"])


def apply_core_properties(document: Any, raw_spec: Any) -> None:
    spec = expect_object(raw_spec, "properties")
    allowed = {
        "title",
        "subject",
        "author",
        "last_modified_by",
        "category",
        "keywords",
        "comments",
        "language",
        "identifier",
        "version",
    }
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(f"properties 包含未知字段：{sorted(unknown)}")
    properties = document.core_properties
    for key, value in spec.items():
        setattr(properties, key, value)


def _clear_container(container: Any) -> None:
    for paragraph in list(container.paragraphs):
        container._element.remove(paragraph._element)
    for table in list(container.tables):
        container._element.remove(table._element)


def apply_header_footer(document: Any, raw_header: Any, raw_footer: Any) -> None:
    for section in document.sections:
        if raw_header is not None:
            spec = expect_object(raw_header, "header")
            _clear_container(section.header)
            if "blocks" in spec:
                add_blocks(document, section.header, spec["blocks"])
            else:
                paragraph = section.header.add_paragraph()
                add_runs(
                    paragraph,
                    text=str(spec.get("text", "")),
                    runs=spec.get("runs"),
                )
                apply_paragraph_format(paragraph, spec)
        if raw_footer is not None:
            spec = expect_object(raw_footer, "footer")
            _clear_container(section.footer)
            if "blocks" in spec:
                add_blocks(document, section.footer, spec["blocks"])
            else:
                paragraph = section.footer.add_paragraph()
                add_runs(
                    paragraph,
                    text=str(spec.get("text", "")),
                    runs=spec.get("runs"),
                )
                apply_paragraph_format(paragraph, spec)
                if spec.get("page_number"):
                    _add_page_number(
                        paragraph,
                        prefix=str(spec.get("page_number_prefix", "")),
                        suffix=str(spec.get("page_number_suffix", "")),
                    )


def set_update_fields(document: Any) -> None:
    from docx.oxml import OxmlElement

    settings = document.settings._element
    existing = settings.find(qn("updateFields"))
    if existing is None:
        existing = OxmlElement("w:updateFields")
        settings.append(existing)
    existing.set(qn("val"), "true")
