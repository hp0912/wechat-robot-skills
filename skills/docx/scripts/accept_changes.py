#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from _docx_common import (
    DOCX_INPUT_SUFFIXES,
    SkillArgumentParser,
    find_program,
    input_file,
    inspect_archive,
    office_profile_uri,
    output_file,
    parse_xml_bytes,
    publish_file,
    rewrite_docx_parts,
    run_cli,
    run_program,
)


MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script"
 script:name="Module1" script:language="StarBasic">
Sub AcceptAllTrackedChanges()
    Dim frame As Object
    Dim dispatcher As Object
    frame = ThisComponent.CurrentController.Frame
    dispatcher = createUnoService("com.sun.star.frame.DispatchHelper")
    dispatcher.executeDispatch(frame, ".uno:AcceptAllTrackedChanges", "", 0, Array())
    ThisComponent.store()
    ThisComponent.close(True)
End Sub
</script:module>
"""

SCRIPT_XLB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library"
 library:name="Standard" library:readonly="false" library:passwordprotected="false">
 <library:element library:name="Module1"/>
</library:library>
"""

SCRIPT_XLC = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:libraries PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "libraries.dtd">
<library:libraries xmlns:library="http://openoffice.org/2000/library"
 xmlns:xlink="http://www.w3.org/1999/xlink">
 <library:library library:name="Standard"
  xlink:href="$(USER)/basic/Standard/script.xlb/"
  xlink:type="simple" library:link="false"/>
</library:libraries>
"""


def _timeout_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-2000:]
    return str(value)[-2000:]


def _unwrap(element: Any) -> None:
    parent = element.getparent()
    if parent is None:
        return
    index = parent.index(element)
    for child in list(element):
        element.remove(child)
        parent.insert(index, child)
        index += 1
    parent.remove(element)


def _remove(element: Any) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _accept_revisions_in_xml(payload: bytes) -> tuple[bytes, int]:
    from lxml import etree

    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": namespace}
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=False,
        remove_comments=False,
    )
    root = etree.fromstring(payload, parser=parser)
    changes = 0

    # 接受被删除的表格行和单元格；插入标记稍后作为普通 w:ins 解包。
    for row in list(root.xpath(".//w:tr[w:trPr/w:del]", namespaces=ns)):
        _remove(row)
        changes += 1
    for cell in list(root.xpath(".//w:tc[w:tcPr/w:cellDel]", namespaces=ns)):
        _remove(cell)
        changes += 1

    # 删除段落标记表示把当前段落与下一段合并。
    while True:
        paragraphs = root.xpath(".//w:p[w:pPr/w:rPr/w:del]", namespaces=ns)
        if not paragraphs:
            break
        paragraph = paragraphs[0]
        marker = paragraph.find("./w:pPr/w:rPr/w:del", namespaces=ns)
        next_paragraph = paragraph.getnext()
        if (
            next_paragraph is not None
            and next_paragraph.tag == f"{{{namespace}}}p"
        ):
            for child in list(next_paragraph):
                if child.tag == f"{{{namespace}}}pPr":
                    continue
                next_paragraph.remove(child)
                paragraph.append(child)
            _remove(next_paragraph)
        if marker is not None:
            _remove(marker)
        changes += 1

    # 删除修订内容和移动来源。
    for expression in (".//w:del", ".//w:moveFrom"):
        for element in list(root.xpath(expression, namespaces=ns)):
            _remove(element)
            changes += 1

    # 保留插入内容和移动目标，去掉外层修订容器。
    for expression in (".//w:ins", ".//w:moveTo"):
        for element in list(root.xpath(expression, namespaces=ns)):
            _unwrap(element)
            changes += 1

    removable_names = {
        "pPrChange",
        "rPrChange",
        "tblPrChange",
        "tblGridChange",
        "trPrChange",
        "tcPrChange",
        "sectPrChange",
        "numberingChange",
        "cellIns",
        "cellDel",
        "cellMerge",
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
    }
    for local_name in removable_names:
        for element in list(
            root.xpath(f".//w:{local_name}", namespaces=ns)
        ):
            _remove(element)
            changes += 1

    # malformed producers sometimes leave delText outside w:del; accepted view treats it as text.
    for element in root.xpath(".//w:delText", namespaces=ns):
        element.tag = f"{{{namespace}}}t"
        changes += 1

    return (
        etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        ),
        changes,
    )


def _accept_with_ooxml(source: Path, destination: Path) -> int:
    replacements: dict[str, bytes] = {}
    changes = 0
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            rewritten, part_changes = _accept_revisions_in_xml(archive.read(name))
            if part_changes:
                replacements[name] = rewritten
                changes += part_changes
    rewrite_docx_parts(source, destination, replacements)
    return changes


def _revision_count(path: Path) -> int:
    revision_tags = {
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        + name
        for name in (
            "ins",
            "del",
            "moveFrom",
            "moveTo",
            "pPrChange",
            "rPrChange",
            "tblPrChange",
            "trPrChange",
            "tcPrChange",
            "sectPrChange",
        )
    }
    count = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = parse_xml_bytes(archive.read(name), label=name)
            count += sum(1 for element in root.iter() if element.tag in revision_tags)
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = SkillArgumentParser(
        description="通过固定 LibreOffice 宏接受 Word 文档中的全部修订。"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> dict[str, Any]:
    from docx import Document

    args = build_parser().parse_args()
    source = input_file(args.input, DOCX_INPUT_SUFFIXES)
    destination = output_file(
        args.output,
        {".docx"},
        overwrite=args.overwrite,
    )
    if source == destination:
        raise ValueError("输出路径不能与输入文件相同")
    before = _revision_count(source)
    with tempfile.TemporaryDirectory(prefix="docx-accept-") as temp_name:
        temp_dir = Path(temp_name)
        staged = temp_dir / "accepted.docx"
        shutil.copy2(source, staged)
        stdout = ""
        stderr = ""
        timed_out_after_save = False
        macro_failed = False
        engine = "copy"
        if before:
            engine = "libreoffice"
            soffice = find_program("soffice", "libreoffice")
            profile = temp_dir / "profile"
            profile.mkdir()
            cache_dir = profile / "cache"
            cache_dir.mkdir()
            process_env = os.environ.copy()
            process_env["XDG_CACHE_HOME"] = str(cache_dir)
            try:
                run_program(
                    [
                        soffice,
                        f"-env:UserInstallation={office_profile_uri(profile)}",
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nolockcheck",
                        "--nofirststartwizard",
                        "--terminate_after_init",
                    ],
                    timeout=min(args.timeout, 30),
                    env=process_env,
                )
            except Exception:
                # 部分 LibreOffice 构建不支持 terminate_after_init；后续宏调用仍可初始化配置。
                pass
            macro_dir = profile / "user" / "basic" / "Standard"
            macro_dir.mkdir(parents=True, exist_ok=True)
            (macro_dir / "Module1.xba").write_text(MACRO, encoding="utf-8")
            (macro_dir / "script.xlb").write_text(
                SCRIPT_XLB,
                encoding="utf-8",
            )
            (profile / "user" / "basic" / "script.xlc").write_text(
                SCRIPT_XLC,
                encoding="utf-8",
            )
            command = [
                soffice,
                f"-env:UserInstallation={office_profile_uri(profile)}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                "vnd.sun.star.script:Standard.Module1.AcceptAllTrackedChanges"
                "?language=Basic&location=application",
                str(staged),
            ]
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=min(args.timeout, 30),
                    check=False,
                    env=process_env,
                )
                stdout = completed.stdout[-2000:]
                stderr = completed.stderr[-2000:]
                if completed.returncode != 0:
                    macro_failed = True
            except subprocess.TimeoutExpired as exc:
                stdout = _timeout_text(exc.stdout)
                stderr = _timeout_text(exc.stderr)
                timed_out_after_save = True

        after = _revision_count(staged)
        if after:
            fallback = temp_dir / "accepted-ooxml.docx"
            _accept_with_ooxml(staged, fallback)
            shutil.copy2(fallback, staged)
            engine = "ooxml-fallback"
            after = _revision_count(staged)
            if after:
                raise RuntimeError(
                    f"接受修订后仍检测到 {after} 个修订标记；未发布结果"
                )
        archive = inspect_archive(staged)
        Document(str(staged))
        publish_source = temp_dir / "publish.docx"
        shutil.copy2(staged, publish_source)
        publish_file(publish_source, destination, overwrite=args.overwrite)

    return {
        "path": str(destination),
        "source": str(source),
        "revision_markers_before": before,
        "revision_markers_after": after,
        "status": "success",
        "engine": engine,
        "libreoffice_macro_failed": macro_failed,
        "office_timed_out_after_save": timed_out_after_save,
        "office_stdout": stdout,
        "office_stderr": stderr,
        "archive": archive,
        "requires_visual_review": True,
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
