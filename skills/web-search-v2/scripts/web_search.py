#!/usr/bin/env python3
"""Search and fetch web pages using the DeepSeek tools API.

API reference: https://api-docs.deepseek.com/zh-cn/guides/search/
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, NoReturn

API_HOST = "https://api.deepseek.com"
MAX_RETRIES = 2

HTTP_ERROR_HINTS = {
    400: "请求参数不合法",
    401: "API Key 无效，请检查 DEEPSEEK_API_KEY",
    402: "账号余额不足",
    403: "网页抓取被安全策略拒绝",
    404: "未能获取可用的网页正文",
    408: "请求超时",
    413: "请求体过大",
    429: "请求频率超过限制",
    500: "DeepSeek 服务内部错误",
    504: "下游请求超时",
}


class SkillError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SkillError(f"参数错误：{message}")


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep Bearer credentials on the fixed API host.
        return None


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = SkillArgumentParser(description="通过 DeepSeek 搜索网页或抓取网页正文")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query", help="搜索词，1～128 个字符")
    mode.add_argument("--url", help="要抓取的 HTTP / HTTPS 网页地址")
    parser.add_argument("--count", type=int, help="搜索结果数量，1～50，默认 10")
    parser.add_argument("--ori-question", help="用户原始问题，1～128 个字符")
    parser.add_argument("--localization", help="用于本地化搜索的 IP 地址")
    parser.add_argument("--need-content", action="store_true", help="同时获取搜索结果正文")
    parser.add_argument("--timeout", type=int, default=60, help="每次请求超时秒数，默认 60")
    return parser.parse_args(argv)


def _validate_query(value: str, name: str) -> str:
    if not value.strip() or len(value) > 128:
        raise SkillError(f"{name} 必须为 1～128 个字符，且不能全为空白")
    return value


def _validate_url(value: str) -> str:
    if not value or len(value) > 8192 or any(char.isspace() for char in value):
        raise SkillError("url 必须为不超过 8192 个字符且不含空白的 HTTP / HTTPS 地址")
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
        parsed.port  # Validate the port when present.
    except ValueError:
        valid = False
    if not valid:
        raise SkillError("url 必须为含主机名且不带用户名或密码的 HTTP / HTTPS 地址")
    return value


def _build_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.timeout <= 0:
        raise SkillError("timeout 必须为正整数")
    if args.url is not None:
        if (
            args.count is not None
            or args.ori_question is not None
            or args.localization is not None
            or args.need_content
        ):
            raise SkillError("url 不能与 count、ori-question、localization、need-content 混用")
        return "/v1/tools/fetch", {"url": _validate_url(args.url)}

    count = 10 if args.count is None else args.count
    if not 1 <= count <= 50:
        raise SkillError("count 必须在 1～50 之间")
    body: dict[str, Any] = {
        "query": _validate_query(args.query, "query"),
        "count": count,
        "need_content": args.need_content,
    }
    if args.ori_question is not None:
        body["ori_question"] = _validate_query(args.ori_question, "ori-question")
    if args.localization is not None:
        try:
            ipaddress.ip_address(args.localization)
        except ValueError as exc:
            raise SkillError("localization 必须为有效的 IPv4 或 IPv6 地址") from exc
        body["localization"] = args.localization
    return "/v1/tools/search", body


def _http_error(exc: urllib.error.HTTPError) -> SkillError:
    message = HTTP_ERROR_HINTS.get(exc.code, "接口请求失败")
    try:
        data = json.loads(exc.read(8192).decode("utf-8"))
        error = data.get("error") if isinstance(data, dict) else None
        detail = error.get("message") if isinstance(error, dict) else None
        if isinstance(detail, str) and detail.strip():
            message = f"{message}：{detail}"
    except (ValueError, OSError, http.client.HTTPException):
        pass
    finally:
        exc.close()
    return SkillError(f"DeepSeek HTTP {exc.code}：{message}", exc.code)


def _post_json(endpoint: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SkillError("环境变量 DEEPSEEK_API_KEY 未配置")
    request = urllib.request.Request(
        API_HOST + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    for attempt in range(MAX_RETRIES + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise SkillError("DeepSeek 返回了无效的 JSON 对象")
            return data
        except urllib.error.HTTPError as exc:
            error = _http_error(exc)
            if attempt == MAX_RETRIES or not (exc.code in {408, 429} or 500 <= exc.code < 600):
                raise error from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == MAX_RETRIES:
                raise SkillError(f"DeepSeek 网络请求失败：{exc}") from exc
        except (ValueError, UnicodeError) as exc:
            raise SkillError("DeepSeek 返回了无法解析的 JSON") from exc
        time.sleep(2**attempt)
    raise SkillError("DeepSeek 请求失败")


def _format_result(body: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if "query" in body:
        results = data.get("results")
        if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
            raise SkillError("DeepSeek 搜索响应缺少有效的 results 数组")
        return {"ok": True, "query": body["query"], "results": results}

    if not isinstance(data.get("title"), str) or not isinstance(data.get("content"), str):
        raise SkillError("DeepSeek 抓取响应缺少有效的 title 或 content")
    if not data["content"].strip():
        raise SkillError("DeepSeek 未返回可用的网页正文")
    return {"ok": True, "url": body["url"], "title": data["title"], "content": data["content"]}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(sys.argv[1:] if argv is None else argv)
        endpoint, body = _build_request(args)
        data = _post_json(endpoint, body, args.timeout)
        result = _format_result(body, data)
    except SkillError as exc:
        message = str(exc)
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        result = {"ok": False, "error": message[:1000]}
        if exc.status_code is not None:
            result["status_code"] = exc.status_code
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
