#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import difflib
import importlib.metadata
import io
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from _docx_common import (
    WORD_INPUT_SUFFIXES,
    SkillArgumentParser,
    find_program,
    input_file,
    run_cli,
    run_program,
    run_soffice_convert,
)


DEFAULT_DPI = 260
DEFAULT_MAX_CHARS = 24000
DEFAULT_TIMEOUT_SECONDS = 180
MAX_PAGES_PER_CALL = 4
MAX_PIXELS_PER_PAGE = 20_000_000
MIN_MEAN_CONFIDENCE = 0.60
MIN_MEANINGFUL_CHARS = 5
MIN_OCR_IMAGE_AREA_RATIO = 0.015
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


def build_parser():
    parser = SkillArgumentParser(
        description="渲染 Word 页面并用本地 OCR 提取图片中的文字。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--pages",
        help=(
            "要识别的页码，例如 2 或 2,5-6；省略时自动选择含可读图片的页面"
        ),
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="续读单页图片文字时的字符偏移量",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"单次最多返回字符数，默认 {DEFAULT_MAX_CHARS}",
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
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"转换和单页渲染超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
    )
    return parser


def _parse_page_spec(value: str, page_count: int) -> list[int]:
    if not value.strip():
        raise ValueError("pages 不能为空")
    pages: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            pieces = part.split("-", 1)
            try:
                start = int(pieces[0])
                end = int(pieces[1])
            except ValueError as exc:
                raise ValueError(f"页码范围格式错误：{part}") from exc
            if start > end:
                raise ValueError(f"页码范围起始值不能大于结束值：{part}")
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise ValueError(f"页码格式错误：{part}") from exc
        if start < 1 or end > page_count:
            raise ValueError(f"页码必须在 1 到 {page_count} 之间：{part}")
        pages.update(range(start, end + 1))
    if not pages:
        raise ValueError("pages 不能为空")
    return sorted(pages)


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return "\n".join(
        WHITESPACE_PATTERN.sub(" ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    )


def _comparison_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _normalized_box(
    x0: Any,
    top: Any,
    x1: Any,
    bottom: Any,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float] | None:
    try:
        left = max(0.0, min(page_width, float(x0)))
        upper = max(0.0, min(page_height, float(top)))
        right = max(0.0, min(page_width, float(x1)))
        lower = max(0.0, min(page_height, float(bottom)))
    except (TypeError, ValueError):
        return None
    if (
        page_width <= 0
        or page_height <= 0
        or right <= left
        or lower <= upper
    ):
        return None
    return (
        left / page_width,
        upper / page_height,
        right / page_width,
        lower / page_height,
    )


def _group_native_words(
    words: list[dict[str, Any]],
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    sorted_words = sorted(
        words,
        key=lambda word: (
            round(float(word.get("top", 0.0)) / 2.5),
            float(word.get("x0", 0.0)),
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    group_top: float | None = None
    for word in sorted_words:
        try:
            top = float(word.get("top", 0.0))
        except (TypeError, ValueError):
            continue
        if not groups or group_top is None or abs(top - group_top) > 3.0:
            groups.append([word])
            group_top = top
        else:
            groups[-1].append(word)
            group_top = sum(
                float(item.get("top", 0.0)) for item in groups[-1]
            ) / len(groups[-1])

    lines: list[dict[str, Any]] = []
    for group in groups:
        group.sort(key=lambda word: float(word.get("x0", 0.0)))
        text = _clean_text(
            " ".join(str(word.get("text", "")) for word in group)
        )
        key = _comparison_key(text)
        if not key:
            continue
        box = _normalized_box(
            min(float(word.get("x0", 0.0)) for word in group),
            min(float(word.get("top", 0.0)) for word in group),
            max(float(word.get("x1", 0.0)) for word in group),
            max(float(word.get("bottom", 0.0)) for word in group),
            page_width,
            page_height,
        )
        if box is not None:
            lines.append({"key": key, "box": box})
    return lines


def _page_profiles(
    pdf_path: Path,
) -> tuple[dict[int, dict[str, Any]], list[str]]:
    import pdfplumber

    profiles: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            page_width = float(page.width)
            page_height = float(page.height)
            try:
                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=3,
                    keep_blank_chars=False,
                    use_text_flow=False,
                )
            except Exception as exc:
                words = []
                warnings.append(
                    f"第 {page_number} 页原生文本层读取失败：{exc}"
                )
            native_lines = _group_native_words(
                list(words or []),
                page_width,
                page_height,
            )
            native_keys = [line["key"] for line in native_lines]

            all_image_boxes: list[
                tuple[float, float, float, float]
            ] = []
            ocr_image_boxes: list[
                tuple[float, float, float, float]
            ] = []
            image_area = 0.0
            image_count = 0
            for image in page.images:
                box = _normalized_box(
                    image.get("x0"),
                    image.get("top"),
                    image.get("x1"),
                    image.get("bottom"),
                    page_width,
                    page_height,
                )
                if box is None:
                    continue
                image_count += 1
                all_image_boxes.append(box)
                area = (box[2] - box[0]) * (box[3] - box[1])
                image_area += area
                if area >= MIN_OCR_IMAGE_AREA_RATIO:
                    ocr_image_boxes.append(box)

            profiles[page_number] = {
                "image_count": image_count,
                "ocr_image_count": len(ocr_image_boxes),
                "image_area_ratio": round(min(1.0, image_area), 4),
                "native_text_char_count": sum(
                    len(key) for key in native_keys
                ),
                "_native_keys": native_keys,
                "_native_boxes": native_lines,
                "_image_boxes": all_image_boxes,
            }
    return profiles, warnings


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


def _similar_to_any(
    candidate: str,
    references: list[str],
    *,
    threshold: float,
) -> bool:
    if not candidate:
        return False
    for reference in references:
        if not reference:
            continue
        if candidate == reference:
            return True
        shorter = min(len(candidate), len(reference))
        longer = max(len(candidate), len(reference))
        if shorter >= 3 and candidate in reference:
            return True
        if (
            shorter >= 3
            and reference in candidate
            and longer <= round(shorter * 1.25)
        ):
            return True
        if shorter >= 3 and difflib.SequenceMatcher(
            None,
            candidate,
            reference,
        ).ratio() >= threshold:
            return True
    return False


def _line_center(
    box: list[list[float]] | None,
    image_width: int,
    image_height: int,
) -> tuple[float, float] | None:
    if not box or image_width <= 0 or image_height <= 0:
        return None
    return (
        sum(point[0] for point in box) / len(box) / image_width,
        sum(point[1] for point in box) / len(box) / image_height,
    )


def _point_in_boxes(
    point: tuple[float, float] | None,
    boxes: list[tuple[float, float, float, float]],
    *,
    padding: float = 0.005,
) -> bool:
    if point is None:
        return False
    x, y = point
    return any(
        x0 - padding <= x <= x1 + padding
        and y0 - padding <= y <= y1 + padding
        for x0, y0, x1, y1 in boxes
    )


def _line_is_native(
    line: dict[str, Any],
    profile: dict[str, Any],
    image_width: int,
    image_height: int,
) -> bool:
    candidate = _comparison_key(line["text"])
    if _similar_to_any(
        candidate,
        profile["_native_keys"],
        threshold=0.82,
    ):
        return True

    center = _line_center(line["box"], image_width, image_height)
    if center is None:
        return False
    x, y = center
    padding = 0.01
    for native_line in profile["_native_boxes"]:
        x0, y0, x1, y1 = native_line["box"]
        if (
            x0 - padding <= x <= x1 + padding
            and y0 - padding <= y <= y1 + padding
            and _similar_to_any(
                candidate,
                [native_line["key"]],
                threshold=0.68,
            )
        ):
            return True
    return False


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


def _ocr_page(
    engine: Any,
    image_path: Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    from PIL import Image

    with Image.open(image_path) as image:
        image_width, image_height = image.size

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    started = time.monotonic()
    with (
        contextlib.redirect_stdout(captured_stdout),
        contextlib.redirect_stderr(captured_stderr),
    ):
        result = engine(str(image_path))
    elapsed = time.monotonic() - started

    raw_lines = _ordered_lines(result)
    image_lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    filtered_native = 0
    filtered_outside_images = 0
    filtered_duplicates = 0
    image_boxes = profile["_image_boxes"]
    for line in raw_lines:
        if _line_is_native(line, profile, image_width, image_height):
            filtered_native += 1
            continue
        center = _line_center(line["box"], image_width, image_height)
        if image_boxes and not _point_in_boxes(center, image_boxes):
            filtered_outside_images += 1
            continue
        key = _comparison_key(line["text"])
        if key and key in seen:
            filtered_duplicates += 1
            continue
        if key:
            seen.add(key)
        image_lines.append(line)

    text = "\n".join(line["text"] for line in image_lines)
    weighted_chars = [
        max(1, sum(1 for character in line["text"] if not character.isspace()))
        for line in image_lines
    ]
    total_weight = sum(weighted_chars)
    mean_confidence = (
        sum(
            line["confidence"] * weight
            for line, weight in zip(image_lines, weighted_chars)
        )
        / total_weight
        if total_weight
        else 0.0
    )
    meaningful_chars = sum(1 for character in text if character.isalnum())
    low_confidence_lines = sum(
        1
        for line in image_lines
        if line["confidence"] < MIN_MEAN_CONFIDENCE
    )

    reasons: list[str] = []
    if not text:
        status = "no_image_text"
        reasons.append("未识别到原生文本之外的图片文字")
    elif meaningful_chars < MIN_MEANINGFUL_CHARS:
        status = "sparse"
        reasons.append(
            f"图片中的有效文字少于 {MIN_MEANINGFUL_CHARS} 个字符"
        )
    elif mean_confidence < MIN_MEAN_CONFIDENCE:
        status = "low_confidence"
        reasons.append(
            "图片文字 OCR 平均置信度低于 "
            f"{round(MIN_MEAN_CONFIDENCE * 100)}%"
        )
    else:
        status = "good"

    return {
        "text": text,
        "status": status,
        "usable_for_summary": status == "good",
        "needs_review": status in {"sparse", "low_confidence"},
        "raw_ocr_line_count": len(raw_lines),
        "image_line_count": len(image_lines),
        "filtered_native_line_count": filtered_native,
        "filtered_outside_image_line_count": filtered_outside_images,
        "filtered_duplicate_line_count": filtered_duplicates,
        "low_confidence_line_count": low_confidence_lines,
        "mean_confidence": round(mean_confidence, 4),
        "meaningful_chars": meaningful_chars,
        "reasons": reasons,
        "ocr_seconds": round(elapsed, 3),
    }


def _pdf_pages(path: Path) -> tuple[int, dict[int, tuple[float, float]]]:
    from pypdf import PdfReader

    page_sizes: dict[int, tuple[float, float]] = {}
    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("LibreOffice 生成了加密 PDF，无法执行 OCR")
        page_count = len(reader.pages)
        for page_number, page in enumerate(reader.pages, start=1):
            page_sizes[page_number] = (
                abs(float(page.cropbox.width)),
                abs(float(page.cropbox.height)),
            )
    return page_count, page_sizes


def _render_page(
    pdf_path: Path,
    page_number: int,
    page_size: tuple[float, float],
    dpi: int,
    timeout: int,
    temp_dir: Path,
) -> tuple[Path, float]:
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

    prefix = temp_dir / f"page-{page_number:04d}"
    output = prefix.with_suffix(".png")
    started = time.monotonic()
    run_program(
        [
            find_program("pdftoppm"),
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
        ],
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"第 {page_number} 页没有生成有效 PNG")
    return output, elapsed


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    if args.start_offset < 0:
        raise ValueError("start-offset 不能小于 0")
    if args.max_chars < 1 or args.max_chars > 60000:
        raise ValueError("max-chars 必须在 1 到 60000 之间")
    if args.dpi < 150 or args.dpi > 400:
        raise ValueError("dpi 必须在 150 到 400 之间")
    if args.timeout < 1 or args.timeout > 600:
        raise ValueError("timeout 必须在 1 到 600 秒之间")

    source = input_file(args.input, WORD_INPUT_SUFFIXES)
    page_outputs: list[dict[str, Any]] = []
    returned_chars = 0
    next_page: int | None = None
    next_offset = 0
    remaining_pages: list[int] = []
    office_output = {"stdout": "", "stderr": ""}

    with tempfile.TemporaryDirectory(prefix="docx-ocr-") as temp_name:
        temp_dir = Path(temp_name)
        staged_input = temp_dir / f"document{source.suffix.lower()}"
        shutil.copy2(source, staged_input)
        pdf_path, office_output = run_soffice_convert(
            staged_input,
            target_format="pdf",
            output_dir=temp_dir / "pdf",
            timeout=args.timeout,
        )
        page_count, page_sizes = _pdf_pages(pdf_path)
        if page_count < 1:
            raise ValueError("Word 文档没有可执行 OCR 的页面")
        profiles, profile_warnings = _page_profiles(pdf_path)
        if len(profiles) != page_count:
            raise RuntimeError(
                f"渲染结果有 {page_count} 页，但只检查到 "
                f"{len(profiles)} 页"
            )
        candidate_pages = [
            page_number
            for page_number, profile in profiles.items()
            if profile["ocr_image_count"] > 0
        ]

        selection_mode = "explicit" if args.pages else "auto"
        if args.pages:
            target_pages = _parse_page_spec(args.pages, page_count)
            if len(target_pages) > MAX_PAGES_PER_CALL:
                raise ValueError(
                    f"单次最多 OCR {MAX_PAGES_PER_CALL} 页，"
                    "请拆分 pages 后重试"
                )
        else:
            target_pages = candidate_pages

        if args.start_offset > 0:
            if not args.pages or len(target_pages) != 1:
                raise ValueError(
                    "使用 start-offset 时必须显式指定且只指定一页"
                )

        selected_pages = target_pages[:MAX_PAGES_PER_CALL]
        queued_pages = target_pages[MAX_PAGES_PER_CALL:]
        if selected_pages:
            engine = _create_ocr_engine()
            for index, page_number in enumerate(selected_pages):
                budget = args.max_chars - returned_chars
                if budget <= 0:
                    next_page = page_number
                    remaining_pages = (
                        selected_pages[index:] + queued_pages
                    )
                    break

                image_path, render_seconds = _render_page(
                    pdf_path,
                    page_number,
                    page_sizes[page_number],
                    args.dpi,
                    args.timeout,
                    temp_dir,
                )
                result = _ocr_page(
                    engine,
                    image_path,
                    profiles[page_number],
                )
                full_text = result.pop("text")
                offset = args.start_offset if index == 0 else 0
                if offset > len(full_text):
                    raise ValueError(
                        f"start-offset 超过第 {page_number} 页图片文字长度 "
                        f"{len(full_text)}"
                    )

                usable = bool(result["usable_for_summary"])
                if not usable:
                    page_text = ""
                    complete = True
                else:
                    remaining_text = full_text[offset:]
                    page_text = remaining_text[:budget]
                    complete = len(page_text) == len(remaining_text)

                profile = profiles[page_number]
                page_outputs.append(
                    {
                        "page": page_number,
                        "text": page_text,
                        "char_count": len(full_text),
                        "offset_start": offset if usable else 0,
                        "offset_end": (
                            offset + len(page_text) if usable else 0
                        ),
                        "complete": complete,
                        "render_seconds": round(render_seconds, 3),
                        "image_count": profile["image_count"],
                        "ocr_image_count": profile["ocr_image_count"],
                        "image_area_ratio": profile[
                            "image_area_ratio"
                        ],
                        "native_text_char_count": profile[
                            "native_text_char_count"
                        ],
                        **result,
                    }
                )
                returned_chars += len(page_text)

                if not complete:
                    next_page = page_number
                    next_offset = offset + len(page_text)
                    remaining_pages = (
                        selected_pages[index + 1 :] + queued_pages
                    )
                    break

        if next_page is None and queued_pages:
            next_page = queued_pages[0]
            remaining_pages = queued_pages

    all_processed = len(page_outputs) == len(selected_pages)
    all_complete = all(page["complete"] for page in page_outputs)
    all_safe = all(
        page["status"] in {"good", "no_image_text"}
        for page in page_outputs
    )
    has_more = next_page is not None
    return {
        "source": str(source),
        "page_count": page_count,
        "selection_mode": selection_mode,
        "candidate_pages": candidate_pages,
        "selected_pages": selected_pages,
        "processed_pages": [page["page"] for page in page_outputs],
        "engine": "rapidocr",
        "engine_version": _package_version("rapidocr"),
        "runtime": "onnxruntime",
        "runtime_version": _package_version("onnxruntime"),
        "offline": True,
        "dpi": args.dpi,
        "returned_chars": returned_chars,
        "pages": page_outputs,
        "usable_for_summary": any(
            page["usable_for_summary"] for page in page_outputs
        ),
        "complete_ocr_coverage": (
            not has_more
            and all_processed
            and all_complete
            and all_safe
        ),
        "needs_review": any(page["needs_review"] for page in page_outputs),
        "has_more": has_more,
        "next_page": next_page,
        "next_offset": next_offset,
        "remaining_pages": remaining_pages,
        "profile_warnings": profile_warnings,
        "office_stdout": office_output["stdout"],
        "office_stderr": office_output["stderr"],
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
