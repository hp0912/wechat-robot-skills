#!/usr/bin/env python3

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

from _pdf_common import SkillArgumentParser, input_pdf, run_cli


def _parse_args(argv: list[str]):
    parser = SkillArgumentParser(description="检查 PDF 元数据和页面结构")
    parser.add_argument("--input", required=True, help="本地 PDF 路径")
    return parser.parse_args(argv)


def _inspect(path) -> dict[str, Any]:
    from pypdf import PdfReader

    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        encrypted = bool(reader.is_encrypted)
        metadata = {
            str(key).lstrip("/"): str(value)
            for key, value in (reader.metadata or {}).items()
            if value is not None
        }
        if encrypted:
            return {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "encrypted": True,
                "page_count": None,
                "metadata": metadata,
                "page_layouts": [],
                "form_field_count": None,
            }

        layouts: Counter[tuple[float, float, int]] = Counter()
        for page in reader.pages:
            width = round(float(page.mediabox.width), 2)
            height = round(float(page.mediabox.height), 2)
            rotation = int(page.get("/Rotate", 0) or 0) % 360
            layouts[(width, height, rotation)] += 1

        try:
            fields = reader.get_fields() or {}
            form_field_count = len(fields)
        except Exception:
            form_field_count = 0

        return {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "encrypted": False,
            "page_count": len(reader.pages),
            "metadata": metadata,
            "page_layouts": [
                {
                    "width_points": width,
                    "height_points": height,
                    "rotation": rotation,
                    "count": count,
                }
                for (width, height, rotation), count in sorted(layouts.items())
            ],
            "form_field_count": form_field_count,
        }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return run_cli(
        lambda: _inspect(input_pdf(_parse_args(arguments).input))
    )


if __name__ == "__main__":
    raise SystemExit(main())
