#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Any

from _document_builder import (
    add_blocks,
    apply_core_properties,
    apply_document_defaults,
    apply_header_footer,
    apply_named_styles,
    apply_page_settings,
    expect_list,
    set_update_fields,
)
from _docx_common import (
    SkillArgumentParser,
    inspect_archive,
    load_json_argument,
    output_file,
    publish_file,
    run_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="依据受控 JSON 说明创建专业 Word DOCX 文档。"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--spec", help="内联 JSON 文档说明")
    parser.add_argument("--spec-file", help="JSON 文档说明文件")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    destination = output_file(
        args.output,
        {".docx"},
        overwrite=args.overwrite,
    )
    spec = load_json_argument(args.spec, args.spec_file, label="文档说明")
    allowed = {
        "properties",
        "page",
        "default_font",
        "styles",
        "header",
        "footer",
        "blocks",
    }
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(f"文档说明包含未知顶层字段：{sorted(unknown)}")
    blocks = expect_list(spec.get("blocks"), "blocks")
    if not blocks:
        raise ValueError("blocks 不能为空")

    document = Document()
    apply_document_defaults(document, spec.get("default_font", {}))
    if "styles" in spec:
        apply_named_styles(document, spec["styles"])
    if "properties" in spec:
        apply_core_properties(document, spec["properties"])
    apply_page_settings(document.sections[0], spec.get("page", {}))
    counts = add_blocks(document, document, blocks)
    apply_header_footer(
        document,
        spec.get("header"),
        spec.get("footer"),
    )
    set_update_fields(document)

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".docx",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        document.save(temp_path)
        archive = inspect_archive(temp_path)
        if archive["missing_required_parts"]:
            raise ValueError(
                "生成文档缺少必要部件："
                + "、".join(archive["missing_required_parts"])
            )
        Document(str(temp_path))
        publish_file(temp_path, destination, overwrite=args.overwrite)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "path": str(destination),
        "properties": {
            "title": document.core_properties.title,
            "author": document.core_properties.author,
            "subject": document.core_properties.subject,
        },
        "section_count": len(document.sections),
        "paragraph_count": len(document.paragraphs),
        "table_count": len(document.tables),
        "counts": counts,
        "archive": archive,
        "requires_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
