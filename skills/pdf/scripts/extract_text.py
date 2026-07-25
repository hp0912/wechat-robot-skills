#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _pdf_common import (
    SkillArgumentParser,
    input_pdf,
    run_cli,
    selected_page_window,
)

DEFAULT_MAX_PAGES = 8
DEFAULT_MAX_CHARS = 24000
DEFAULT_TIMEOUT_SECONDS = 120
MIN_MEANINGFUL_PAGE_CHARS = 20
IMAGE_PAGE_MIN_MEANINGFUL_CHARS = 100
IMAGE_PAGE_MIN_COVERAGE = 0.5
IMAGE_COVERAGE_GRID_SIZE = 32
CID_PATTERN = re.compile(r"\(cid:\d+\)", re.IGNORECASE)
PAGE_NUMBER_PATTERN = re.compile(
    r"(?im)^\s*\d+\s*(?:of|/)\s*\d+\s*$"
)


@dataclass
class ExtractionCandidate:
    engine: str
    texts: list[str]
    quality: dict[str, Any]
    warnings: list[str]
    page_engines: list[str]


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="按页分段提取 PDF 文本")
    parser.add_argument("--input", required=True, help="本地 PDF 路径")
    parser.add_argument("--start-page", type=int, default=1, help="起始页，1-based")
    parser.add_argument("--end-page", type=int, help="结束页，1-based，默认到末页")
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="起始页文本字符偏移量；大于 0 时须传入上次返回的 next_engine",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"单次最多处理页数，默认 {DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"单次最多返回文本字符数，默认 {DEFAULT_MAX_CHARS}",
    )
    parser.add_argument(
        "--layout",
        action="store_true",
        help="尽量保留版面空格；普通总结通常不要启用",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "poppler", "pdfplumber"),
        default="auto",
        help="提取引擎，默认 auto：优先 Poppler，失败或质量差时回退 pdfplumber",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Poppler 提取超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
    )
    args = parser.parse_args(argv)
    if args.start_offset < 0:
        raise ValueError("start-offset 不能小于 0")
    if args.start_offset > 0 and args.engine == "auto":
        raise ValueError(
            "使用 start-offset 续读同一页时，必须把上次返回的 "
            "next_engine 传给 --engine"
        )
    if args.max_chars < 1:
        raise ValueError("max-chars 必须大于 0")
    if args.timeout < 1:
        raise ValueError("timeout 必须大于 0")
    return args


def _matrix(values) -> tuple[float, float, float, float, float, float]:
    try:
        converted = tuple(float(value) for value in values[:6])
        if len(converted) != 6:
            raise ValueError
        return (
            converted[0],
            converted[1],
            converted[2],
            converted[3],
            converted[4],
            converted[5],
        )
    except (TypeError, ValueError):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _concat_matrix(
    current: tuple[float, float, float, float, float, float],
    update: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    ca, cb, cc, cd, ce, cf = current
    ua, ub, uc, ud, ue, uf = update
    return (
        ca * ua + cc * ub,
        cb * ua + cd * ub,
        ca * uc + cc * ud,
        cb * uc + cd * ud,
        ca * ue + cc * uf + ce,
        cb * ue + cd * uf + cf,
    )


def _transform_point(
    matrix: tuple[float, float, float, float, float, float],
    point: tuple[float, float],
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def _rectangle_polygon(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    matrix: tuple[float, float, float, float, float, float],
) -> list[tuple[float, float]]:
    return [
        _transform_point(matrix, point)
        for point in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
    ]


def _point_in_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _grid_cell_center(
    page_rect: tuple[float, float, float, float],
    grid_x: int,
    grid_y: int,
) -> tuple[float, float]:
    left, bottom, right, top = page_rect
    grid = IMAGE_COVERAGE_GRID_SIZE
    return (
        left + (grid_x + 0.5) * (right - left) / grid,
        bottom + (grid_y + 0.5) * (top - bottom) / grid,
    )


def _polygon_cells(
    polygon: list[tuple[float, float]],
    page_rect: tuple[float, float, float, float],
) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    if len(polygon) < 3:
        return cells
    left, bottom, right, top = page_rect
    width = right - left
    height = top - bottom
    if width <= 0 or height <= 0:
        return cells
    min_x = max(left, min(point[0] for point in polygon))
    max_x = min(right, max(point[0] for point in polygon))
    min_y = max(bottom, min(point[1] for point in polygon))
    max_y = min(top, max(point[1] for point in polygon))
    if min_x >= max_x or min_y >= max_y:
        return cells

    grid = IMAGE_COVERAGE_GRID_SIZE
    start_x = max(0, int((min_x - left) / width * grid))
    end_x = min(grid - 1, int((max_x - left) / width * grid))
    start_y = max(0, int((min_y - bottom) / height * grid))
    end_y = min(grid - 1, int((max_y - bottom) / height * grid))
    for grid_x in range(start_x, end_x + 1):
        for grid_y in range(start_y, end_y + 1):
            center = _grid_cell_center(page_rect, grid_x, grid_y)
            if _point_in_polygon(center, polygon):
                cells.add((grid_x, grid_y))
    return cells


def _winding_number(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> int:
    x, y = point
    winding = 0
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1)
        if y1 <= y < y2 and cross > 0:
            winding += 1
        elif y2 <= y < y1 and cross < 0:
            winding -= 1
        previous = current
    return winding


def _clip_path_cells(
    polygons: list[list[tuple[float, float]]],
    page_rect: tuple[float, float, float, float],
    even_odd: bool,
) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    grid = IMAGE_COVERAGE_GRID_SIZE
    for grid_x in range(grid):
        for grid_y in range(grid):
            center = _grid_cell_center(page_rect, grid_x, grid_y)
            if even_odd:
                inside = sum(
                    1
                    for polygon in polygons
                    if _point_in_polygon(center, polygon)
                ) % 2 == 1
            else:
                inside = sum(
                    _winding_number(center, polygon)
                    for polygon in polygons
                ) != 0
            if inside:
                result.add((grid_x, grid_y))
    return result


def _cubic_curve_points(
    start: tuple[float, float],
    control_1: tuple[float, float],
    control_2: tuple[float, float],
    end: tuple[float, float],
    steps: int = 8,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for step in range(1, steps + 1):
        t = step / steps
        inverse = 1 - t
        points.append(
            (
                inverse**3 * start[0]
                + 3 * inverse**2 * t * control_1[0]
                + 3 * inverse * t**2 * control_2[0]
                + t**3 * end[0],
                inverse**3 * start[1]
                + 3 * inverse**2 * t * control_1[1]
                + 3 * inverse * t**2 * control_2[1]
                + t**3 * end[1],
            )
        )
    return points


def _resolve(value):
    try:
        return value.get_object()
    except Exception:
        return value


def _form_marker(xobject) -> tuple[Any, ...]:
    reference = getattr(xobject, "indirect_reference", None)
    if reference is not None:
        try:
            return ("indirect", int(reference.idnum), int(reference.generation))
        except Exception:
            pass
    return ("object", id(xobject))


def _walk_content_images(
    content,
    resources,
    reader,
    page_rect: tuple[float, float, float, float],
    covered_cells: set[tuple[int, int]],
    initial_matrix: tuple[
        float,
        float,
        float,
        float,
        float,
        float,
    ] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
    initial_clip_cells: Optional[set[tuple[int, int]]] = None,
    active_forms: Optional[set[tuple]] = None,
) -> bool:
    from pypdf.generic import ContentStream

    if content is None or resources is None:
        return False
    try:
        operations = ContentStream(content, reader).operations
    except Exception:
        return False

    resolved_resources = _resolve(resources)
    try:
        xobjects = _resolve(resolved_resources.get("/XObject") or {})
    except Exception:
        xobjects = {}

    grid = IMAGE_COVERAGE_GRID_SIZE
    current_matrix = initial_matrix
    current_clip_cells = (
        {
            (grid_x, grid_y)
            for grid_x in range(grid)
            for grid_y in range(grid)
        }
        if initial_clip_cells is None
        else initial_clip_cells
    )
    stack: list[
        tuple[
            tuple[float, float, float, float, float, float],
            set[tuple[int, int]],
        ]
    ] = []
    has_image = False
    forms = active_forms if active_forms is not None else set()
    path_polygons: list[list[tuple[float, float]]] = []
    current_subpath: Optional[list[tuple[float, float]]] = None
    pending_clip_rule: Optional[bytes] = None
    path_end_operators = {
        b"n",
        b"S",
        b"s",
        b"f",
        b"F",
        b"f*",
        b"B",
        b"B*",
        b"b",
        b"b*",
    }

    for operands, operator in operations:
        if operator == b"q":
            stack.append((current_matrix, current_clip_cells))
        elif operator == b"Q":
            if stack:
                current_matrix, current_clip_cells = stack.pop()
        elif operator == b"cm":
            current_matrix = _concat_matrix(
                current_matrix,
                _matrix(operands),
            )
        elif operator == b"re":
            try:
                x, y, width, height = (
                    float(value) for value in operands[:4]
                )
                rectangle = _rectangle_polygon(
                    x,
                    y,
                    x + width,
                    y + height,
                    current_matrix,
                )
            except (TypeError, ValueError):
                rectangle = None
            if rectangle is not None:
                path_polygons.append(rectangle)
        elif operator == b"m":
            if current_subpath and len(current_subpath) >= 3:
                path_polygons.append(current_subpath)
            try:
                current_subpath = [
                    _transform_point(
                        current_matrix,
                        (float(operands[0]), float(operands[1])),
                    )
                ]
            except (IndexError, TypeError, ValueError):
                current_subpath = None
        elif operator == b"l" and current_subpath is not None:
            try:
                current_subpath.append(
                    _transform_point(
                        current_matrix,
                        (float(operands[0]), float(operands[1])),
                    )
                )
            except (IndexError, TypeError, ValueError):
                pass
        elif operator == b"c" and current_subpath:
            try:
                control_1 = _transform_point(
                    current_matrix,
                    (float(operands[0]), float(operands[1])),
                )
                control_2 = _transform_point(
                    current_matrix,
                    (float(operands[2]), float(operands[3])),
                )
                end = _transform_point(
                    current_matrix,
                    (float(operands[4]), float(operands[5])),
                )
                current_subpath.extend(
                    _cubic_curve_points(
                        current_subpath[-1],
                        control_1,
                        control_2,
                        end,
                    )
                )
            except (IndexError, TypeError, ValueError):
                pass
        elif operator == b"v" and current_subpath:
            try:
                control_2 = _transform_point(
                    current_matrix,
                    (float(operands[0]), float(operands[1])),
                )
                end = _transform_point(
                    current_matrix,
                    (float(operands[2]), float(operands[3])),
                )
                current_subpath.extend(
                    _cubic_curve_points(
                        current_subpath[-1],
                        current_subpath[-1],
                        control_2,
                        end,
                    )
                )
            except (IndexError, TypeError, ValueError):
                pass
        elif operator == b"y" and current_subpath:
            try:
                control_1 = _transform_point(
                    current_matrix,
                    (float(operands[0]), float(operands[1])),
                )
                end = _transform_point(
                    current_matrix,
                    (float(operands[2]), float(operands[3])),
                )
                current_subpath.extend(
                    _cubic_curve_points(
                        current_subpath[-1],
                        control_1,
                        end,
                        end,
                    )
                )
            except (IndexError, TypeError, ValueError):
                pass
        elif operator == b"h":
            if current_subpath and len(current_subpath) >= 3:
                path_polygons.append(current_subpath)
            current_subpath = None
        elif operator in {b"W", b"W*"}:
            pending_clip_rule = operator
        elif operator == b"INLINE IMAGE":
            image_polygon = _rectangle_polygon(
                0,
                0,
                1,
                1,
                current_matrix,
            )
            has_image = True
            covered_cells.update(
                _polygon_cells(
                    image_polygon,
                    page_rect,
                ).intersection(current_clip_cells)
            )
        elif operator == b"Do" and operands:
            try:
                xobject = _resolve(xobjects.get(operands[0]))
                subtype = str(xobject.get("/Subtype"))
            except Exception:
                continue
            if subtype == "/Image":
                image_polygon = _rectangle_polygon(
                    0,
                    0,
                    1,
                    1,
                    current_matrix,
                )
                has_image = True
                covered_cells.update(
                    _polygon_cells(
                        image_polygon,
                        page_rect,
                    ).intersection(current_clip_cells)
                )
            elif subtype == "/Form":
                marker = _form_marker(xobject)
                if marker in forms:
                    continue
                form_matrix = _concat_matrix(
                    current_matrix,
                    _matrix(
                        xobject.get("/Matrix")
                        or (1, 0, 0, 1, 0, 0)
                    ),
                )
                form_clip_cells = current_clip_cells
                try:
                    x1, y1, x2, y2 = (
                        float(value) for value in xobject.get("/BBox")
                    )
                    form_bbox = _rectangle_polygon(
                        x1,
                        y1,
                        x2,
                        y2,
                        form_matrix,
                    )
                    form_clip_cells = current_clip_cells.intersection(
                        _polygon_cells(form_bbox, page_rect)
                    )
                except (TypeError, ValueError):
                    pass
                forms.add(marker)
                try:
                    nested_has_image = _walk_content_images(
                        xobject,
                        xobject.get("/Resources") or resolved_resources,
                        reader,
                        page_rect,
                        covered_cells,
                        initial_matrix=form_matrix,
                        initial_clip_cells=form_clip_cells,
                        active_forms=forms,
                    )
                finally:
                    forms.remove(marker)
                has_image = has_image or nested_has_image
        if operator in path_end_operators:
            if current_subpath and len(current_subpath) >= 3:
                path_polygons.append(current_subpath)
            if pending_clip_rule is not None and path_polygons:
                path_cells = _clip_path_cells(
                    path_polygons,
                    page_rect,
                    even_odd=pending_clip_rule == b"W*",
                )
                current_clip_cells = current_clip_cells.intersection(
                    path_cells
                )
            path_polygons = []
            current_subpath = None
            pending_clip_rule = None

    return has_image


def _content_image_coverage(
    content,
    resources,
    reader,
    page_rect: tuple[float, float, float, float],
) -> tuple[bool, float]:
    covered_cells: set[tuple[int, int]] = set()
    has_image = _walk_content_images(
        content,
        resources,
        reader,
        page_rect,
        covered_cells,
    )
    grid_cells = IMAGE_COVERAGE_GRID_SIZE**2
    return has_image, len(covered_cells) / grid_cells


def _page_count(path: Path) -> int:
    from pypdf import PdfReader

    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法提取正文")
        return len(reader.pages)


def _image_coverage_by_page(
    path: Path,
    start_page: int,
    end_page: int,
) -> dict[int, float]:
    from pypdf import PdfReader

    coverage: dict[int, float] = {}
    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        for page_number in range(start_page, end_page + 1):
            page = reader.pages[page_number - 1]
            left = float(page.cropbox.left)
            bottom = float(page.cropbox.bottom)
            right = float(page.cropbox.right)
            top = float(page.cropbox.top)
            has_image, page_coverage = _content_image_coverage(
                page.get_contents(),
                page.get("/Resources"),
                reader,
                (
                    min(left, right),
                    min(bottom, top),
                    max(left, right),
                    max(bottom, top),
                ),
            )
            if has_image:
                coverage[page_number] = page_coverage
    return coverage


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _find_pdftotext() -> Path:
    executable_names = (
        ("pdftotext.exe", "pdftotext")
        if os.name == "nt"
        else ("pdftotext", "pdftotext.exe")
    )
    for executable_name in executable_names:
        direct = shutil.which(executable_name)
        if direct:
            return Path(direct).resolve()

    probes: list[Path] = [Path(sys.executable).resolve()]
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        probes.append(Path(pdftoppm).resolve())

    checked: set[Path] = set()
    for probe in probes:
        for executable_name in executable_names:
            sibling = probe.with_name(executable_name)
            if sibling not in checked:
                checked.add(sibling)
                if _is_executable(sibling):
                    return sibling

        for ancestor in probe.parents:
            for executable_name in executable_names:
                for relative in (
                    Path("native/poppler/bin") / executable_name,
                    Path("native/poppler/poppler/bin") / executable_name,
                ):
                    candidate = ancestor / relative
                    if candidate in checked:
                        continue
                    checked.add(candidate)
                    if _is_executable(candidate):
                        return candidate

    raise RuntimeError("环境预置的 pdftotext 不可用")


def _stderr_warnings(value: str) -> list[str]:
    warnings: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if line and line not in warnings:
            warnings.append(line[:500])
        if len(warnings) >= 5:
            break
    return warnings


def _split_poppler_pages(output: str, expected_pages: int) -> list[str]:
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    pages = normalized.split("\f")
    while len(pages) > expected_pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != expected_pages:
        raise RuntimeError(
            f"pdftotext 返回页数不正确：预期 {expected_pages}，实际 {len(pages)}"
        )
    return [page.rstrip("\n").replace("\x00", "") for page in pages]


def _extract_with_poppler(
    path: Path,
    start_page: int,
    end_page: int,
    layout: bool,
    timeout: int,
) -> tuple[list[str], list[str]]:
    executable = _find_pdftotext()
    command = [
        str(executable),
        "-enc",
        "UTF-8",
        "-f",
        str(start_page),
        "-l",
        str(end_page),
    ]
    if layout:
        command.append("-layout")
    command.extend((str(path), "-"))

    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pdftotext 提取超过 {timeout} 秒") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise RuntimeError(f"pdftotext 提取失败：{detail or '返回非零状态'}")

    expected_pages = end_page - start_page + 1
    return (
        _split_poppler_pages(completed.stdout, expected_pages),
        _stderr_warnings(completed.stderr),
    )


def _extract_with_pdfplumber(
    path: Path,
    canonical_page_count: int,
    start_page: int,
    end_page: int,
    layout: bool,
) -> tuple[list[str], list[str]]:
    import pdfplumber

    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        observed_page_count = len(pdf.pages)
        if observed_page_count != canonical_page_count:
            raise RuntimeError(
                "pdfplumber 页数与 PDF 检查结果不一致："
                f"{observed_page_count} != {canonical_page_count}"
            )
        for page_number in range(start_page, end_page + 1):
            text = pdf.pages[page_number - 1].extract_text(layout=layout) or ""
            texts.append(text.replace("\x00", ""))
    return texts, []


def _is_radical(character: str) -> bool:
    codepoint = ord(character)
    return 0x2E80 <= codepoint <= 0x2FDF


def _page_quality(
    text: str,
    page_number: int,
    image_coverage: float,
) -> dict[str, Any]:
    has_image = image_coverage > 0
    visible_chars = sum(1 for character in text if not character.isspace())
    meaningful_text = PAGE_NUMBER_PATTERN.sub("", text)
    meaningful_chars = sum(
        1 for character in meaningful_text if character.isalnum()
    )
    cid_tokens = len(CID_PATTERN.findall(text))
    replacement_chars = text.count("\ufffd")
    radical_chars = sum(1 for character in text if _is_radical(character))
    control_chars = sum(
        1
        for character in text
        if ord(character) < 32 and character not in "\n\r\t\f"
    )

    reasons: list[str] = []
    if visible_chars == 0:
        status = "empty"
        reasons.append("未提取到可见文字")
        if has_image:
            reasons.append("页面包含图像对象")
    else:
        cid_ratio = cid_tokens / visible_chars
        replacement_ratio = replacement_chars / visible_chars
        radical_ratio = radical_chars / visible_chars
        control_ratio = control_chars / visible_chars
        if cid_tokens >= 3 or cid_ratio >= 0.01:
            reasons.append("包含大量 (cid:...) 字体映射占位符")
        if replacement_chars >= 3 or replacement_ratio >= 0.01:
            reasons.append("包含大量 Unicode 替换字符")
        if radical_chars >= 3 and radical_ratio >= 0.005:
            reasons.append("包含疑似替代汉字的部首字符")
        if control_chars >= 3 or control_ratio >= 0.01:
            reasons.append("包含异常控制字符")
        if reasons:
            status = "poor"
        elif (
            image_coverage >= IMAGE_PAGE_MIN_COVERAGE
            and meaningful_chars < IMAGE_PAGE_MIN_MEANINGFUL_CHARS
        ):
            status = "sparse"
            reasons.append(
                f"页面图像覆盖约 {round(image_coverage * 100)}%，"
                f"且可提取文字少于 {IMAGE_PAGE_MIN_MEANINGFUL_CHARS} 个字符"
            )
        elif meaningful_chars < MIN_MEANINGFUL_PAGE_CHARS:
            status = "sparse"
            reasons.append(
                f"有效文字少于 {MIN_MEANINGFUL_PAGE_CHARS} 个字符"
            )
        else:
            status = "good"

    penalty = min(
        100,
        cid_tokens * 8
        + replacement_chars * 8
        + radical_chars * 2
        + control_chars * 4,
    )
    if status == "empty":
        score = 0
    elif status == "sparse":
        score = min(50, meaningful_chars * 2)
    else:
        score = max(0, 100 - penalty)
    return {
        "page": page_number,
        "has_image": has_image,
        "image_coverage": round(image_coverage, 4),
        "status": status,
        "score": score,
        "visible_chars": visible_chars,
        "meaningful_chars": meaningful_chars,
        "cid_tokens": cid_tokens,
        "replacement_chars": replacement_chars,
        "radical_chars": radical_chars,
        "control_chars": control_chars,
        "reasons": reasons,
    }


def _text_quality(
    texts: list[str],
    start_page: int,
    image_coverage: dict[int, float],
) -> dict[str, Any]:
    pages = [
        _page_quality(
            text,
            start_page + index,
            image_coverage.get(start_page + index, 0.0),
        )
        for index, text in enumerate(texts)
    ]
    statuses = [page["status"] for page in pages]
    good_pages = [page["page"] for page in pages if page["status"] == "good"]
    suspect_pages = [
        page["page"] for page in pages if page["status"] != "good"
    ]

    if statuses and all(status == "good" for status in statuses):
        status = "good"
    elif good_pages:
        status = "mixed"
    elif statuses and all(item in {"empty", "sparse"} for item in statuses):
        status = "empty"
    else:
        status = "poor"

    reasons: list[str] = []
    for page in pages:
        for reason in page["reasons"]:
            message = f"第 {page['page']} 页：{reason}"
            if message not in reasons:
                reasons.append(message)

    score = (
        round(sum(page["score"] for page in pages) / len(pages))
        if pages
        else 0
    )
    return {
        "status": status,
        "score": score,
        "visible_chars": sum(page["visible_chars"] for page in pages),
        "meaningful_chars": sum(page["meaningful_chars"] for page in pages),
        "cid_tokens": sum(page["cid_tokens"] for page in pages),
        "replacement_chars": sum(page["replacement_chars"] for page in pages),
        "radical_chars": sum(page["radical_chars"] for page in pages),
        "control_chars": sum(page["control_chars"] for page in pages),
        "image_pages": sorted(
            page["page"] for page in pages if page["has_image"]
        ),
        "good_pages": good_pages,
        "suspect_pages": suspect_pages,
        "reasons": reasons,
        "pages": pages,
    }


def _attempt_engine(
    engine: str,
    path: Path,
    page_count: int,
    image_coverage: dict[int, float],
    start_page: int,
    end_page: int,
    layout: bool,
    timeout: int,
) -> ExtractionCandidate:
    if engine == "poppler":
        texts, warnings = _extract_with_poppler(
            path,
            start_page,
            end_page,
            layout,
            timeout,
        )
    elif engine == "pdfplumber":
        texts, warnings = _extract_with_pdfplumber(
            path,
            page_count,
            start_page,
            end_page,
            layout,
        )
    else:
        raise ValueError(f"未知提取引擎：{engine}")
    return ExtractionCandidate(
        engine=engine,
        texts=texts,
        quality=_text_quality(texts, start_page, image_coverage),
        warnings=warnings,
        page_engines=[engine] * len(texts),
    )


def _merge_candidates(
    candidates: list[ExtractionCandidate],
    start_page: int,
    image_coverage: dict[int, float],
) -> ExtractionCandidate:
    page_rank = {"empty": 0, "poor": 1, "sparse": 2, "good": 3}
    merged_texts: list[str] = []
    page_engines: list[str] = []

    for page_index in range(len(candidates[0].texts)):
        chosen = max(
            candidates,
            key=lambda candidate: (
                page_rank[
                    candidate.quality["pages"][page_index]["status"]
                ],
                candidate.quality["pages"][page_index]["score"],
                candidate.quality["pages"][page_index]["visible_chars"],
            ),
        )
        merged_texts.append(chosen.texts[page_index])
        page_engines.append(chosen.page_engines[page_index])

    warnings: list[str] = []
    for candidate in candidates:
        for warning in candidate.warnings:
            if warning not in warnings:
                warnings.append(warning)

    unique_engines = list(dict.fromkeys(page_engines))
    return ExtractionCandidate(
        engine=(
            unique_engines[0]
            if len(unique_engines) == 1
            else "hybrid"
        ),
        texts=merged_texts,
        quality=_text_quality(
            merged_texts,
            start_page,
            image_coverage,
        ),
        warnings=warnings,
        page_engines=page_engines,
    )


def _select_candidate(
    args,
    path: Path,
    page_count: int,
    image_coverage: dict[int, float],
    start_page: int,
    end_page: int,
) -> tuple[ExtractionCandidate, list[dict[str, Any]]]:
    engines = (
        ("poppler", "pdfplumber")
        if args.engine == "auto"
        else (args.engine,)
    )
    candidates: list[ExtractionCandidate] = []
    attempts: list[dict[str, Any]] = []

    for engine in engines:
        try:
            candidate = _attempt_engine(
                engine,
                path,
                page_count,
                image_coverage,
                start_page,
                end_page,
                args.layout,
                args.timeout,
            )
        except Exception as exc:
            attempts.append(
                {
                    "engine": engine,
                    "ok": False,
                    "error": str(exc),
                }
            )
            continue

        candidates.append(candidate)
        attempts.append(
            {
                "engine": engine,
                "ok": True,
                "quality": candidate.quality["status"],
                "score": candidate.quality["score"],
            }
        )
        if candidate.quality["status"] == "good":
            return candidate, attempts

    if not candidates:
        details = "；".join(
            f"{attempt['engine']}: {attempt.get('error', '失败')}"
            for attempt in attempts
        )
        raise RuntimeError(f"所有文字提取引擎均失败：{details}")

    return (
        _merge_candidates(candidates, start_page, image_coverage),
        attempts,
    )


def _paginate(
    texts: list[str],
    page_qualities: list[dict[str, Any]],
    page_engines: list[str],
    start_page: int,
    start_offset: int,
    max_chars: int,
    window_next: Optional[int],
) -> tuple[
    list[dict[str, Any]],
    int,
    Optional[int],
    int,
    Optional[str],
]:
    pages_output: list[dict[str, Any]] = []
    used_chars = 0
    next_page: Optional[int] = None
    next_offset = 0
    next_engine: Optional[str] = None
    quality_by_page = {
        item["page"]: item for item in page_qualities
    }

    for index, text in enumerate(texts):
        page_number = start_page + index
        page_engine = page_engines[index]
        page_quality = quality_by_page[page_number]
        text_usable = page_quality["status"] == "good"
        offset = start_offset if index == 0 else 0
        if offset > len(text):
            raise ValueError(
                f"start-offset 超过第 {page_number} 页文本长度 {len(text)}"
            )
        if not text_usable:
            if offset > 0:
                raise ValueError(
                    f"第 {page_number} 页文本质量不可用，不能使用 start-offset"
                )
            pages_output.append(
                {
                    "page": page_number,
                    "text": "",
                    "char_count": len(text),
                    "offset_start": 0,
                    "offset_end": 0,
                    "complete": True,
                    "usable_for_summary": False,
                    "text_quality_status": page_quality["status"],
                    "extractor": page_engine,
                }
            )
            continue
        remaining = text[offset:]
        budget = max_chars - used_chars

        if budget <= 0:
            next_page = page_number
            next_offset = offset
            break
        if len(remaining) > budget:
            if used_chars > 0 and budget < min(500, len(remaining)):
                next_page = page_number
                next_offset = offset
                break
            excerpt = remaining[:budget]
            pages_output.append(
                {
                    "page": page_number,
                    "text": excerpt,
                    "char_count": len(text),
                    "offset_start": offset,
                    "offset_end": offset + len(excerpt),
                    "complete": False,
                    "usable_for_summary": True,
                    "text_quality_status": page_quality["status"],
                    "extractor": page_engine,
                }
            )
            used_chars += len(excerpt)
            next_page = page_number
            next_offset = offset + len(excerpt)
            next_engine = page_engine
            break

        pages_output.append(
            {
                "page": page_number,
                "text": remaining,
                "char_count": len(text),
                "offset_start": offset,
                "offset_end": len(text),
                "complete": True,
                "usable_for_summary": True,
                "text_quality_status": page_quality["status"],
                "extractor": page_engine,
            }
        )
        used_chars += len(remaining)

    if next_page is None:
        next_page = window_next
    return (
        pages_output,
        used_chars,
        next_page,
        next_offset,
        next_engine,
    )


def _extract(args) -> dict[str, Any]:
    path = input_pdf(args.input)
    page_count = _page_count(path)
    effective_max_pages = 1 if args.start_offset > 0 else args.max_pages
    start_page, actual_end, window_next = selected_page_window(
        page_count,
        args.start_page,
        args.end_page,
        effective_max_pages,
    )
    image_coverage = _image_coverage_by_page(
        path,
        start_page,
        actual_end,
    )
    candidate, attempts = _select_candidate(
        args,
        path,
        page_count,
        image_coverage,
        start_page,
        actual_end,
    )
    (
        pages_output,
        used_chars,
        next_page,
        next_offset,
        next_engine,
    ) = _paginate(
        candidate.texts,
        candidate.quality["pages"],
        candidate.page_engines,
        start_page,
        args.start_offset,
        args.max_chars,
        window_next,
    )

    usable = any(
        page["usable_for_summary"] for page in pages_output
    )
    needs_ocr = bool(candidate.quality["suspect_pages"])

    return {
        "path": str(path),
        "page_count": page_count,
        "extractor": candidate.engine,
        "engine_attempts": attempts,
        "text_quality": candidate.quality,
        "usable_for_summary": usable,
        "complete_text_coverage": not needs_ocr,
        "needs_ocr": needs_ocr,
        "warnings": candidate.warnings,
        "start_page": start_page,
        "end_page": pages_output[-1]["page"] if pages_output else None,
        "window_end_page": actual_end,
        "returned_chars": used_chars,
        "pages": pages_output,
        "has_more": next_page is not None,
        "next_page": next_page,
        "next_offset": next_offset,
        "next_engine": next_engine,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _extract(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
