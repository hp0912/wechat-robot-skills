#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from _pptx_common import (
    OOXML_PRESENTATION_SUFFIXES,
    SkillArgumentParser,
    find_program,
    input_file,
    output_directory,
    publish_file,
    run_cli,
    run_program,
    run_soffice_convert,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="通过 LibreOffice 和 Poppler 把演示文稿渲染为逐页 PNG。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-slide", type=int, default=1)
    parser.add_argument("--end-slide", type=int)
    parser.add_argument("--max-slides", type=int, default=30)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--include-pdf", action="store_true")
    parser.add_argument("--contact-sheet", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _contact_sheet(
    images: list[Path],
    slide_numbers: list[int],
    destination: Path,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    columns = min(4, max(1, len(images)))
    rows = math.ceil(len(images) / columns)
    thumb_width = 420
    label_height = 34
    gap = 18
    opened: list[Image.Image] = []
    try:
        for path in images:
            opened.append(Image.open(path).convert("RGB"))
        aspect = opened[0].height / opened[0].width
        thumb_height = max(1, round(thumb_width * aspect))
        canvas_width = columns * thumb_width + (columns + 1) * gap
        canvas_height = rows * (thumb_height + label_height) + (rows + 1) * gap
        canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                20,
            )
        except OSError:
            font = ImageFont.load_default()
        for index, (image, slide_number) in enumerate(zip(opened, slide_numbers)):
            row, column = divmod(index, columns)
            x = gap + column * (thumb_width + gap)
            y = gap + row * (thumb_height + label_height)
            thumbnail = image.copy()
            thumbnail.thumbnail((thumb_width, thumb_height))
            paste_x = x + (thumb_width - thumbnail.width) // 2
            paste_y = y + (thumb_height - thumbnail.height) // 2
            canvas.paste(thumbnail, (paste_x, paste_y))
            label = f"Slide {slide_number}"
            label_box = draw.textbbox((0, 0), label, font=font)
            label_width = label_box[2] - label_box[0]
            draw.text(
                (x + (thumb_width - label_width) // 2, y + thumb_height + 6),
                label,
                fill="black",
                font=font,
            )
        canvas.save(destination, format="PNG", optimize=True)
    finally:
        for image in opened:
            image.close()


def main() -> dict[str, Any]:
    from pypdf import PdfReader

    args = build_parser().parse_args()
    if args.start_slide < 1:
        raise ValueError("start-slide 必须大于 0")
    if args.end_slide is not None and args.end_slide < args.start_slide:
        raise ValueError("end-slide 不能小于 start-slide")
    if args.max_slides < 1 or args.max_slides > 100:
        raise ValueError("max-slides 必须在 1 到 100 之间")
    if args.dpi < 72 or args.dpi > 300:
        raise ValueError("dpi 必须在 72 到 300 之间")

    source = input_file(args.input, OOXML_PRESENTATION_SUFFIXES)
    destination_dir = output_directory(args.output_dir)
    with tempfile.TemporaryDirectory(prefix="pptx-render-") as temp_name:
        temp_dir = Path(temp_name)
        staged_input = temp_dir / f"presentation{source.suffix.lower()}"
        shutil.copy2(source, staged_input)
        pdf_path, office_output = run_soffice_convert(
            staged_input,
            target_format="pdf",
            output_dir=temp_dir / "pdf",
            timeout=args.timeout,
        )
        slide_count = len(PdfReader(str(pdf_path)).pages)
        if slide_count < 1:
            raise ValueError("LibreOffice 生成的 PDF 没有页面")
        if args.start_slide > slide_count:
            raise ValueError(f"start-slide 超出页面总数 {slide_count}")
        requested_end = slide_count if args.end_slide is None else args.end_slide
        if requested_end > slide_count:
            raise ValueError(f"end-slide 超出页面总数 {slide_count}")
        actual_end = min(
            requested_end,
            args.start_slide + args.max_slides - 1,
        )

        raw_prefix = temp_dir / "raw-slide"
        run_program(
            [
                find_program("pdftoppm"),
                "-png",
                "-r",
                str(args.dpi),
                "-f",
                str(args.start_slide),
                "-l",
                str(actual_end),
                str(pdf_path),
                str(raw_prefix),
            ],
            timeout=args.timeout,
        )
        raw_pages = sorted(
            temp_dir.glob("raw-slide-*.png"),
            key=lambda path: int(path.stem.rsplit("-", 1)[1]),
        )
        expected_count = actual_end - args.start_slide + 1
        if len(raw_pages) != expected_count:
            raise RuntimeError(
                f"Poppler 应生成 {expected_count} 页，实际生成 {len(raw_pages)} 页"
            )
        slide_numbers = list(range(args.start_slide, actual_end + 1))
        destinations = [
            destination_dir / f"slide-{number:04d}.png"
            for number in slide_numbers
        ]
        contact_destination = destination_dir / (
            f"contact-sheet-{args.start_slide:04d}-{actual_end:04d}.png"
        )
        if args.contact_sheet:
            destinations.append(contact_destination)
        if args.include_pdf:
            destinations.append(destination_dir / "presentation.pdf")
        if not args.overwrite:
            existing = [str(path) for path in destinations if path.exists()]
            if existing:
                raise FileExistsError("以下渲染目标已存在：" + "、".join(existing))

        output_paths: list[str] = []
        published_pages: list[Path] = []
        for slide_number, raw_page in zip(slide_numbers, raw_pages):
            destination = destination_dir / f"slide-{slide_number:04d}.png"
            publish_file(raw_page, destination, overwrite=args.overwrite)
            output_paths.append(str(destination))
            published_pages.append(destination)

        contact_sheet_path: Optional[str] = None
        if args.contact_sheet:
            staged_contact = temp_dir / "contact-sheet.png"
            _contact_sheet(
                published_pages,
                slide_numbers,
                staged_contact,
            )
            publish_file(
                staged_contact,
                contact_destination,
                overwrite=args.overwrite,
            )
            contact_sheet_path = str(contact_destination)

        pdf_output: Optional[str] = None
        if args.include_pdf:
            destination_pdf = destination_dir / "presentation.pdf"
            staged_pdf = temp_dir / "publish.pdf"
            shutil.copy2(pdf_path, staged_pdf)
            publish_file(staged_pdf, destination_pdf, overwrite=args.overwrite)
            pdf_output = str(destination_pdf)

    next_slide = actual_end + 1 if actual_end < requested_end else None
    return {
        "source": str(source),
        "slide_count": slide_count,
        "start_slide": args.start_slide,
        "end_slide": actual_end,
        "rendered_slides": output_paths,
        "contact_sheet": contact_sheet_path,
        "pdf": pdf_output,
        "has_more": next_slide is not None,
        "next_slide": next_slide,
        "dpi": args.dpi,
        "office_stdout": office_output["stdout"],
        "office_stderr": office_output["stderr"],
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
