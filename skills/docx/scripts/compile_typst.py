#!/usr/bin/env python3
"""Compile task-local Typst source to a checked PDF via a fixed command."""

from __future__ import annotations

import os
import json
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

from _docx_common import (SkillArgumentParser, WORD_OUTPUT_ROOT, find_program, input_file, load_json_argument,
                          output_file, publish_file, run_cli, run_program)


def resume_source(spec, directory):
    if set(spec) - {"name", "contact", "summary", "sections"}:
        raise ValueError("简历仅支持 name、contact、summary、sections")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        raise ValueError("name 必须是非空文本")
    spec.setdefault("contact", [])
    spec.setdefault("summary", "")
    if not isinstance(spec["contact"], list) or not all(isinstance(x, str) for x in spec["contact"]):
        raise ValueError("contact 必须是文本数组")
    if not isinstance(spec["summary"], str) or not isinstance(spec.get("sections"), list):
        raise ValueError("summary 必须是文本，sections 必须是数组")
    for section in spec["sections"]:
        if (not isinstance(section, dict) or set(section) != {"title", "items"}
                or not isinstance(section["title"], str) or not isinstance(section["items"], list)
                or not all(isinstance(item, str) for item in section["items"])):
            raise ValueError("sections[] 必须包含文本 title 和文本数组 items")
    (directory / "resume.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    source = directory / "resume.typ"
    shutil.copy2(Path(__file__).resolve().parent.parent / "assets/resume.typ", source)
    return source


def main():
    from pypdf import PdfReader

    parser = SkillArgumentParser(description="把任务目录中的 Typst 文档编译为 PDF")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input")
    group.add_argument("--template", choices=["resume"])
    parser.add_argument("--spec")
    parser.add_argument("--spec-file")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.template:
            task_root = WORD_OUTPUT_ROOT / "tmp"
            task_root.mkdir(parents=True, exist_ok=True)
            task_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="typst-resume-", dir=task_root)))
            source = resume_source(load_json_argument(args.spec, args.spec_file, label="简历说明"), task_dir)
        else:
            if args.spec is not None or args.spec_file is not None:
                raise ValueError("spec/spec-file 仅用于 --template resume")
            source = input_file(args.input, {".typ"})
            try:
                source.relative_to((WORD_OUTPUT_ROOT / "tmp").resolve())
            except ValueError as exc:
                raise ValueError("Typst 源文件及其素材必须放在 /usr/local/src/word/tmp/<任务名>/") from exc
        if source.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Typst 源文件不能超过 2 MiB")
        destination = output_file(args.output, {".pdf"}, overwrite=args.overwrite)
        descriptor, name = tempfile.mkstemp(prefix=".typst-", suffix=".pdf", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            result = run_program([find_program("typst"), "compile", "--root", str(source.parent),
                                  "--diagnostic-format", "short", str(source), str(temporary)],
                                 timeout=args.timeout, cwd=source.parent)
            reader = PdfReader(str(temporary))
            page_count = len(reader.pages)
            if reader.is_encrypted or page_count < 1:
                raise ValueError("Typst 未生成可读取的 PDF 页面")
            publish_file(temporary, destination, overwrite=args.overwrite)
        finally:
            temporary.unlink(missing_ok=True)
    return {"path": str(destination), "source": args.input, "template": args.template, "page_count": page_count,
            "warnings": result.stderr[-8000:], "requires_visual_review": True}


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
