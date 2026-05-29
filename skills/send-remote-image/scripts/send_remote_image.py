#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.stderr = sys.stdout


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
        return json.loads(text)


def _expand_json_array_values(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                raise ValueError("image_urls 必须是字符串数组")
            for item in parsed:
                if not isinstance(item, str):
                    raise ValueError("image_urls 必须是字符串数组")
                if item.strip():
                    expanded.append(item.strip())
            continue
        expanded.append(stripped)
    return expanded


def _parse_cli_params(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--image_url", action="append", default=[])
    parser.add_argument("--image_urls", action="append", default=[])

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    image_urls = _expand_json_array_values(namespace.image_url + namespace.image_urls)
    deduped: list[str] = []
    seen = set()
    for image_url in image_urls:
        if image_url not in seen:
            seen.add(image_url)
            deduped.append(image_url)
    return deduped


def _validate_remote_image_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"远程图片 URL 格式不正确: {value}")
    return value


def _send_remote_images(client_port: str, to_wxid: str, image_urls: list[str]) -> None:
    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/image/url"
    _http_post_json(send_url, {"to_wxid": to_wxid, "image_urls": image_urls})


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少远程图片 URL\n")
        return 1

    try:
        raw_image_urls = _parse_cli_params(sys.argv[1:])
        if not raw_image_urls:
            sys.stdout.write("缺少远程图片 URL\n")
            return 1
        image_urls = [_validate_remote_image_url(value) for value in raw_image_urls]
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

    try:
        _send_remote_images(client_port, to_wxid, image_urls)
        sys.stdout.write("图片发送成功\n")
        return 0
    except Exception as exc:
        sys.stdout.write(f"图片发送失败: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())