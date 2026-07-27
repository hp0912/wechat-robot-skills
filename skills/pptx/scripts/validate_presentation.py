#!/usr/bin/env python3

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from _pptx_common import (
    OOXML_PRESENTATION_SUFFIXES,
    P_NS,
    R_NS,
    SkillArgumentParser,
    input_file,
    inspect_archive,
    parse_xml_bytes,
    run_cli,
    run_soffice_convert,
)


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
PLACEHOLDER_RE = re.compile(
    r"\b(?:lorem|ipsum|todo|x{3,})\b|\[insert|this\s+(?:page|slide).+layout",
    re.IGNORECASE,
)
EMU_PER_INCH = 914400


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(description="校验 PowerPoint 的结构、关系和可渲染性。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--original")
    parser.add_argument("--check-render", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    return parser


def _issue(
    code: str,
    message: str,
    *,
    part: Optional[str] = None,
    slide: Optional[int] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if part is not None:
        payload["part"] = part
    if slide is not None:
        payload["slide"] = slide
    return payload


def _owner_part_for_rels(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    path = PurePosixPath(name)
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        raise ValueError(f"关系部件路径无效：{name}")
    owner_name = path.name[: -len(".rels")]
    owner_parent = path.parent.parent
    return (owner_parent / owner_name).as_posix()


def _resolve_relationship_target(owner_part: str, target: str) -> Optional[str]:
    if not target or target.startswith("#"):
        return None
    if target.startswith("/"):
        normalized = posixpath.normpath(target).lstrip("/")
        if normalized == ".." or normalized.startswith("../"):
            return None
        return normalized
    base = posixpath.dirname(owner_part)
    normalized = posixpath.normpath(posixpath.join(base, target))
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized.lstrip("/")


def _validate_relationships(
    archive: zipfile.ZipFile,
    names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, str]]]:
    errors: list[dict[str, Any]] = []
    externals: list[dict[str, Any]] = []
    maps: dict[str, dict[str, str]] = {}
    for name in sorted(item for item in names if item.endswith(".rels")):
        try:
            root = parse_xml_bytes(archive.read(name), label=name)
        except ValueError as exc:
            errors.append(_issue("invalid_relationship_xml", str(exc), part=name))
            continue
        owner_part = _owner_part_for_rels(name)
        relationships: dict[str, str] = {}
        for node in root.findall(f"{{{REL_NS}}}Relationship"):
            relationship_id = node.attrib.get("Id", "")
            target = node.attrib.get("Target", "")
            if not relationship_id or relationship_id in relationships:
                errors.append(
                    _issue(
                        "duplicate_or_missing_relationship_id",
                        f"关系 Id 缺失或重复：{relationship_id!r}",
                        part=name,
                    )
                )
                continue
            if node.attrib.get("TargetMode") == "External":
                externals.append(
                    {
                        "part": name,
                        "id": relationship_id,
                        "type": node.attrib.get("Type", ""),
                        "target": target,
                    }
                )
                relationships[relationship_id] = target
                continue
            resolved = _resolve_relationship_target(owner_part, target)
            if resolved is None:
                errors.append(
                    _issue(
                        "unsafe_relationship_target",
                        f"关系目标不安全：{target}",
                        part=name,
                    )
                )
                continue
            relationships[relationship_id] = resolved
            if resolved not in names:
                errors.append(
                    _issue(
                        "missing_relationship_target",
                        f"关系目标不存在：{resolved}",
                        part=name,
                    )
                )
        maps[owner_part] = relationships
    return errors, externals, maps


def _validate_slide_order(
    archive: zipfile.ZipFile,
    relationship_maps: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[dict[str, Any]] = []
    ordered_parts: list[str] = []
    root = parse_xml_bytes(
        archive.read("ppt/presentation.xml"),
        label="ppt/presentation.xml",
    )
    slide_ids: set[str] = set()
    relationship_ids: set[str] = set()
    presentation_relationships = relationship_maps.get(
        "ppt/presentation.xml",
        {},
    )
    for node in root.findall(f".//{{{P_NS}}}sldId"):
        slide_id = node.attrib.get("id", "")
        relationship_id = node.attrib.get(f"{{{R_NS}}}id", "")
        if not slide_id or slide_id in slide_ids:
            errors.append(
                _issue(
                    "duplicate_or_missing_slide_id",
                    f"页面 id 缺失或重复：{slide_id!r}",
                    part="ppt/presentation.xml",
                )
            )
        slide_ids.add(slide_id)
        if not relationship_id or relationship_id in relationship_ids:
            errors.append(
                _issue(
                    "duplicate_or_missing_slide_relationship",
                    f"页面关系 id 缺失或重复：{relationship_id!r}",
                    part="ppt/presentation.xml",
                )
            )
        relationship_ids.add(relationship_id)
        target = presentation_relationships.get(relationship_id)
        if not target or not target.startswith("ppt/slides/slide"):
            errors.append(
                _issue(
                    "invalid_slide_relationship",
                    f"页面关系 {relationship_id!r} 未指向有效 slide 部件",
                    part="ppt/presentation.xml",
                )
            )
        else:
            ordered_parts.append(target)
    if not ordered_parts:
        errors.append(
            _issue(
                "no_slides",
                "演示文稿不包含页面",
                part="ppt/presentation.xml",
            )
        )
    return errors, ordered_parts


def _validate_charts(
    archive: zipfile.ZipFile,
    names: set[str],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for name in sorted(
        item
        for item in names
        if item.startswith("ppt/charts/chart") and item.endswith(".xml")
    ):
        root = parse_xml_bytes(archive.read(name), label=name)
        declared_axis_ids: set[str] = set()
        for axis_name in ("catAx", "valAx", "dateAx", "serAx"):
            for axis in root.findall(f".//{{{C_NS}}}{axis_name}"):
                node = axis.find(f"{{{C_NS}}}axId")
                if node is not None and node.attrib.get("val"):
                    declared_axis_ids.add(node.attrib["val"])
        referenced_axis_ids = {
            node.attrib["val"]
            for node in root.findall(f".//{{{C_NS}}}axId")
            if node.attrib.get("val")
        }
        # PptxGenJS writes the standard primary series-axis id on ordinary
        # two-dimensional charts even though no serAx element is required.
        # Secondary value/category ids must still have real declarations.
        tolerated_series_axis_ids = {"2094734556"}
        missing_axis_ids = sorted(
            referenced_axis_ids
            - declared_axis_ids
            - tolerated_series_axis_ids
        )
        if declared_axis_ids and missing_axis_ids:
            errors.append(
                _issue(
                    "undeclared_chart_axis",
                    "图表引用了未声明的坐标轴："
                    + "、".join(missing_axis_ids),
                    part=name,
                )
            )
        for bar_chart in root.findall(f".//{{{C_NS}}}barChart"):
            grouping = bar_chart.find(f"{{{C_NS}}}grouping")
            grouping_value = grouping.attrib.get("val") if grouping is not None else ""
            if grouping_value not in {"stacked", "percentStacked"}:
                continue
            invalid_label = bar_chart.find(
                f".//{{{C_NS}}}dLblPos[@val='outEnd']"
            )
            if invalid_label is not None:
                errors.append(
                    _issue(
                        "invalid_stacked_chart_label_position",
                        "堆积柱形或条形图不能使用 outEnd 数据标签位置",
                        part=name,
                    )
                )
    return errors


def _visual_structure(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    from pptx import Presentation

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    presentation = Presentation(str(path))
    slide_width = int(presentation.slide_width)
    slide_height = int(presentation.slide_height)
    tolerance = 2000
    for slide_number, slide in enumerate(presentation.slides, start=1):
        if len(slide.shapes) == 0:
            warnings.append(
                _issue("empty_slide", "页面没有可见形状", slide=slide_number)
            )
        for shape in slide.shapes:
            left = int(shape.left)
            top = int(shape.top)
            right = left + int(shape.width)
            bottom = top + int(shape.height)
            if (
                left < -tolerance
                or top < -tolerance
                or right > slide_width + tolerance
                or bottom > slide_height + tolerance
            ):
                errors.append(
                    _issue(
                        "shape_out_of_bounds",
                        f"形状 {shape.name!r} 超出页面边界",
                        slide=slide_number,
                    )
                )
            text = getattr(shape, "text", "")
            if text and PLACEHOLDER_RE.search(text):
                warnings.append(
                    _issue(
                        "placeholder_text",
                        f"形状 {shape.name!r} 可能残留占位文本",
                        slide=slide_number,
                    )
                )
    return errors, warnings, len(presentation.slides)


def _validate_once(path: Path, *, check_render: bool, timeout: int) -> dict[str, Any]:
    archive_info = inspect_archive(path)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if archive_info["missing_required_parts"]:
        errors.append(
            _issue(
                "missing_required_parts",
                "缺少必要部件："
                + "、".join(archive_info["missing_required_parts"]),
            )
        )
        return {
            "errors": errors,
            "warnings": warnings,
            "archive": archive_info,
            "slide_count": 0,
            "external_relationships": [],
        }
    if archive_info["duplicate_members"]:
        errors.append(
            _issue(
                "duplicate_archive_members",
                "压缩包存在重复成员："
                + "、".join(archive_info["duplicate_members"][:20]),
            )
        )

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for name in sorted(
            item for item in names if item.endswith((".xml", ".rels"))
        ):
            try:
                parse_xml_bytes(archive.read(name), label=name)
            except ValueError as exc:
                errors.append(_issue("invalid_xml", str(exc), part=name))
        relationship_errors, externals, maps = _validate_relationships(
            archive,
            names,
        )
        errors.extend(relationship_errors)
        slide_errors, ordered_parts = _validate_slide_order(archive, maps)
        errors.extend(slide_errors)
        orphaned_slides = sorted(
            name
            for name in names
            if name.startswith("ppt/slides/slide")
            and name.endswith(".xml")
            and name not in set(ordered_parts)
        )
        if orphaned_slides:
            warnings.append(
                _issue(
                    "orphaned_slide_parts",
                    "存在未被 presentation.xml 引用的页面部件："
                    + "、".join(orphaned_slides[:20]),
                )
            )
        errors.extend(_validate_charts(archive, names))

    try:
        shape_errors, shape_warnings, slide_count = _visual_structure(path)
        errors.extend(shape_errors)
        warnings.extend(shape_warnings)
    except Exception as exc:
        errors.append(
            _issue(
                "python_pptx_open_failed",
                f"python-pptx 无法打开演示文稿：{exc}",
            )
        )
        slide_count = 0

    render_result: Optional[dict[str, Any]] = None
    if check_render and not any(
        item["code"] in {"missing_required_parts", "invalid_xml"}
        for item in errors
    ):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory(prefix="pptx-validate-render-") as temp_name:
            temp_dir = Path(temp_name)
            staged = temp_dir / f"presentation{path.suffix.lower()}"
            shutil.copy2(path, staged)
            pdf, office_output = run_soffice_convert(
                staged,
                target_format="pdf",
                output_dir=temp_dir / "pdf",
                timeout=timeout,
            )
            pdf_pages = len(PdfReader(str(pdf)).pages)
            render_result = {
                "pdf_pages": pdf_pages,
                "office_stdout": office_output["stdout"],
                "office_stderr": office_output["stderr"],
            }
            if pdf_pages != slide_count:
                errors.append(
                    _issue(
                        "render_page_count_mismatch",
                        f"结构页数为 {slide_count}，渲染页数为 {pdf_pages}",
                    )
                )
    return {
        "errors": errors,
        "warnings": warnings,
        "archive": archive_info,
        "slide_count": slide_count,
        "external_relationships": externals,
        "render": render_result,
    }


def _signature(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (
        issue.get("code"),
        issue.get("part"),
        issue.get("slide"),
        issue.get("message"),
    )


def main() -> dict[str, Any]:
    args = build_parser().parse_args()
    source = input_file(args.input, OOXML_PRESENTATION_SUFFIXES)
    result = _validate_once(
        source,
        check_render=args.check_render,
        timeout=args.timeout,
    )
    baseline_count = 0
    if args.original:
        original = input_file(args.original, OOXML_PRESENTATION_SUFFIXES)
        baseline = _validate_once(
            original,
            check_render=False,
            timeout=args.timeout,
        )
        baseline_signatures = {
            _signature(item)
            for item in baseline["errors"]
            if item["code"]
            in {
                "invalid_xml",
                "shape_out_of_bounds",
                "python_pptx_open_failed",
            }
        }
        before = len(result["errors"])
        result["errors"] = [
            item
            for item in result["errors"]
            if _signature(item) not in baseline_signatures
        ]
        baseline_count = before - len(result["errors"])
        result["original"] = str(original)
    status = "valid" if not result["errors"] else "invalid"
    return {
        "source": str(source),
        "status": status,
        "issue_count": len(result["errors"]),
        "warning_count": len(result["warnings"]),
        "baselined_issue_count": baseline_count,
        **result,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
