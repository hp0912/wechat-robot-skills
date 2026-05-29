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
                raise ValueError("file_paths 必须是字符串数组")
            for item in parsed:
                if not isinstance(item, str):
                    raise ValueError("file_paths 必须是字符串数组")
                if item.strip():
                    expanded.append(item.strip())
            continue
        expanded.append(stripped)
    return expanded


def _parse_cli_params(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--file_path", action="append", default=[])
    parser.add_argument("--file_paths", action="append", default=[])

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    file_paths = _expand_json_array_values(namespace.file_path + namespace.file_paths)
    deduped: list[str] = []
    seen = set()
    for file_path in file_paths:
        if file_path not in seen:
            seen.add(file_path)
            deduped.append(file_path)
    return deduped


def _is_remote_url(value: str) -> bool:
    return urllib.parse.urlparse(value).scheme in {"http", "https"}


def _normalize_local_file_path(value: str) -> str:
    if _is_remote_url(value):
        raise ValueError("本地图片技能不支持远程 URL，请使用 send-remote-image")

    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"本地图片文件不存在: {value}")
    return str(path.resolve())


def _send_local_image(client_port: str, to_wxid: str, file_path: str) -> None:
    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/image/local"
    _http_post_json(send_url, {"to_wxid": to_wxid, "file_path": file_path})


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少本地图片路径\n")
        return 1

    try:
        raw_file_paths = _parse_cli_params(sys.argv[1:])
        if not raw_file_paths:
            sys.stdout.write("缺少本地图片路径\n")
            return 1
        file_paths = [_normalize_local_file_path(value) for value in raw_file_paths]
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
        for file_path in file_paths:
            _send_local_image(client_port, to_wxid, file_path)
        sys.stdout.write("图片发送成功\n")
        return 0
    except Exception as exc:
        sys.stdout.write(f"图片发送失败: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())