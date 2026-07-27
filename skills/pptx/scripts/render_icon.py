#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from _pptx_common import (
    SkillArgumentParser,
    find_program,
    output_file,
    publish_file,
    run_cli,
    run_program,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="把 React Icons 图标渲染为透明 PNG。")
    parser.add_argument("--library", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--color", default="111827")
    parser.add_argument("--background")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--title")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    destination = output_file(args.output, {".png"}, overwrite=args.overwrite)
    renderer = Path(__file__).with_name("_icon_renderer.js").resolve()
    if not renderer.is_file():
        raise FileNotFoundError(f"内部图标渲染器不存在：{renderer}")
    spec = {
        "library": args.library,
        "name": args.name,
        "color": args.color,
        "background": args.background,
        "size": args.size,
        "title": args.title,
    }
    with tempfile.TemporaryDirectory(prefix="pptx-icon-") as temp_name:
        temp_dir = Path(temp_name)
        spec_path = temp_dir / "spec.json"
        staged = temp_dir / "icon.png"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        completed = run_program(
            [
                find_program("node"),
                str(renderer),
                "--spec",
                str(spec_path),
                "--output",
                str(staged),
            ],
            timeout=args.timeout,
            cwd=Path.cwd(),
            env=os.environ.copy(),
        )
        try:
            result = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("内部图标渲染器返回了无效结果") from exc
        publish_file(staged, destination, overwrite=args.overwrite)
    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        **result,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
