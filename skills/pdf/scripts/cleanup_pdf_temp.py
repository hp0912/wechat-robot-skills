#!/usr/bin/env python3

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from _pdf_common import PDF_OUTPUT_ROOT, SkillArgumentParser, run_cli


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="清理当前 PDF 任务的临时目录")
    parser.add_argument(
        "--task-dir",
        required=True,
        help=f"仅允许删除 {PDF_OUTPUT_ROOT}/tmp/ 下某个具体任务目录",
    )
    return parser.parse_args(argv)


def _cleanup(value: str) -> dict[str, Any]:
    allowed_root = (PDF_OUTPUT_ROOT / "tmp").resolve()
    candidate = Path(value).expanduser()
    target = (
        candidate.resolve()
        if candidate.is_absolute()
        else (allowed_root / candidate).resolve()
    )
    try:
        relative = target.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(f"只能清理 {allowed_root} 下的任务目录") from exc
    if not relative.parts:
        raise ValueError(f"不能删除 {allowed_root} 根目录")
    if len(relative.parts) != 1:
        raise ValueError(
            f"task-dir 必须直接指向 {allowed_root} 下的单个任务目录"
        )
    if not target.is_dir():
        raise FileNotFoundError(f"任务临时目录不存在：{target}")
    shutil.rmtree(target)
    return {"removed": str(target)}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(lambda: _cleanup(_parse_args(arguments).task_dir))


if __name__ == "__main__":
    raise SystemExit(main())
