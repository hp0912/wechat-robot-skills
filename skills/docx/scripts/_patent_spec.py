"""Map patent content to the shared, validated DOCX creation pipeline."""

from __future__ import annotations

from typing import Any

from _document_builder import expect_list, expect_object


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空文本")
    return value


def build_patent_spec(data: dict[str, Any]) -> dict[str, Any]:
    unknown = set(data) - {"claims", "specification", "abstract", "properties"}
    if unknown:
        raise ValueError(f"专利说明包含未知字段：{sorted(unknown)}")
    blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    margin = 2.5 / 2.54
    page = {"size": "A4", "margins": dict.fromkeys(("top", "bottom", "left", "right"), margin)}

    def start_section(title: str) -> None:
        if sections:
            blocks.append({"type": "section_break", **page})
        sections.append({
            "index": len(sections),
            "header": {"text": title, "alignment": "center"},
            "footer": {"text": "", "alignment": "center", "page_number": True,
                       "page_number_prefix": "第 ", "page_number_suffix": " 页"},
            "page_number_start": 1,
        })
        blocks.append({"type": "heading", "level": 1, "text": title, "alignment": "center"})

    claims = expect_list(data.get("claims", []), "claims")
    if claims:
        start_section("权利要求书")
        items = []
        for index, raw in enumerate(claims, 1):
            claim = expect_object(raw, "claims[]")
            if set(claim) - {"number", "text", "dependent"}:
                raise ValueError("claims[] 仅支持 number、text、dependent")
            if claim.get("number") != index:
                raise ValueError("claims[].number 必须从 1 开始连续编号")
            items.append({"text": _text(claim.get("text"), "claims[].text"),
                          "alignment": "justify", "keep_together": True})
        blocks.append({"type": "numbered_list", "items": items})

    specification = expect_object(data.get("specification", {}), "specification")
    titles = [("field", "技术领域"), ("background", "背景技术"), ("summary", "发明内容"),
              ("drawings", "附图说明"), ("detailed", "具体实施方式")]
    unknown = set(specification) - {key for key, _ in titles}
    if unknown:
        raise ValueError(f"specification 包含未知字段：{sorted(unknown)}")
    if specification:
        start_section("说明书")
        for key, title in titles:
            if key not in specification:
                continue
            value = specification[key]
            paragraphs = value if isinstance(value, list) else [value]
            if not paragraphs:
                raise ValueError(f"specification.{key} 不能为空")
            blocks.append({"type": "heading", "level": 2, "text": title})
            blocks.extend({"type": "paragraph", "text": _text(text, f"specification.{key}"),
                           "alignment": "justify", "first_line_indent": 24 / 72}
                          for text in paragraphs)
    if "abstract" in data:
        abstract = _text(data["abstract"], "abstract")
        start_section("摘要")
        blocks.append({"type": "paragraph", "text": abstract, "alignment": "justify",
                       "first_line_indent": 24 / 72})
    if not blocks:
        raise ValueError("专利说明至少需要非空 claims、specification 或 abstract")
    return {
        "properties": expect_object(data.get("properties", {}), "properties"),
        "page": page,
        "default_font": {"name": "Liberation Serif", "east_asia": "Noto Serif CJK SC",
                         "size": 12, "line_spacing": 1.5},
        "styles": {
            "Heading 1": {"font": "Liberation Sans", "east_asia_font": "Noto Sans CJK SC",
                          "size": 16, "bold": True, "color": "000000"},
            "Heading 2": {"font": "Liberation Sans", "east_asia_font": "Noto Sans CJK SC",
                          "size": 14, "bold": True, "color": "000000"},
        },
        "blocks": blocks,
        "sections": sections,
    }
