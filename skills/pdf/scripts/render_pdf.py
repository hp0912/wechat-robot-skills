#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _pdf_common import (
    SkillArgumentParser,
    input_pdf,
    output_directory,
    publish_temp_file,
    run_cli,
    selected_page_window,
)

DEFAULT_DPI = 150
DEFAULT_MAX_PAGES = 10
DEFAULT_TIMEOUT_SECONDS = 180


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="使用 Poppler 将 PDF 页面渲染为 PNG")
    parser.add_argument("--input", required=True, help="本地 PDF 路径")
    parser.add_argument("--output-dir", required=True, help="PNG 输出目录")
    parser.add_argument("--start-page", type=int, default=1, help="起始页，1-based")
    parser.add_argument("--end-page", type=int, help="结束页，1-based，默认到末页")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"单次最多渲染页数，默认 {DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"渲染分辨率，默认 {DEFAULT_DPI} DPI",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"渲染超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有页面 PNG")
    args = parser.parse_args(argv)
    if args.dpi < 36 or args.dpi > 600:
        raise ValueError("dpi 必须在 36 到 600 之间")
    if args.timeout < 1:
        raise ValueError("timeout 必须大于 0")
    return args


def _page_count(path: Path) -> int:
    from pypdf import PdfReader

    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法渲染")
        return len(reader.pages)


def _fontconfig_file(pdftoppm: Path) -> Path | None:
    for ancestor in pdftoppm.parents:
        candidate = (
            ancestor
            / "native"
            / "poppler"
            / "poppler"
            / "etc"
            / "fonts"
            / "fonts.conf"
        )
        if candidate.is_file():
            return candidate
    for candidate in (
        Path("/etc/fonts/fonts.conf"),
        Path("/opt/homebrew/etc/fonts/fonts.conf"),
        Path("/usr/local/etc/fonts/fonts.conf"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _render(args) -> dict[str, Any]:
    path = input_pdf(args.input)
    destination = output_directory(args.output_dir)
    page_count = _page_count(path)
    start_page, end_page, next_page = selected_page_window(
        page_count,
        args.start_page,
        args.end_page,
        args.max_pages,
    )

    outputs = [
        destination / f"page-{page_number:04d}.png"
        for page_number in range(start_page, end_page + 1)
    ]
    existing = [path for path in outputs if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(f"页面图片已存在：{existing[0]}")

    executable_name = shutil.which("pdftoppm")
    if not executable_name:
        raise RuntimeError("环境预置的 pdftoppm 不可用")
    executable = Path(executable_name).resolve()

    with tempfile.TemporaryDirectory(
        prefix="pdf-render-",
        dir=str(destination),
    ) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        prefix = temp_dir / "page"
        font_cache = destination / ".fontconfig-cache"
        font_cache.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["XDG_CACHE_HOME"] = str(font_cache)
        if not environment.get("FONTCONFIG_FILE"):
            fontconfig = _fontconfig_file(executable)
            if fontconfig is not None:
                environment["FONTCONFIG_FILE"] = str(fontconfig)

        command = [
            str(executable),
            "-f",
            str(start_page),
            "-l",
            str(end_page),
            "-png",
            "-r",
            str(args.dpi),
            str(path),
            str(prefix),
        ]
        try:
            completed = subprocess.run(
                command,
                env=environment,
                text=True,
                capture_output=True,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"PDF 渲染超过 {args.timeout} 秒") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
            raise RuntimeError(f"PDF 渲染失败：{detail or 'pdftoppm 返回错误'}")

        rendered = sorted(
            temp_dir.glob("page-*.png"),
            key=lambda item: int(item.stem.rsplit("-", 1)[-1]),
        )
        if len(rendered) != len(outputs):
            raise RuntimeError(
                f"PDF 渲染页数不正确：预期 {len(outputs)}，实际 {len(rendered)}"
            )

        for temporary, output in zip(rendered, outputs):
            publish_temp_file(temporary, output, args.overwrite)

    return {
        "input": str(path),
        "output_dir": str(destination),
        "page_count": page_count,
        "start_page": start_page,
        "end_page": end_page,
        "dpi": args.dpi,
        "rendered_count": len(outputs),
        "files": [str(path) for path in outputs],
        "has_more": next_page is not None,
        "next_page": next_page,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _render(_parse_args(arguments)))


if __name__ == "__main__":
    raise SystemExit(main())
