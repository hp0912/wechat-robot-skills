#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.metadata
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _pdf_common import (
    SkillArgumentParser,
    input_pdf,
    parse_page_spec,
    run_cli,
)

DEFAULT_DPI = 260
DEFAULT_MAX_CHARS = 24000
DEFAULT_RENDER_TIMEOUT_SECONDS = 180
MAX_PAGES_PER_CALL = 4
MAX_PIXELS_PER_PAGE = 20_000_000
MIN_MEAN_CONFIDENCE = 0.60
MIN_MEANINGFUL_CHARS = 5
WHITESPACE_PATTERN = re.compile(r"[ \t]+")


for variable, value in (
    ("OMP_NUM_THREADS", "2"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
):
    os.environ.setdefault(variable, value)

for logger_name in ("rapidocr", "RapidOCR", "onnxruntime"):
    logging.getLogger(logger_name).setLevel(logging.ERROR)


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="本地 OCR 提取指定 PDF 页面文字")
    parser.add_argument("--input", required=True, help="本地 PDF 路径")
    parser.add_argument(
        "--pages",
        required=True,
        help="要识别的页码，例如 2 或 2,5-6；单次最多 4 页",
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="续读单页 OCR 文字时的字符偏移量",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"单次最多返回文本字符数，默认 {DEFAULT_MAX_CHARS}",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"OCR 渲染分辨率，默认 {DEFAULT_DPI} DPI",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_RENDER_TIMEOUT_SECONDS,
        help=(
            "每页 Poppler 渲染超时秒数，"
            f"默认 {DEFAULT_RENDER_TIMEOUT_SECONDS}"
        ),
    )
    args = parser.parse_args(argv)
    if args.start_offset < 0:
        raise ValueError("start-offset 不能小于 0")
    if args.max_chars < 1 or args.max_chars > 60000:
        raise ValueError("max-chars 必须在 1 到 60000 之间")
    if args.dpi < 150 or args.dpi > 400:
        raise ValueError("dpi 必须在 150 到 400 之间")
    if args.timeout < 1 or args.timeout > 600:
        raise ValueError("timeout 必须在 1 到 600 之间")
    return args


def _pdf_pages(path: Path) -> tuple[int, dict[int, tuple[float, float]]]:
    from pypdf import PdfReader

    page_sizes: dict[int, tuple[float, float]] = {}
    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法执行 OCR")
        page_count = len(reader.pages)
        for page_number, page in enumerate(reader.pages, start=1):
            page_sizes[page_number] = (
                abs(float(page.cropbox.width)),
                abs(float(page.cropbox.height)),
            )
    return page_count, page_sizes


def _find_pdftoppm() -> Path:
    executable = shutil.which("pdftoppm")
    if not executable:
        raise RuntimeError("环境预置的 pdftoppm 不可用")
    return Path(executable).resolve()


def _validate_render_size(
    page_number: int,
    page_size: tuple[float, float],
    dpi: int,
) -> None:
    width_points, height_points = page_size
    estimated_pixels = (
        width_points * dpi / 72.0
        * height_points * dpi / 72.0
    )
    if estimated_pixels > MAX_PIXELS_PER_PAGE:
        raise ValueError(
            f"第 {page_number} 页按 {dpi} DPI 渲染预计超过 "
            f"{MAX_PIXELS_PER_PAGE} 像素，请降低 dpi"
        )


def _render_page(
    executable: Path,
    pdf_path: Path,
    page_number: int,
    page_size: tuple[float, float],
    dpi: int,
    timeout: int,
    temp_dir: Path,
) -> tuple[Path, float]:
    _validate_render_size(page_number, page_size, dpi)
    prefix = temp_dir / f"page-{page_number:04d}"
    output = prefix.with_suffix(".png")
    command = [
        str(executable),
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        "-singlefile",
        "-png",
        "-r",
        str(dpi),
        str(pdf_path),
        str(prefix),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"第 {page_number} 页渲染超过 {timeout} 秒"
        ) from exc
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise RuntimeError(
            f"第 {page_number} 页渲染失败："
            f"{detail or 'pdftoppm 返回错误'}"
        )
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"第 {page_number} 页没有生成有效 PNG")
    return output, elapsed


def _create_ocr_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("环境预置的 rapidocr 模块不可用") from exc

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with (
        contextlib.redirect_stdout(captured_stdout),
        contextlib.redirect_stderr(captured_stderr),
    ):
        return RapidOCR()


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return "\n".join(
        WHITESPACE_PATTERN.sub(" ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    )


def _box_points(value: Any) -> list[list[float]] | None:
    if value is None:
        return None
    try:
        points = [
            [round(float(point[0]), 2), round(float(point[1]), 2)]
            for point in value
        ]
    except (IndexError, TypeError, ValueError):
        return None
    return points if len(points) == 4 else None


def _ordered_lines(result: Any) -> list[dict[str, Any]]:
    texts = list(getattr(result, "txts", None) or ())
    scores = list(getattr(result, "scores", None) or ())
    raw_boxes = getattr(result, "boxes", None)
    boxes = list(raw_boxes) if raw_boxes is not None else []

    lines: list[dict[str, Any]] = []
    for index, raw_text in enumerate(texts):
        text = _clean_text(raw_text)
        if not text:
            continue
        try:
            confidence = float(scores[index])
        except (IndexError, TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        box = _box_points(boxes[index] if index < len(boxes) else None)
        if box:
            left = min(point[0] for point in box)
            top = min(point[1] for point in box)
        else:
            left = float(index)
            top = float(index)
        lines.append(
            {
                "text": text,
                "confidence": confidence,
                "box": box,
                "_left": left,
                "_top": top,
                "_index": index,
            }
        )

    lines.sort(
        key=lambda line: (
            round(line["_top"] / 10.0),
            line["_left"],
            line["_index"],
        )
    )
    return lines


def _ocr_page(engine: Any, image_path: Path) -> dict[str, Any]:
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    started = time.monotonic()
    with (
        contextlib.redirect_stdout(captured_stdout),
        contextlib.redirect_stderr(captured_stderr),
    ):
        result = engine(str(image_path))
    elapsed = time.monotonic() - started

    lines = _ordered_lines(result)
    text = "\n".join(line["text"] for line in lines)
    weighted_chars = [
        max(1, sum(1 for character in line["text"] if not character.isspace()))
        for line in lines
    ]
    total_weight = sum(weighted_chars)
    mean_confidence = (
        sum(
            line["confidence"] * weight
            for line, weight in zip(lines, weighted_chars)
        )
        / total_weight
        if total_weight
        else 0.0
    )
    meaningful_chars = sum(1 for character in text if character.isalnum())
    low_confidence_lines = sum(
        1 for line in lines if line["confidence"] < MIN_MEAN_CONFIDENCE
    )

    reasons: list[str] = []
    if not text:
        status = "empty"
        reasons.append("本地 OCR 未识别到文字")
    elif meaningful_chars < MIN_MEANINGFUL_CHARS:
        status = "sparse"
        reasons.append(
            f"有效文字少于 {MIN_MEANINGFUL_CHARS} 个字符"
        )
    elif mean_confidence < MIN_MEAN_CONFIDENCE:
        status = "low_confidence"
        reasons.append(
            "OCR 平均置信度低于 "
            f"{round(MIN_MEAN_CONFIDENCE * 100)}%"
        )
    else:
        status = "good"

    return {
        "text": text,
        "status": status,
        "usable_for_summary": status == "good",
        "line_count": len(lines),
        "low_confidence_line_count": low_confidence_lines,
        "mean_confidence": round(mean_confidence, 4),
        "meaningful_chars": meaningful_chars,
        "reasons": reasons,
        "ocr_seconds": round(elapsed, 3),
    }


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _extract(args) -> dict[str, Any]:
    path = input_pdf(args.input)
    page_count, page_sizes = _pdf_pages(path)
    if page_count < 1:
        raise ValueError("PDF 没有可执行 OCR 的页面")
    requested_pages = parse_page_spec(args.pages, page_count)
    if len(requested_pages) > MAX_PAGES_PER_CALL:
        raise ValueError(
            f"单次最多 OCR {MAX_PAGES_PER_CALL} 页，"
            "请拆分 pages 后重试"
        )
    if args.start_offset > 0 and len(requested_pages) != 1:
        raise ValueError("使用 start-offset 时 pages 必须只包含一页")

    executable = _find_pdftoppm()
    engine = _create_ocr_engine()
    page_outputs: list[dict[str, Any]] = []
    returned_chars = 0
    next_page: int | None = None
    next_offset = 0
    remaining_pages: list[int] = []

    with tempfile.TemporaryDirectory(prefix="pdf-ocr-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for index, page_number in enumerate(requested_pages):
            budget = args.max_chars - returned_chars
            if budget <= 0:
                next_page = page_number
                remaining_pages = requested_pages[index:]
                break

            image_path, render_seconds = _render_page(
                executable,
                path,
                page_number,
                page_sizes[page_number],
                args.dpi,
                args.timeout,
                temp_dir,
            )
            result = _ocr_page(engine, image_path)
            full_text = result.pop("text")
            offset = args.start_offset if index == 0 else 0
            if offset > len(full_text):
                raise ValueError(
                    f"start-offset 超过第 {page_number} 页 OCR "
                    f"文本长度 {len(full_text)}"
                )

            usable = bool(result["usable_for_summary"])
            if not usable:
                page_text = ""
                complete = True
            else:
                remaining_text = full_text[offset:]
                page_text = remaining_text[:budget]
                complete = len(page_text) == len(remaining_text)

            page_outputs.append(
                {
                    "page": page_number,
                    "text": page_text,
                    "char_count": len(full_text),
                    "offset_start": offset if usable else 0,
                    "offset_end": offset + len(page_text) if usable else 0,
                    "complete": complete,
                    "render_seconds": round(render_seconds, 3),
                    **result,
                }
            )
            returned_chars += len(page_text)

            if not complete:
                next_page = page_number
                next_offset = offset + len(page_text)
                remaining_pages = requested_pages[index + 1 :]
                break

    has_more = next_page is not None
    all_processed = len(page_outputs) == len(requested_pages)
    all_complete = all(page["complete"] for page in page_outputs)
    all_usable = all(
        page["usable_for_summary"] for page in page_outputs
    )
    return {
        "path": str(path),
        "page_count": page_count,
        "engine": "rapidocr",
        "engine_version": _package_version("rapidocr"),
        "runtime": "onnxruntime",
        "runtime_version": _package_version("onnxruntime"),
        "offline": True,
        "dpi": args.dpi,
        "requested_pages": requested_pages,
        "processed_pages": [page["page"] for page in page_outputs],
        "returned_chars": returned_chars,
        "pages": page_outputs,
        "usable_for_summary": any(
            page["usable_for_summary"] for page in page_outputs
        ),
        "complete_ocr_coverage": (
            all_processed and all_complete and all_usable
        ),
        "needs_review": any(
            not page["usable_for_summary"] for page in page_outputs
        ),
        "has_more": has_more,
        "next_page": next_page,
        "next_offset": next_offset,
        "remaining_pages": remaining_pages,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _extract(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
