#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.stderr = sys.stdout

# ============================================================
# 表情映射表 —— 对 AI 模型透明，脚本内部维护
# 格式: "[名称]" -> {"Md5": str, "TotalLen": int}
# ============================================================
EMOJI_MAP: dict[str, dict[str, object]] = {
    "[调皮]": {
        "Md5": "da56c104712858765ff6edb61083c4ab",
        "TotalLen": 10250946,
    },
    "[无语]": {
        "Md5": "8690c66ab09a767cb5f7c7818c0a517f",
        "TotalLen": 26296,
    },
    "[爱心]": {
        "Md5": "1b44ec167ccb38d8260a57bccaa516e5",
        "TotalLen": 8337713,
    },
    "[安慰]": {
        "Md5": "ad2f9eb42093bf73190cef07d8958e6b",
        "TotalLen": 418037,
    },
    "[嘲笑]": {
        "Md5": "16644536f3300a81d96f712a399ff92e",
        "TotalLen": 205506,
    },
    "[傻瓜]": {
        "Md5": "45484d7b1133b16e68285943a6dc1ba3",
        "TotalLen": 74925,
    },
    "[厉害]": {
        "Md5": "de4fb8aff77f474746efa3e7a6d638dc",
        "TotalLen": 57770,
    },
    "[生气]": {
        "Md5": "b74fc587508b4b1ce13009ed73585f44",
        "TotalLen": 59990,
    },
    "[开心]": {
        "Md5": "4aae9dbe27651859ed526bcaad5f1f34",
        "TotalLen": 19788,
    },
}


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


def _expand_json_array_values(values: list[str], label: str) -> list[str]:
    expanded: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                raise ValueError(f"{label} 必须是字符串数组")
            for item in parsed:
                if not isinstance(item, str):
                    raise ValueError(f"{label} 必须是字符串数组")
                if item.strip():
                    expanded.append(item.strip())
            continue
        expanded.append(stripped)
    return expanded


def _parse_cli_params(argv: list[str]) -> tuple[list[str], bool]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--name", action="append", default=[])
    parser.add_argument("--names", action="append", default=[])
    parser.add_argument("--ended", action="store_true", default=False)

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    names = _expand_json_array_values(namespace.name + namespace.names, "names")
    # 去重，保持顺序
    deduped: list[str] = []
    seen = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)

    return deduped, namespace.ended


def _lookup_emoji(name: str) -> dict[str, object]:
    """在 EMOJI_MAP 中查找表情，未找到抛出 ValueError。"""
    if name not in EMOJI_MAP:
        available = ", ".join(EMOJI_MAP.keys())
        raise ValueError(f"未知表情: {name}，可用表情: {available}")
    return EMOJI_MAP[name]


def _send_emoji(client_port: str, to_wxid: str, md5: str, total_len: int) -> None:
    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/emoji"
    _http_post_json(send_url, {"to_wxid": to_wxid, "Md5": md5, "TotalLen": total_len})


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少表情名称（需要 --name）\n")
        return 1

    try:
        names, ended = _parse_cli_params(sys.argv[1:])
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    if not names:
        sys.stdout.write("缺少表情名称（需要 --name）\n")
        return 1

    # 查找表情
    emojis: list[dict[str, object]] = []
    try:
        for name in names:
            emojis.append(_lookup_emoji(name))
    except ValueError as exc:
        sys.stdout.write(f"{exc}\n")
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
        for emoji in emojis:
            md5 = str(emoji["Md5"])
            total_len = int(emoji["TotalLen"])  # type: ignore[arg-type]
            _send_emoji(client_port, to_wxid, md5, total_len)
        sys.stdout.write("表情发送成功\n")
        if ended:
            sys.stdout.write("ended")
        return 0
    except Exception as exc:
        sys.stdout.write(f"表情发送失败: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
