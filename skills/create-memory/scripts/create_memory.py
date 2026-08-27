#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, NoReturn

sys.stderr = sys.stdout


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = SkillArgumentParser(description="创建当前微信会话的长期记忆")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"环境变量 {name} 未配置")
    return value


def _client_private_token() -> str:
    return os.environ.get("ROBOT_CLIENT_PRIVATE_TOKEN", "").strip()


def _positive_int_env(name: str) -> int:
    raw = _require_env(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if value < 1:
        raise ValueError(f"环境变量 {name} 必须是正整数")
    return value


def _optional_positive_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if value < 0:
        raise ValueError(f"环境变量 {name} 不能是负数")
    return value or None


def _client_port() -> int:
    raw = _require_env("ROBOT_WECHAT_CLIENT_PORT")
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("机器人客户端端口必须是整数") from exc
    if port < 1 or port > 65535:
        raise ValueError("机器人客户端端口必须在 1 到 65535 之间")
    return port


def _request_body() -> dict[str, Any]:
    body: dict[str, Any] = {
        "message_id": _positive_int_env("ROBOT_MESSAGE_ID"),
    }
    referenced_message_id = _optional_positive_int_env("ROBOT_REF_MESSAGE_ID")
    if referenced_message_id is not None:
        body["referenced_message_id"] = referenced_message_id
    return body


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Private-Token": _client_private_token(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"客户端返回 HTTP {exc.code}：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise RuntimeError(
                "请求超时，记忆可能已经保存，请先查询后再决定是否重试"
            ) from exc
        raise RuntimeError(f"无法连接机器人客户端：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("请求超时，记忆可能已经保存，请先查询后再决定是否重试") from exc
    except OSError as exc:
        raise RuntimeError(
            f"请求中断，记忆可能已经保存，请先查询后再决定是否重试：{exc}"
        ) from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("机器人客户端返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("机器人客户端返回格式错误")
    return payload


def _validate_success(payload: dict[str, Any]) -> None:
    if payload.get("code") != 200:
        message = str(payload.get("message") or "未知业务错误").strip()
        raise RuntimeError(message)


def main() -> int:
    try:
        args = _parse_args(sys.argv[1:])
        body = _request_body()
        port = _client_port()
        if args.dry_run:
            output = {"ok": True, "dry_run": True, "request": body}
        else:
            url = f"http://127.0.0.1:{port}/api/v1/robot/memories"
            payload = _post_json(url, body)
            _validate_success(payload)
            output = {
                "ok": True,
                "message": "长期记忆创建成功",
            }
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
        return 0
    except (ValueError, RuntimeError) as exc:
        sys.stdout.write(f"创建记忆失败：{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
