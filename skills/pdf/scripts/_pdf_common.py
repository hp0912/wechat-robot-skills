#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, NoReturn, Optional


def quiet_pdf_library_logs() -> None:
    """Keep third-party recovery warnings out of the JSON tool response."""
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pypdf").setLevel(logging.ERROR)


quiet_pdf_library_logs()


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def failure_message(exc: Exception) -> str:
    if isinstance(exc, FileExistsError):
        return str(exc)
    if isinstance(exc, PermissionError):
        return "文件处理失败：没有目标路径的访问权限"
    if isinstance(exc, OSError):
        return f"文件处理失败：{exc}"
    return str(exc)


def run_cli(action: Callable[[], dict[str, Any]]) -> int:
    try:
        result = action()
    except Exception as exc:
        emit({"ok": False, "error": failure_message(exc)})
        return 1
    emit({"ok": True, **result})
    return 0


def input_pdf(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在：{path}")
    if not path.is_file():
        raise ValueError(f"PDF 路径不是文件：{path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("PDF 输入路径必须以 .pdf 结尾")
    if path.stat().st_size <= 0:
        raise ValueError("PDF 文件为空")
    return path


def output_pdf(value: str, overwrite: bool) -> Path:
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() != ".pdf":
        raise ValueError("PDF 输出路径必须以 .pdf 结尾")
    if path.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def output_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"输出路径不是目录：{path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_temp_pdf(output: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".part.pdf",
        dir=str(output.parent),
    )
    os.close(descriptor)
    return Path(name)


def publish_temp_file(temp_path: Path, output: Path, overwrite: bool) -> None:
    if overwrite:
        os.replace(temp_path, output)
        return
    try:
        os.link(temp_path, output)
    except FileExistsError as exc:
        raise FileExistsError(f"目标文件已存在：{output}") from exc
    temp_path.unlink()


def parse_page_spec(value: Optional[str], page_count: int) -> list[int]:
    if page_count < 1:
        return []
    if value is None or not value.strip():
        return list(range(1, page_count + 1))

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
        raise ValueError("页码范围不能为空")
    return sorted(pages)


def selected_page_window(
    page_count: int,
    start_page: int,
    end_page: Optional[int],
    max_pages: int,
) -> tuple[int, int, Optional[int]]:
    if page_count < 1:
        raise ValueError("PDF 没有可处理的页面")
    if start_page < 1 or start_page > page_count:
        raise ValueError(f"start-page 必须在 1 到 {page_count} 之间")
    if max_pages < 1:
        raise ValueError("max-pages 必须大于 0")

    requested_end = page_count if end_page is None else end_page
    if requested_end < start_page or requested_end > page_count:
        raise ValueError(
            f"end-page 必须在 start-page 到 {page_count} 之间"
        )

    actual_end = min(requested_end, start_page + max_pages - 1)
    next_page = actual_end + 1 if actual_end < requested_end else None
    return start_page, actual_end, next_page
