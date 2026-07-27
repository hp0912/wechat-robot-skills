#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Callable, NoReturn, Optional
from urllib.parse import quote


OOXML_PRESENTATION_SUFFIXES = {".pptx", ".potx", ".ppsx"}
PRESENTATION_INPUT_SUFFIXES = OOXML_PRESENTATION_SUFFIXES | {".ppt"}
PRESENTATION_OUTPUT_SUFFIXES = {".pptx", ".potx", ".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 1_073_741_824
MAX_MEMBER_BYTES = 268_435_456

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=json_default))


def failure_message(exc: Exception) -> str:
    if isinstance(exc, (FileExistsError, FileNotFoundError)):
        return str(exc)
    if isinstance(exc, PermissionError):
        return "文件处理失败：没有目标路径的访问权限"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"外部程序执行超时（{exc.timeout} 秒）"
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        return f"外部程序执行失败：{stderr or exc}"
    if isinstance(exc, zipfile.BadZipFile):
        return "文件不是有效的 OOXML 压缩包"
    if isinstance(exc, OSError):
        return f"文件处理失败：{exc}"
    return str(exc)


def run_cli(action: Callable[[], dict[str, Any]]) -> int:
    try:
        result = action()
    except Exception as exc:
        emit({"ok": False, "error": failure_message(exc)})
        return 1
    emit({"ok": True, **result})
    return 0


def input_file(value: str, suffixes: Optional[set[str]] = None) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"输入文件不存在：{path}")
    if not path.is_file():
        raise ValueError(f"输入路径不是文件：{path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"输入文件为空：{path}")
    if suffixes is not None and path.suffix.lower() not in suffixes:
        expected = "、".join(sorted(suffixes))
        raise ValueError(f"不支持的输入格式 {path.suffix}；允许：{expected}")
    return path


def output_file(
    value: str,
    suffixes: Optional[set[str]] = None,
    *,
    overwrite: bool = False,
) -> Path:
    path = Path(value).expanduser().resolve()
    if suffixes is not None and path.suffix.lower() not in suffixes:
        expected = "、".join(sorted(suffixes))
        raise ValueError(f"不支持的输出格式 {path.suffix}；允许：{expected}")
    if path.exists() and not path.is_file():
        raise ValueError(f"目标路径不是文件：{path}")
    if path.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def output_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"输出路径不是目录：{path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish_file(source: Path, destination: Path, *, overwrite: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        os.replace(source, destination)
        return
    try:
        os.link(source, destination)
    except FileExistsError as exc:
        raise FileExistsError(f"目标文件已存在：{destination}") from exc
    except OSError:
        if destination.exists():
            raise FileExistsError(f"目标文件已存在：{destination}")
        shutil.copy2(source, destination)
    finally:
        if source.exists():
            source.unlink()


def load_json_argument(
    inline_value: Optional[str],
    file_value: Optional[str],
    *,
    label: str,
    max_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    if bool(inline_value) == bool(file_value):
        raise ValueError(f"{label} 必须且只能通过内联 JSON 或 JSON 文件提供一次")
    if file_value:
        source = input_file(file_value, {".json"})
        if source.stat().st_size > max_bytes:
            raise ValueError(f"{label} JSON 文件超过 {max_bytes} 字节限制")
        raw = source.read_text(encoding="utf-8")
    else:
        raw = inline_value or ""
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValueError(f"{label} 内联 JSON 超过 {max_bytes} 字节限制")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} JSON 无效：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} JSON 顶层必须是对象")
    return parsed


def find_program(*names: str) -> str:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(f"运行环境缺少命令：{' / '.join(names)}")


def run_program(
    args: list[str],
    *,
    timeout: int,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    if timeout < 1 or timeout > 900:
        raise ValueError("timeout 必须在 1 到 900 秒之间")
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def office_profile_uri(profile: Path) -> str:
    return "file://" + quote(str(profile.resolve()), safe="/:")


def run_soffice_convert(
    source: Path,
    *,
    target_format: str,
    output_dir: Path,
    timeout: int,
    filter_name: Optional[str] = None,
) -> tuple[Path, dict[str, str]]:
    soffice = find_program("soffice", "libreoffice")
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="pptx-soffice-profile-"))
    try:
        cache_dir = profile / "cache"
        cache_dir.mkdir()
        process_env = os.environ.copy()
        process_env["XDG_CACHE_HOME"] = str(cache_dir)
        convert_arg = target_format
        if filter_name:
            convert_arg = f"{target_format}:{filter_name}"
        completed = run_program(
            [
                soffice,
                f"-env:UserInstallation={office_profile_uri(profile)}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                "--convert-to",
                convert_arg,
                "--outdir",
                str(output_dir),
                str(source),
            ],
            timeout=timeout,
            env=process_env,
        )
        expected = output_dir / f"{source.stem}.{target_format}"
        if not expected.exists():
            candidates = sorted(output_dir.glob(f"{source.stem}.*"))
            if len(candidates) == 1:
                expected = candidates[0]
            else:
                raise RuntimeError(
                    "LibreOffice 未生成预期文件；"
                    f"stdout={completed.stdout[-1000:]!r} "
                    f"stderr={completed.stderr[-1000:]!r}"
                )
        return expected, {
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _safe_archive_name(name: str) -> PurePosixPath:
    candidate = PurePosixPath(name)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or ".." in candidate.parts
        or candidate.parts[0].endswith(":")
    ):
        raise ValueError(f"OOXML 压缩包含不安全路径：{name}")
    return candidate


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def inspect_archive(path: Path) -> dict[str, Any]:
    total_size = 0
    xml_count = 0
    media_count = 0
    slide_count = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ValueError(
                f"OOXML 压缩包成员过多：{len(infos)} > {MAX_ARCHIVE_MEMBERS}"
            )
        seen: set[str] = set()
        duplicates: list[str] = []
        for info in infos:
            _safe_archive_name(info.filename)
            if _zip_member_is_symlink(info):
                raise ValueError(f"OOXML 压缩包含符号链接：{info.filename}")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"OOXML 成员过大：{info.filename}")
            total_size += info.file_size
            if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("OOXML 解压后总大小超过安全限制")
            if info.filename in seen:
                duplicates.append(info.filename)
            seen.add(info.filename)
            if info.filename.endswith((".xml", ".rels")):
                xml_count += 1
            if info.filename.startswith("ppt/media/") and not info.is_dir():
                media_count += 1
            if (
                info.filename.startswith("ppt/slides/slide")
                and info.filename.endswith(".xml")
                and "/_rels/" not in info.filename
            ):
                slide_count += 1
        required = {
            "[Content_Types].xml",
            "_rels/.rels",
            "ppt/presentation.xml",
            "ppt/_rels/presentation.xml.rels",
        }
        missing = sorted(required - seen)
        return {
            "member_count": len(infos),
            "uncompressed_bytes": total_size,
            "xml_part_count": xml_count,
            "media_count": media_count,
            "slide_part_count": slide_count,
            "duplicate_members": duplicates,
            "missing_required_parts": missing,
        }


def safe_extract_presentation(source: Path, destination: Path) -> dict[str, Any]:
    archive_info = inspect_archive(source)
    if archive_info["missing_required_parts"]:
        raise ValueError(
            "演示文稿缺少必要部件："
            + "、".join(archive_info["missing_required_parts"])
        )
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            relative = _safe_archive_name(info.filename)
            target = destination.joinpath(*relative.parts)
            resolved = target.resolve()
            if root not in resolved.parents and resolved != root:
                raise ValueError(f"OOXML 成员逃逸输出目录：{info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
    return archive_info


def pack_presentation_directory(source_dir: Path, destination: Path) -> dict[str, Any]:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise ValueError(f"待打包目录不存在或不是目录：{source_dir}")
    required = {
        source_dir / "[Content_Types].xml",
        source_dir / "_rels" / ".rels",
        source_dir / "ppt" / "presentation.xml",
        source_dir / "ppt" / "_rels" / "presentation.xml.rels",
    }
    missing = sorted(
        str(path.relative_to(source_dir)) for path in required if not path.is_file()
    )
    if missing:
        raise ValueError("待打包目录缺少必要部件：" + "、".join(missing))

    files: list[Path] = []
    total_size = 0
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"待打包目录包含符号链接：{path}")
        if path.is_file():
            size = path.stat().st_size
            if size > MAX_MEMBER_BYTES:
                raise ValueError(f"待打包文件过大：{path}")
            total_size += size
            if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("待打包文件总大小超过安全限制")
            files.append(path)
    if len(files) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("待打包文件数量超过安全限制")

    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in files:
            archive.write(path, path.relative_to(source_dir).as_posix())
    return inspect_archive(destination)


def parse_xml_bytes(payload: bytes, *, label: str = "XML") -> Any:
    try:
        from defusedxml import ElementTree as ET
        from defusedxml.common import DefusedXmlException

        try:
            return ET.fromstring(payload)
        except (ET.ParseError, DefusedXmlException, ValueError) as exc:
            raise ValueError(f"{label} 解析失败：{exc}") from exc
    except ModuleNotFoundError:
        from lxml import etree

        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            recover=False,
            huge_tree=False,
            remove_comments=False,
        )
        try:
            return etree.fromstring(payload, parser=parser)
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise ValueError(f"{label} 解析失败：{exc}") from exc
