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
from pathlib import Path
from typing import Any, NoReturn, Optional

from _docx_common import emit, failure_message, output_file, publish_file


DEFAULT_TIMEOUT_SECONDS = 60
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
USER_AGENT = "wechat-robot-docx-attachment-downloader/1.0"


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def _validate_https_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("附件 URL 不能为空")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("附件 URL 不能包含空白字符或控制字符")

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("附件 URL 必须是有效的 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("附件 URL 不允许包含用户名或密码")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("附件 URL 端口格式不正确") from exc
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
    parser = SkillArgumentParser(
        description="下载不超过 25 MiB 的远程 HTTPS 通用附件"
    )
    parser.add_argument(
        "--url",
        "--attachment-url",
        "--attachment_url",
        dest="url",
        required=True,
        help="远程 HTTPS 附件地址",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="附件本地输出路径；允许图片、音视频、压缩包及其他文件类型",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"连接和读取超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖本次任务已存在的缓存文件",
    )
    args = parser.parse_args(argv)

    args.url = _validate_https_url(args.url)
    if args.timeout < 1 or args.timeout > 600:
        raise ValueError("timeout 必须在 1 到 600 秒之间")
    args.output = output_file(args.output, overwrite=args.overwrite)
    return args


def _content_length(headers: Any) -> Optional[int]:
    raw_value = headers.get("Content-Length")
    if raw_value is None:
        return None
    try:
        size = int(raw_value)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


def _reject_if_too_large(size_bytes: int, *, source: str) -> None:
    if size_bytes <= MAX_ATTACHMENT_BYTES:
        return
    raise ValueError(
        "附件超过 25 MiB（26214400 字节）限制，已拒绝下载；"
        f"{source}大小为 {size_bytes} 字节"
    )


def _probe_size(
    opener: urllib.request.OpenerDirector,
    url: str,
    timeout: int,
) -> Optional[int]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
        method="HEAD",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            _validate_https_url(response.geturl())
            size = _content_length(response.headers)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
    ):
        return None

    if size is not None:
        _reject_if_too_large(size, source="远端声明")
    return size


def _content_type(headers: Any) -> Optional[str]:
    raw_value = headers.get("Content-Type")
    if not raw_value:
        return None
    media_type = raw_value.split(";", 1)[0].strip().lower()
    return media_type or None


def _download(args: argparse.Namespace) -> dict[str, Any]:
    output: Path = args.output
    opener = urllib.request.build_opener(HTTPSOnlyRedirectHandler())
    probed_size = _probe_size(opener, args.url, args.timeout)
    request = urllib.request.Request(
        args.url,
        headers={
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    temp_path: Optional[Path] = None
    downloaded_bytes = 0
    response_size: Optional[int] = None
    response_type: Optional[str] = None
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
                response_size = _content_length(response.headers)
                response_type = _content_type(response.headers)
                if response_size is not None:
                    _reject_if_too_large(response_size, source="下载响应声明")

                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    _reject_if_too_large(downloaded_bytes, source="已接收")
                    temp_file.write(chunk)

            temp_file.flush()
            os.fsync(temp_file.fileno())

        if downloaded_bytes == 0:
            raise ValueError("远程服务器返回了空附件")

        publish_file(temp_path, output, overwrite=args.overwrite)
        temp_path = None
        declared_size = (
            response_size if response_size is not None else probed_size
        )
        if response_size is not None:
            size_probe = "get-content-length"
        elif probed_size is not None:
            size_probe = "head-content-length"
        else:
            size_probe = "stream"
        return {
            "path": str(output),
            "size_bytes": downloaded_bytes,
            "declared_size_bytes": declared_size,
            "size_limit_bytes": MAX_ATTACHMENT_BYTES,
            "size_probe": size_probe,
            "content_type": response_type,
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"附件下载失败：远程服务器返回 HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "附件下载失败：连接或读取超时"
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return "附件下载失败：连接或读取超时"
        return "附件下载失败：无法访问远程服务器"
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
