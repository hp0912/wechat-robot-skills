#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, NoReturn, Optional

from _pptx_common import (
    PRESENTATION_INPUT_SUFFIXES,
    emit,
    failure_message,
    inspect_archive,
    output_file,
    parse_xml_bytes,
    publish_file,
)


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
MAX_ALLOWED_BYTES = 512 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
USER_AGENT = "wechat-robot-pptx-skill/1.0"
OLE_COMPOUND_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PRESENTATION_CONTENT_TYPES = {
    (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation.main+xml"
    ): ".pptx",
    (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.template.main+xml"
    ): ".potx",
    (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.slideshow.main+xml"
    ): ".ppsx",
}


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def _validate_https_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("演示文稿 URL 不能为空")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("演示文稿 URL 不能包含空白字符或控制字符")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("演示文稿 URL 必须是有效的 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("演示文稿 URL 不允许包含用户名或密码")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("演示文稿 URL 端口格式不正确") from exc
    return url


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            _validate_https_url(newurl),
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = SkillArgumentParser(description="下载并校验远程 HTTPS 演示文稿")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    args.url = _validate_https_url(args.url)
    if args.timeout < 1 or args.timeout > 600:
        raise ValueError("timeout 必须在 1 到 600 秒之间")
    if args.max_bytes < 1 or args.max_bytes > MAX_ALLOWED_BYTES:
        raise ValueError(f"max-bytes 必须在 1 到 {MAX_ALLOWED_BYTES} 之间")
    args.output = output_file(
        args.output,
        PRESENTATION_INPUT_SUFFIXES,
        overwrite=args.overwrite,
    )
    return args


def _detect_ooxml_suffix(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = parse_xml_bytes(
            archive.read("[Content_Types].xml"),
            label="[Content_Types].xml",
        )
    for element in root.iter(f"{{{CONTENT_TYPES_NS}}}Override"):
        if element.attrib.get("PartName") != "/ppt/presentation.xml":
            continue
        detected = PRESENTATION_CONTENT_TYPES.get(
            element.attrib.get("ContentType", "")
        )
        if detected:
            return detected
    raise ValueError("下载内容不是受支持的 PowerPoint OOXML 文件")


def _validate_ooxml(path: Path, expected_suffix: str) -> dict[str, Any]:
    archive = inspect_archive(path)
    if archive["missing_required_parts"]:
        raise ValueError(
            "下载内容不是有效的 PowerPoint OOXML 文件；缺少："
            + "、".join(archive["missing_required_parts"])
        )
    if archive["duplicate_members"]:
        raise ValueError(
            "PowerPoint 压缩包含重复成员："
            + "、".join(archive["duplicate_members"][:10])
        )
    detected_suffix = _detect_ooxml_suffix(path)
    if detected_suffix != expected_suffix:
        raise ValueError(
            "下载内容的实际格式为 "
            f"{detected_suffix}，但 output 使用了 {expected_suffix}"
        )

    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("当前 Python 未加载环境预置的 python-pptx 模块") from exc
    try:
        presentation = Presentation(str(path))
        slide_count = len(presentation.slides)
    except Exception as exc:
        raise ValueError("下载内容不是可解析的 PowerPoint 演示文稿") from exc
    return {
        "format": detected_suffix.lstrip("."),
        "slide_count": slide_count,
        "validation": "ooxml-and-python-pptx",
        "archive": archive,
    }


def _validate_legacy_ppt(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        magic = stream.read(len(OLE_COMPOUND_MAGIC))
    if magic != OLE_COMPOUND_MAGIC:
        raise ValueError("下载内容不是有效的旧版 PowerPoint 复合文件")
    return {
        "format": "ppt",
        "validation": "ole-compound-signature",
    }


def _validate_presentation(path: Path, suffix: str) -> dict[str, Any]:
    if suffix == ".ppt":
        return _validate_legacy_ppt(path)
    return _validate_ooxml(path, suffix)


def _download(args: argparse.Namespace) -> dict[str, Any]:
    output: Path = args.output
    request = urllib.request.Request(
        args.url,
        headers={
            "Accept": (
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation,"
                "application/vnd.ms-powerpoint,"
                "application/octet-stream;q=0.9,*/*;q=0.1"
            ),
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    opener = urllib.request.build_opener(HTTPSOnlyRedirectHandler())
    temp_path: Optional[Path] = None
    downloaded_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.stem}.",
            suffix=f".part{output.suffix}",
            dir=str(output.parent),
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            with opener.open(request, timeout=args.timeout) as response:
                _validate_https_url(response.geturl())
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        expected_bytes = int(content_length)
                    except ValueError:
                        expected_bytes = 0
                    if expected_bytes > args.max_bytes:
                        raise ValueError(
                            f"远程文件超过大小限制：最多允许 {args.max_bytes} 字节"
                        )
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > args.max_bytes:
                        raise ValueError(
                            f"远程文件超过大小限制：最多允许 {args.max_bytes} 字节"
                        )
                    temp_file.write(chunk)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if downloaded_bytes == 0:
            raise ValueError("远程服务器返回了空文件")
        details = _validate_presentation(temp_path, output.suffix.lower())
        publish_file(temp_path, output, overwrite=args.overwrite)
        temp_path = None
        return {
            "path": str(output),
            "size_bytes": downloaded_bytes,
            **details,
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"下载失败：远程服务器返回 HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "下载失败：连接或读取超时"
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return "下载失败：连接或读取超时"
        return "下载失败：无法访问远程服务器"
    return failure_message(exc)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = _parse_args(sys.argv[1:] if argv is None else argv)
        result = _download(args)
    except Exception as exc:
        emit({"ok": False, "error": _failure_message(exc)})
        return 1
    emit({"ok": True, **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
