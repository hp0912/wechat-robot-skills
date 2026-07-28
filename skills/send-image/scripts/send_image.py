#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.stderr = sys.stdout


def _raise_for_client_error(response: dict) -> None:
    if not response:
        return

    code = response.get("code")
    if code is not None and str(code) != "200":
        message = str(response.get("message") or f"客户端返回错误码 {code}")
        raise RuntimeError(message)

    data = response.get("data")
    if isinstance(data, list):
        failures = [
            item.strip()
            for item in data
            if isinstance(item, str) and item.lstrip().startswith("失败:")
        ]
        if failures:
            raise RuntimeError("；".join(failures))


def _http_post_json(url: str, body: dict, timeout: int = 300) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
    if not text.strip():
        return {}

    response = json.loads(text)
    if not isinstance(response, dict):
        raise RuntimeError("客户端返回格式不正确")
    _raise_for_client_error(response)
    return response


def _expand_json_array_values(values: list[str], label: str) -> list[str]:
    expanded: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list) or not all(
                isinstance(item, str) for item in parsed
            ):
                raise ValueError(f"{label} 必须是字符串数组")
            expanded.extend(item.strip() for item in parsed if item.strip())
            continue
        expanded.append(stripped)
    return expanded


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _parse_cli_params(argv: list[str]) -> tuple[list[str], list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--file_path", action="append", default=[])
    parser.add_argument("--file_paths", action="append", default=[])
    parser.add_argument("--image_url", action="append", default=[])
    parser.add_argument("--image_urls", action="append", default=[])

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    file_paths = _expand_json_array_values(
        namespace.file_path + namespace.file_paths, "file_paths"
    )
    image_urls = _expand_json_array_values(
        namespace.image_url + namespace.image_urls, "image_urls"
    )
    return _dedupe(file_paths), _dedupe(image_urls)


def _is_remote_url(value: str) -> bool:
    return urllib.parse.urlparse(value).scheme.lower() in {"http", "https"}


def _normalize_local_image_path(value: str) -> str:
    if _is_remote_url(value):
        raise ValueError(f"本地图片路径不能是远程 URL: {value}")

    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"本地图片文件不存在: {value}")
    if path.stat().st_size <= 0:
        raise ValueError(f"本地图片文件不能为空: {value}")
    return str(path.resolve())


def _validate_remote_image_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"远程图片 URL 格式不正确: {value}")
    return value


def _send_local_image(client_port: str, to_wxid: str, file_path: str) -> None:
    send_url = (
        f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/image/local"
    )
    _http_post_json(send_url, {"to_wxid": to_wxid, "file_path": file_path})


def _send_remote_images(
    client_port: str, to_wxid: str, image_urls: list[str]
) -> None:
    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/image/url"
    _http_post_json(send_url, {"to_wxid": to_wxid, "image_urls": image_urls})


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少本地图片路径或远程图片 URL\n")
        return 1

    try:
        raw_file_paths, raw_image_urls = _parse_cli_params(sys.argv[1:])
        if not raw_file_paths and not raw_image_urls:
            sys.stdout.write("缺少本地图片路径或远程图片 URL\n")
            return 1
        file_paths = _dedupe(
            [_normalize_local_image_path(value) for value in raw_file_paths]
        )
        image_urls = [
            _validate_remote_image_url(value) for value in raw_image_urls
        ]
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        sys.stdout.write("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置\n")
        return 1

    to_wxid = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not to_wxid:
        sys.stdout.write("环境变量 ROBOT_FROM_WX_ID 未配置\n")
        return 1

    failures: list[str] = []
    for file_path in file_paths:
        try:
            _send_local_image(client_port, to_wxid, file_path)
        except Exception as exc:
            failures.append(f"本地图片 {file_path}: {exc}")

    if image_urls:
        try:
            _send_remote_images(client_port, to_wxid, image_urls)
        except Exception as exc:
            failures.append(f"远程图片: {exc}")

    if failures:
        sys.stdout.write(f"图片发送失败: {'；'.join(failures)}\n")
        return 1

    sys.stdout.write("图片发送成功\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
