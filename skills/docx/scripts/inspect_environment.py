#!/usr/bin/env python3
"""Report installed document tools and exact font-family availability."""

from __future__ import annotations

import importlib.metadata
import shutil

from _docx_common import SkillArgumentParser, run_cli, run_program


def main():
    parser = SkillArgumentParser(description="检查文档依赖和指定字体是否实际安装")
    parser.add_argument("--font", action="append", default=[], help="需精确检查的字体家族，可重复传入")
    args = parser.parse_args()
    tools = {name: shutil.which(name) for name in ("soffice", "pandoc", "pdftoppm", "pdftotext", "typst", "fc-list")}
    packages = {}
    for name in ("python-docx", "lxml", "defusedxml", "Pillow", "pdfplumber", "pypdf", "rapidocr", "onnxruntime"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    fonts = set()
    if tools["fc-list"]:
        result = run_program([tools["fc-list"], "--format", "%{family}\n"], timeout=30)
        fonts = {name.strip() for line in result.stdout.splitlines() for name in line.split(",") if name.strip()}
    lookup = {name.casefold() for name in fonts}
    requested = {name: name.strip().casefold() in lookup for name in args.font}
    return {
        "tools": tools, "packages": packages,
        "missing_tools": [name for name, path in tools.items() if not path],
        "missing_packages": [name for name, version in packages.items() if not version],
        "requested_fonts": requested,
        "missing_fonts": [name for name, installed in requested.items() if not installed],
        "font_inventory_available": bool(tools["fc-list"]),
        "font_families": sorted(fonts),
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
