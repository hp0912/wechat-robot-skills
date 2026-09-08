#!/usr/bin/env python3
"""Read supporting PDF/text/HTML material through a bounded JSON interface."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from _docx_common import SkillArgumentParser, input_file, run_cli


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def read_text(source: Path) -> str:
    raw = source.read_bytes()
    encodings = ["utf-16"] if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030", "big5"]
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法可靠识别文本编码，请提供 UTF-8 文本")
    if source.suffix.lower() in {".html", ".htm"}:
        parser = HTMLText()
        parser.feed(text)
        text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    return text


def read_pdf_page(page: Any, columns: str) -> tuple[str, int, int]:
    tables = page.find_tables()
    boxes = [table.bbox for table in tables]

    def outside_tables(obj):
        x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
        y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
        return not any(a <= x <= c and b <= y <= d for a, b, c, d in boxes)

    body = page.filter(outside_tables)
    midpoint = (page.bbox[0] + page.bbox[2]) / 2
    centers = [(char["x0"] + char["x1"]) / 2 for char in body.chars if char.get("text", "").strip()]
    left_count = sum(x < midpoint for x in centers)
    right_count = len(centers) - left_count
    # Require a genuine central gutter. A balanced full-width paragraph is not two columns.
    gutter = page.width * 0.02
    detected = (min(left_count, right_count) >= 8 and not any(abs(x - midpoint) < gutter for x in centers))
    count = (2 if detected else 1) if columns == "auto" else int(columns)
    if count == 2:
        # Partition by character centre: never discard text in a cropped central gap.
        parts = [body.filter(lambda obj: (obj.get("x0", 0) + obj.get("x1", 0)) / 2 < midpoint).extract_text() or "",
                 body.filter(lambda obj: (obj.get("x0", 0) + obj.get("x1", 0)) / 2 >= midpoint).extract_text() or ""]
    else:
        parts = [body.extract_text() or ""]
    for index, table in enumerate(tables, 1):
        rows = [" | ".join(str(cell or "").replace("\n", " ") for cell in row) for row in table.extract()]
        parts.append(f"[表格 {index}]\n" + "\n".join(rows))
    return "\n\n".join(part.strip() for part in parts if part.strip()), count, len(tables)


def main() -> dict[str, Any]:
    parser = SkillArgumentParser(description="提取 Word 任务的 PDF、TXT、Markdown 或 HTML 资料")
    parser.add_argument("--input", required=True)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--columns", choices=["auto", "1", "2"], default="auto")
    args = parser.parse_args()
    source = input_file(args.input, {".pdf", ".txt", ".md", ".html", ".htm"})
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("输入资料不能超过 25 MiB")
    if not 256 <= args.max_chars <= 12000 or args.start_offset < 0 or args.page < 1:
        raise ValueError("max-chars 必须为 256–12000，start-offset 不小于 0，page 从 1 开始")
    page_count, columns, table_count, has_images = 1, 1, 0, False
    if source.suffix.lower() == ".pdf":
        import pdfplumber
        from pypdf import PdfReader

        if PdfReader(str(source)).is_encrypted:
            raise ValueError("请提供已解密的 PDF 副本")
        with pdfplumber.open(source) as pdf:
            page_count = len(pdf.pages)
            if args.page > page_count:
                raise ValueError("page 超出 PDF 页数")
            page = pdf.pages[args.page - 1]
            text, columns, table_count = read_pdf_page(page, args.columns)
            has_images = bool(page.images)
    else:
        if args.page != 1:
            raise ValueError("文本资料只有一个逻辑页，请使用 start-offset 续读")
        text = read_text(source)
    if args.start_offset > len(text):
        raise ValueError("start-offset 超出当前页文本长度")
    end = min(args.start_offset + args.max_chars, len(text))
    more_text = end < len(text)
    usable = bool(text.strip()) and text.count("\ufffd") / max(1, len(text)) < 0.02
    return {
        "path": str(source), "page": args.page, "page_count": page_count,
        "text": text[args.start_offset:end], "start_offset": args.start_offset,
        "columns": columns, "table_count": table_count, "has_images": has_images,
        "usable_for_summary": usable, "needs_ocr": source.suffix.lower() == ".pdf" and not usable,
        "has_more": more_text or args.page < page_count,
        "next_page": args.page if more_text else (args.page + 1 if args.page < page_count else None),
        "next_offset": end if more_text else 0,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
