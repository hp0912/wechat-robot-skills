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
    load_json_argument,
    output_file,
    publish_file,
    run_cli,
    run_program,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="按受控 JSON 说明创建 PowerPoint 演示文稿。")
    parser.add_argument("--output", required=True)
    parser.add_argument("--spec")
    parser.add_argument("--spec-file")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _validate_spec(spec: dict[str, Any]) -> None:
    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("spec.slides 必须是非空数组")
    if len(slides) > 300:
        raise ValueError("单个演示文稿最多支持 300 页")
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise ValueError(f"spec.slides[{index}] 必须是对象")
        elements = slide.get("elements", [])
        if not isinstance(elements, list):
            raise ValueError(f"spec.slides[{index}].elements 必须是数组")
        if len(elements) > 1000:
            raise ValueError(f"spec.slides[{index}].elements 超过 1000 项限制")


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    destination = output_file(args.output, {".pptx"}, overwrite=args.overwrite)
    spec = load_json_argument(args.spec, args.spec_file, label="演示文稿说明")
    _validate_spec(spec)
    builder = Path(__file__).with_name("_presentation_builder.js").resolve()
    if not builder.is_file():
        raise FileNotFoundError(f"内部构建器不存在：{builder}")

    with tempfile.TemporaryDirectory(prefix="pptx-create-") as temp_name:
        temp_dir = Path(temp_name)
        spec_path = temp_dir / "spec.json"
        staged_output = temp_dir / "presentation.pptx"
        spec_path.write_text(
            json.dumps(spec, ensure_ascii=False),
            encoding="utf-8",
        )
        completed = run_program(
            [
                find_program("node"),
                str(builder),
                "--spec",
                str(spec_path),
                "--output",
                str(staged_output),
            ],
            timeout=args.timeout,
            cwd=Path.cwd(),
            env=os.environ.copy(),
        )
        if not staged_output.is_file() or staged_output.stat().st_size <= 0:
            raise RuntimeError("PptxGenJS 未生成输出文件")
        try:
            builder_result = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("内部构建器返回了无效结果") from exc
        publish_file(staged_output, destination, overwrite=args.overwrite)

    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        **builder_result,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
