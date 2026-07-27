#!/usr/bin/env python3

from __future__ import annotations

import argparse
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from _pptx_common import (
    P_NS,
    R_NS,
    REL_NS,
    SkillArgumentParser,
    input_file,
    inspect_archive,
    output_file,
    publish_file,
    run_cli,
)


CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
SLIDE_REL_TYPE_SUFFIX = "/slide"
SLIDE_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "presentationml.slide+xml"
)
DROP_RELATIONSHIP_SUFFIXES = ("/notesSlide", "/comments", "/comment")


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="安全复制现有 PPTX 页面并更新 OOXML 包关系。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--slide", type=int, required=True)
    parser.add_argument("--after", type=int)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _parse_xml(payload: bytes, label: str) -> Any:
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=False,
        remove_blank_text=False,
    )
    try:
        return etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"{label} 解析失败：{exc}") from exc


def _serialize_xml(root: Any) -> bytes:
    from lxml import etree

    return etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )


def _next_relationship_id(existing: set[str]) -> str:
    number = 1
    while f"rId{number}" in existing:
        number += 1
    return f"rId{number}"


def _slide_rels_name(slide_part: str) -> str:
    path = PurePosixPath(slide_part)
    return (path.parent / "_rels" / f"{path.name}.rels").as_posix()


def _resolve_part_target(owner_part: str, target: str) -> str:
    if not target or target.startswith("#"):
        raise ValueError(f"关系目标无效：{target!r}")
    if target.startswith("/"):
        normalized = posixpath.normpath(target).lstrip("/")
    else:
        normalized = posixpath.normpath(
            posixpath.join(posixpath.dirname(owner_part), target)
        )
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"关系目标逃逸 OOXML 包根目录：{target}")
    return normalized.lstrip("/")


def _duplicate(
    source: Path,
    staged: Path,
    *,
    source_slide: int,
    insert_after: int,
) -> dict[str, Any]:
    with zipfile.ZipFile(source, "r") as incoming:
        infos = incoming.infolist()
        names = {info.filename for info in infos}
        presentation_root = _parse_xml(
            incoming.read("ppt/presentation.xml"),
            "ppt/presentation.xml",
        )
        presentation_rels_root = _parse_xml(
            incoming.read("ppt/_rels/presentation.xml.rels"),
            "ppt/_rels/presentation.xml.rels",
        )
        content_types_root = _parse_xml(
            incoming.read("[Content_Types].xml"),
            "[Content_Types].xml",
        )

        slide_id_list = presentation_root.find(f"{{{P_NS}}}sldIdLst")
        if slide_id_list is None:
            raise ValueError("presentation.xml 缺少 sldIdLst")
        slide_ids = list(slide_id_list)
        if source_slide < 1 or source_slide > len(slide_ids):
            raise ValueError(f"slide 超出页面总数 {len(slide_ids)}")
        if insert_after < 1 or insert_after > len(slide_ids):
            raise ValueError(f"after 超出页面总数 {len(slide_ids)}")

        rel_targets: dict[str, str] = {}
        existing_rel_ids: set[str] = set()
        slide_relationship_type = None
        for relationship in presentation_rels_root:
            relationship_id = relationship.attrib.get("Id", "")
            existing_rel_ids.add(relationship_id)
            target = relationship.attrib.get("Target", "")
            if relationship.attrib.get("TargetMode") == "External":
                continue
            posix_target = _resolve_part_target(
                "ppt/presentation.xml",
                target,
            )
            rel_targets[relationship_id] = posix_target
            if relationship.attrib.get("Type", "").endswith(SLIDE_REL_TYPE_SUFFIX):
                slide_relationship_type = relationship.attrib.get("Type")
        if not slide_relationship_type:
            raise ValueError("presentation.xml.rels 不包含 slide 关系类型")

        source_rel_id = slide_ids[source_slide - 1].attrib.get(f"{{{R_NS}}}id")
        source_part = rel_targets.get(source_rel_id or "")
        if not source_part or source_part not in names:
            raise ValueError("无法解析待复制页面的 slide 部件")

        existing_numbers = [
            int(match.group(1))
            for name in names
            if (
                match := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            )
        ]
        new_part_number = max(existing_numbers, default=0) + 1
        new_part = f"ppt/slides/slide{new_part_number}.xml"
        new_rels_part = _slide_rels_name(new_part)
        new_rel_id = _next_relationship_id(existing_rel_ids)
        numeric_ids = [
            int(item.attrib["id"])
            for item in slide_ids
            if item.attrib.get("id", "").isdigit()
        ]
        new_slide_id = str(max(numeric_ids, default=255) + 1)

        from lxml import etree

        new_relationship = etree.Element(
            f"{{{REL_NS}}}Relationship",
            Id=new_rel_id,
            Type=slide_relationship_type,
            Target=f"slides/slide{new_part_number}.xml",
        )
        presentation_rels_root.append(new_relationship)
        new_slide_id_element = etree.Element(
            f"{{{P_NS}}}sldId",
            id=new_slide_id,
        )
        new_slide_id_element.set(f"{{{R_NS}}}id", new_rel_id)
        slide_id_list.insert(insert_after, new_slide_id_element)

        override_exists = any(
            node.attrib.get("PartName") == f"/{new_part}"
            for node in content_types_root.findall(
                f"{{{CONTENT_TYPES_NS}}}Override"
            )
        )
        if not override_exists:
            content_types_root.append(
                etree.Element(
                    f"{{{CONTENT_TYPES_NS}}}Override",
                    PartName=f"/{new_part}",
                    ContentType=SLIDE_CONTENT_TYPE,
                )
            )

        replacements = {
            "ppt/presentation.xml": _serialize_xml(presentation_root),
            "ppt/_rels/presentation.xml.rels": _serialize_xml(
                presentation_rels_root
            ),
            "[Content_Types].xml": _serialize_xml(content_types_root),
        }
        additions = {new_part: incoming.read(source_part)}
        source_rels_part = _slide_rels_name(source_part)
        dropped_relationships: list[str] = []
        shared_relationships: list[dict[str, str]] = []
        if source_rels_part in names:
            slide_rels_root = _parse_xml(
                incoming.read(source_rels_part),
                source_rels_part,
            )
            for relationship in list(slide_rels_root):
                relationship_type = relationship.attrib.get("Type", "")
                if relationship_type.endswith(DROP_RELATIONSHIP_SUFFIXES):
                    dropped_relationships.append(relationship_type)
                    slide_rels_root.remove(relationship)
                    continue
                if relationship_type.endswith(
                    ("/chart", "/diagramData", "/diagramDrawing", "/oleObject")
                ):
                    shared_relationships.append(
                        {
                            "type": relationship_type,
                            "target": relationship.attrib.get("Target", ""),
                        }
                    )
            additions[new_rels_part] = _serialize_xml(slide_rels_root)

        with zipfile.ZipFile(
            staged,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as outgoing:
            for info in infos:
                payload = replacements.get(info.filename)
                if payload is None:
                    payload = incoming.read(info.filename)
                outgoing.writestr(info, payload)
            for name, payload in additions.items():
                outgoing.writestr(name, payload)

    archive = inspect_archive(staged)
    return {
        "source_slide": source_slide,
        "insert_after": insert_after,
        "new_slide": insert_after + 1,
        "new_slide_part": new_part,
        "dropped_relationship_types": dropped_relationships,
        "shared_relationships": shared_relationships,
        "archive": archive,
    }


def main() -> dict[str, Any]:
    from pptx import Presentation

    args = build_parser().parse_args()
    source = input_file(args.input, {".pptx"})
    destination = output_file(args.output, {".pptx"}, overwrite=args.overwrite)
    if source == destination:
        raise ValueError("不能覆盖输入演示文稿；请使用新的 output 路径")
    insert_after = args.slide if args.after is None else args.after
    with tempfile.TemporaryDirectory(prefix="pptx-duplicate-") as temp_name:
        staged = Path(temp_name) / "duplicated.pptx"
        result = _duplicate(
            source,
            staged,
            source_slide=args.slide,
            insert_after=insert_after,
        )
        presentation = Presentation(str(staged))
        result["slide_count"] = len(presentation.slides)
        publish_file(staged, destination, overwrite=args.overwrite)
    return {
        "source": str(source),
        "path": str(destination),
        **result,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
