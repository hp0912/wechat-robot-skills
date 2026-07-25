#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape

from _pdf_common import (
    SkillArgumentParser,
    new_temp_pdf,
    output_pdf,
    publish_temp_file,
    run_cli,
)

FONT_NAME = "PDFSkillDocumentFont"
ASCII_DASHES = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="从 UTF-8 文本创建排版后的 PDF")
    parser.add_argument("--input", required=True, help="UTF-8 文本或 Markdown 文件")
    parser.add_argument("--output", required=True, help="PDF 输出路径")
    parser.add_argument("--title", default="", help="文档标题")
    parser.add_argument(
        "--page-size",
        choices=("A4", "LETTER"),
        default="A4",
        help="页面大小，默认 A4",
    )
    parser.add_argument(
        "--font-path",
        default="",
        help="可选的 TTF/TTC 字体路径；包含中文时会自动查找预置字体",
    )
    parser.add_argument(
        "--font-size",
        type=float,
        default=11,
        help="正文字号，默认 11",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=54,
        help="页边距（points），默认 54",
    )
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 PDF")
    args = parser.parse_args(argv)
    if args.font_size < 6 or args.font_size > 36:
        raise ValueError("font-size 必须在 6 到 36 之间")
    if args.margin < 18 or args.margin > 144:
        raise ValueError("margin 必须在 18 到 144 之间")
    return args


def _input_text(value: str) -> tuple[Path, str]:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文本输入文件不存在：{path}")
    text = path.read_text(encoding="utf-8").translate(ASCII_DASHES)
    if not text.strip():
        raise ValueError("文本输入文件为空")
    return path, text


def _font_candidates(explicit: str, text: str) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    environment_font = os.environ.get("PDF_FONT_PATH", "").strip()
    if environment_font:
        candidates.append(Path(environment_font).expanduser().resolve())

    has_cjk = bool(re.search(r"[\u2e80-\u9fff\uf900-\ufaff]", text))
    if has_cjk:
        names = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        families = ("Noto Sans CJK SC", "Source Han Sans SC", "Hiragino Sans GB")
    else:
        names = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
        families = ("DejaVu Sans", "Arial")
    candidates.extend(Path(name) for name in names)

    fc_match = shutil_which("fc-match")
    if fc_match:
        with tempfile.TemporaryDirectory(prefix="pdf-font-cache-") as cache_dir:
            environment = os.environ.copy()
            environment["XDG_CACHE_HOME"] = cache_dir
            for family in families:
                try:
                    completed = subprocess.run(
                        [fc_match, "-f", "%{file}\n", family],
                        env=environment,
                        text=True,
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    continue
                for line in completed.stdout.splitlines():
                    if line.strip():
                        candidates.append(Path(line.strip()))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def shutil_which(command: str) -> Optional[str]:
    import shutil

    return shutil.which(command)


def _register_font(text: str, explicit: str) -> tuple[str, Optional[str]]:
    if all(ord(character) < 128 for character in text):
        return "Helvetica", None

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    errors = []
    for candidate in _font_candidates(explicit, text):
        if not candidate.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(candidate)))
            return FONT_NAME, str(candidate)
        except Exception as exc:
            errors.append(f"{candidate.name}: {exc}")
    detail = errors[-1] if errors else "未找到候选字体"
    raise RuntimeError(f"环境预置字体无法覆盖文档字符：{detail}")


def _markdown_table(lines: list[str]) -> Optional[list[list[str]]]:
    if len(lines) < 2 or not all("|" in line for line in lines):
        return None

    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines
    ]
    if not rows or not rows[0]:
        return None
    separator = rows[1]
    if len(separator) != len(rows[0]) or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in separator
    ):
        return None
    data = [rows[0], *rows[2:]]
    if any(len(row) != len(rows[0]) for row in data):
        return None
    return data


def _story(
    text: str,
    title: str,
    font_name: str,
    font_size: float,
    available_width: float,
):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    body = ParagraphStyle(
        "Body",
        fontName=font_name,
        fontSize=font_size,
        leading=font_size * 1.5,
        spaceAfter=font_size * 0.7,
        wordWrap="CJK",
    )
    heading1 = ParagraphStyle(
        "Heading1",
        parent=body,
        fontSize=font_size * 1.7,
        leading=font_size * 2,
        spaceBefore=font_size,
        spaceAfter=font_size,
    )
    heading2 = ParagraphStyle(
        "Heading2",
        parent=body,
        fontSize=font_size * 1.35,
        leading=font_size * 1.7,
        spaceBefore=font_size * 0.8,
        spaceAfter=font_size * 0.6,
    )
    heading3 = ParagraphStyle(
        "Heading3",
        parent=body,
        fontSize=font_size * 1.15,
        leading=font_size * 1.5,
        spaceBefore=font_size * 0.6,
        spaceAfter=font_size * 0.4,
    )
    title_style = ParagraphStyle(
        "Title",
        parent=heading1,
        fontSize=font_size * 2,
        leading=font_size * 2.4,
        alignment=TA_CENTER,
        spaceAfter=10 * mm,
    )

    story = []
    if title.strip():
        story.append(Paragraph(escape(title.strip()), title_style))

    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.rstrip() for line in block.splitlines()]
        if not lines:
            continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+)", lines[0].strip())
        table_data = _markdown_table(lines)
        if table_data is not None:
            cell_style = ParagraphStyle(
                "TableCell",
                parent=body,
                fontSize=max(7, font_size * 0.85),
                leading=max(9, font_size * 1.15),
                spaceAfter=0,
            )
            cells = [
                [Paragraph(escape(cell), cell_style) for cell in row]
                for row in table_data
            ]
            column_width = available_width / len(cells[0])
            table = Table(
                cells,
                colWidths=[column_width] * len(cells[0]),
                repeatRows=1,
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font_name),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF5")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8793A1")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.append(table)
        elif heading and len(lines) == 1:
            styles = {"#": heading1, "##": heading2, "###": heading3}
            story.append(Paragraph(escape(heading.group(2)), styles[heading.group(1)]))
        elif all(line.lstrip().startswith("- ") for line in lines if line.strip()):
            for line in lines:
                if line.strip():
                    story.append(
                        Paragraph(
                            escape(line.lstrip()[2:]),
                            body,
                            bulletText="-",
                        )
                    )
        else:
            content = "<br/>".join(escape(line) for line in lines)
            story.append(Paragraph(content, body))
        story.append(Spacer(1, font_size * 0.35))
    return story


def _create(args) -> dict[str, Any]:
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import A4, LETTER
    from reportlab.platypus import SimpleDocTemplate

    input_path, text = _input_text(args.input)
    title = args.title.strip().translate(ASCII_DASHES)
    font_name, font_path = _register_font(f"{title}\n{text}", args.font_path)
    output = output_pdf(args.output, args.overwrite)
    temporary = new_temp_pdf(output)
    page_size = A4 if args.page_size == "A4" else LETTER

    def draw_footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setTitle(title or input_path.stem)
        canvas.setFont(font_name, 8)
        canvas.drawCentredString(page_size[0] / 2, 18, str(document.page))
        canvas.restoreState()

    try:
        document = SimpleDocTemplate(
            str(temporary),
            pagesize=page_size,
            leftMargin=args.margin,
            rightMargin=args.margin,
            topMargin=args.margin,
            bottomMargin=max(args.margin, 36),
            title=title or input_path.stem,
        )
        document.build(
            _story(
                text,
                title,
                font_name,
                args.font_size,
                page_size[0] - (2 * args.margin),
            ),
            onFirstPage=draw_footer,
            onLaterPages=draw_footer,
        )
        with temporary.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            page_count = len(reader.pages)
        publish_temp_file(temporary, output, args.overwrite)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "input": str(input_path),
        "output": str(output),
        "page_count": page_count,
        "page_size": args.page_size,
        "font": font_name,
        "font_path": font_path,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _create(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
