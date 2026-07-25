#!/usr/bin/env python3

from __future__ import annotations

import argparse
import posixpath
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from urllib.parse import unquote

from _docx_common import (
    CONTENT_TYPES_NS,
    DOCX_INPUT_SUFFIXES,
    REL_NS,
    SkillArgumentParser,
    W_NS,
    input_file,
    inspect_archive,
    parse_xml_bytes,
    run_cli,
    run_soffice_convert,
)


def _parse_xml_parts(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    roots: dict[str, Any] = {}
    issues: list[dict[str, str]] = []
    for name in archive.namelist():
        if not name.endswith((".xml", ".rels")):
            continue
        try:
            roots[name] = parse_xml_bytes(archive.read(name), label=name)
        except ValueError as exc:
            issues.append(
                {
                    "type": "xml_parse_error",
                    "part": name,
                    "message": str(exc),
                }
            )
    return roots, issues


def _relationship_source(rels_name: str) -> PurePosixPath:
    path = PurePosixPath(rels_name)
    if rels_name == "_rels/.rels":
        return PurePosixPath("")
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        return PurePosixPath("")
    source_name = path.name[: -len(".rels")]
    return path.parent.parent / source_name


def _resolve_target(source_part: PurePosixPath, target: str) -> Optional[str]:
    if not target or target.startswith("#"):
        return None
    target = unquote(target).replace("\\", "/")
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        base = str(source_part.parent) if str(source_part) else ""
        normalized = posixpath.normpath(posixpath.join(base, target))
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("关系目标逃逸 OOXML 包根目录")
    return normalized.lstrip("./")


def _validate_relationships(
    roots: dict[str, Any],
    member_names: set[str],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    relationship_tag = f"{{{REL_NS}}}Relationship"
    for name, root in roots.items():
        if not name.endswith(".rels"):
            continue
        source = _relationship_source(name)
        seen_ids: set[str] = set()
        for relationship in root.findall(relationship_tag):
            relationship_id = relationship.attrib.get("Id", "")
            if not relationship_id:
                issues.append(
                    {
                        "type": "relationship_missing_id",
                        "part": name,
                        "message": "Relationship 缺少 Id",
                    }
                )
            elif relationship_id in seen_ids:
                issues.append(
                    {
                        "type": "duplicate_relationship_id",
                        "part": name,
                        "message": relationship_id,
                    }
                )
            seen_ids.add(relationship_id)
            if relationship.attrib.get("TargetMode") == "External":
                continue
            try:
                target = _resolve_target(
                    source,
                    relationship.attrib.get("Target", ""),
                )
            except ValueError as exc:
                issues.append(
                    {
                        "type": "unsafe_relationship_target",
                        "part": name,
                        "message": str(exc),
                    }
                )
                continue
            if target and target not in member_names:
                issues.append(
                    {
                        "type": "missing_relationship_target",
                        "part": name,
                        "message": target,
                    }
                )
    return issues


def _validate_content_types(
    roots: dict[str, Any],
    member_names: set[str],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    root = roots.get("[Content_Types].xml")
    if root is None:
        return issues
    override_tag = f"{{{CONTENT_TYPES_NS}}}Override"
    seen_parts: set[str] = set()
    for override in root.findall(override_tag):
        part = override.attrib.get("PartName", "").lstrip("/")
        if not part:
            issues.append(
                {
                    "type": "content_type_missing_part",
                    "part": "[Content_Types].xml",
                    "message": "Override 缺少 PartName",
                }
            )
            continue
        if part in seen_parts:
            issues.append(
                {
                    "type": "duplicate_content_type_override",
                    "part": "[Content_Types].xml",
                    "message": part,
                }
            )
        seen_parts.add(part)
        if part not in member_names:
            issues.append(
                {
                    "type": "missing_content_type_part",
                    "part": "[Content_Types].xml",
                    "message": part,
                }
            )
    return issues


def _validate_comments(roots: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    comments_root = roots.get("word/comments.xml")
    document_root = roots.get("word/document.xml")
    if document_root is None:
        return issues
    comment_ids: set[str] = set()
    if comments_root is not None:
        for comment in comments_root.iter(f"{{{W_NS}}}comment"):
            comment_id = comment.attrib.get(f"{{{W_NS}}}id")
            if comment_id:
                comment_ids.add(comment_id)
    starts: list[str] = []
    ends: list[str] = []
    references: list[str] = []
    for element in document_root.iter():
        comment_id = element.attrib.get(f"{{{W_NS}}}id")
        if element.tag == f"{{{W_NS}}}commentRangeStart" and comment_id:
            starts.append(comment_id)
        elif element.tag == f"{{{W_NS}}}commentRangeEnd" and comment_id:
            ends.append(comment_id)
        elif element.tag == f"{{{W_NS}}}commentReference" and comment_id:
            references.append(comment_id)
    for comment_id in sorted(set(starts + ends + references)):
        if comment_id not in comment_ids:
            issues.append(
                {
                    "type": "undefined_comment_reference",
                    "part": "word/document.xml",
                    "message": comment_id,
                }
            )
        if starts.count(comment_id) != ends.count(comment_id):
            issues.append(
                {
                    "type": "unbalanced_comment_range",
                    "part": "word/document.xml",
                    "message": comment_id,
                }
            )
        if references.count(comment_id) == 0:
            issues.append(
                {
                    "type": "missing_comment_reference",
                    "part": "word/document.xml",
                    "message": comment_id,
                }
            )
    return issues


def _revision_metadata(roots: dict[str, Any]) -> dict[str, Any]:
    revision_tags = {
        f"{{{W_NS}}}ins",
        f"{{{W_NS}}}del",
        f"{{{W_NS}}}moveFrom",
        f"{{{W_NS}}}moveTo",
    }
    total = 0
    missing_author = 0
    missing_date = 0
    authors: set[str] = set()
    for name, root in roots.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        for element in root.iter():
            if element.tag not in revision_tags:
                continue
            total += 1
            author = element.attrib.get(f"{{{W_NS}}}author")
            date = element.attrib.get(f"{{{W_NS}}}date")
            if author:
                authors.add(author)
            else:
                missing_author += 1
            if not date:
                missing_date += 1
    return {
        "total": total,
        "authors": sorted(authors),
        "missing_author_count": missing_author,
        "missing_date_count": missing_date,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="校验 DOCX 压缩包、XML、关系、批注和 LibreOffice 可打开性。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--check-convert",
        action="store_true",
        help="额外用 LibreOffice 转 PDF，验证文档可渲染",
    )
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    source = input_file(args.input, DOCX_INPUT_SUFFIXES)
    archive_info = inspect_archive(source)
    issues: list[dict[str, str]] = []
    for part in archive_info["missing_required_parts"]:
        issues.append(
            {
                "type": "missing_required_part",
                "part": part,
                "message": part,
            }
        )
    for part in archive_info["duplicate_members"]:
        issues.append(
            {
                "type": "duplicate_zip_member",
                "part": part,
                "message": part,
            }
        )

    with zipfile.ZipFile(source) as archive:
        member_names = set(archive.namelist())
        roots, xml_issues = _parse_xml_parts(archive)
    issues.extend(xml_issues)
    issues.extend(_validate_relationships(roots, member_names))
    issues.extend(_validate_content_types(roots, member_names))
    issues.extend(_validate_comments(roots))
    revisions = _revision_metadata(roots)
    if revisions["missing_author_count"]:
        issues.append(
            {
                "type": "revision_missing_author",
                "part": "word/*.xml",
                "message": str(revisions["missing_author_count"]),
            }
        )
    if revisions["missing_date_count"]:
        issues.append(
            {
                "type": "revision_missing_date",
                "part": "word/*.xml",
                "message": str(revisions["missing_date_count"]),
            }
        )

    document_summary: dict[str, Any] = {}
    try:
        document = Document(str(source))
        document_summary = {
            "paragraph_count": len(document.paragraphs),
            "table_count": len(document.tables),
            "section_count": len(document.sections),
            "comment_count": len(document.comments),
        }
    except Exception as exc:
        issues.append(
            {
                "type": "python_docx_open_error",
                "part": str(source),
                "message": str(exc),
            }
        )

    render_check: Optional[dict[str, Any]] = None
    if args.check_convert:
        try:
            from pypdf import PdfReader

            with tempfile.TemporaryDirectory(prefix="docx-validate-") as temp_name:
                temp_dir = Path(temp_name)
                staged = temp_dir / "document.docx"
                shutil.copy2(source, staged)
                pdf, office_output = run_soffice_convert(
                    staged,
                    target_format="pdf",
                    output_dir=temp_dir / "pdf",
                    timeout=args.timeout,
                )
                page_count = len(PdfReader(str(pdf)).pages)
                if page_count < 1:
                    raise ValueError("转换结果没有页面")
                render_check = {
                    "success": True,
                    "page_count": page_count,
                    "office_stdout": office_output["stdout"],
                    "office_stderr": office_output["stderr"],
                }
        except Exception as exc:
            issues.append(
                {
                    "type": "libreoffice_render_error",
                    "part": str(source),
                    "message": str(exc),
                }
            )
            render_check = {"success": False, "error": str(exc)}

    return {
        "path": str(source),
        "status": "valid" if not issues else "invalid",
        "issue_count": len(issues),
        "issues": issues[:200],
        "issues_truncated": max(0, len(issues) - 200),
        "archive": archive_info,
        "document": document_summary,
        "tracked_changes": revisions,
        "render_check": render_check,
        "requires_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
