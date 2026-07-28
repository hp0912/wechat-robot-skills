#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NoReturn, Optional

from _pdf_common import ensure_output_path

DEFAULT_TIMEOUT_SECONDS = 60
MAX_ALLOWED_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_BYTES = MAX_ALLOWED_BYTES
CHUNK_SIZE = 1024 * 1024
USER_AGENT = "wechat-robot-pdf-skill/1.0"


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _validate_https_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("PDF URL 不能为空")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("PDF URL 不能包含空白字符或控制字符")

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("PDF URL 必须是有效的 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PDF URL 不允许包含用户名或密码")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("PDF URL 端口格式不正确") from exc
    return url


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url = _validate_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = SkillArgumentParser(description="下载并校验远程 HTTPS PDF")
    parser.add_argument(
        "--url",
        "--pdf-url",
        "--pdf_url",
        dest="url",
        required=True,
        help="远程 HTTPS PDF 地址",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="本地输出路径，必须以 .pdf 结尾；父目录不存在时会自动创建",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"连接和读取超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--max-bytes",
        "--max_bytes",
        dest="max_bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"最大下载字节数，默认 {DEFAULT_MAX_BYTES}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的目标文件",
    )
    args = parser.parse_args(argv)

    args.url = _validate_https_url(args.url)
    if args.timeout <= 0:
        raise ValueError("timeout 必须大于 0")
    if args.max_bytes <= 0 or args.max_bytes > MAX_ALLOWED_BYTES:
        raise ValueError(
            f"max-bytes 必须在 1 到 {MAX_ALLOWED_BYTES} 之间"
        )

    output = Path(args.output).expanduser()
    if output.suffix.lower() != ".pdf":
        raise ValueError("output 必须以 .pdf 结尾")
    args.output = ensure_output_path(output)
    return args


def _validate_pdf(path: Path) -> tuple[Optional[int], bool]:
    with path.open("rb") as file:
        prefix = file.read(1024)
    if b"%PDF-" not in prefix:
        raise ValueError("下载内容不是 PDF 文件")

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("当前 Python 未加载环境预置的 pypdf 模块") from exc

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = None
    try:
        reader = PdfReader(str(path), strict=False)
        encrypted = bool(reader.is_encrypted)
        page_count = None if encrypted else len(reader.pages)
    except Exception as exc:
        raise ValueError("下载内容不是可解析的 PDF 文件") from exc
    finally:
        stream = getattr(reader, "stream", None)
        if stream is not None and hasattr(stream, "close"):
            stream.close()
    return page_count, encrypted


def _download(args: argparse.Namespace) -> dict:
    output: Path = args.output
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"目标文件已存在：{output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        args.url,
        headers={
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    opener = urllib.request.build_opener(HTTPSOnlyRedirectHandler())
    temp_path: Optional[Path] = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.name}.",
            suffix=".part",
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
                            "远程文件超过大小限制，已拒绝下载："
                            f"最多允许 {args.max_bytes} 字节"
                        )

                downloaded_bytes = 0
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > args.max_bytes:
                        raise ValueError(
                            "远程文件超过大小限制，已拒绝下载："
                            f"最多允许 {args.max_bytes} 字节"
                        )
                    temp_file.write(chunk)

            temp_file.flush()
            os.fsync(temp_file.fileno())

        if downloaded_bytes == 0:
            raise ValueError("远程服务器返回了空文件")

        page_count, encrypted = _validate_pdf(temp_path)
        if args.overwrite:
            os.replace(temp_path, output)
        else:
            try:
                os.link(temp_path, output)
            except FileExistsError as exc:
                raise FileExistsError(f"目标文件已存在：{output}") from exc
            temp_path.unlink()
        temp_path = None

        return {
            "ok": True,
            "path": str(output),
            "size_bytes": downloaded_bytes,
            "page_count": page_count,
            "encrypted": encrypted,
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
    if isinstance(exc, FileExistsError):
        return str(exc)
    if isinstance(exc, PermissionError):
        return "写入失败：没有目标路径的写入权限"
    if isinstance(exc, OSError):
        return f"文件处理失败：{exc}"
    return str(exc)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = _parse_args(sys.argv[1:] if argv is None else argv)
        result = _download(args)
    except Exception as exc:
        _emit({"ok": False, "error": _failure_message(exc)})
        return 1

    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
