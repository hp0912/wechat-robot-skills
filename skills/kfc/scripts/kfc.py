#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import traceback
import urllib.error
import urllib.request


sys.stderr = sys.stdout


API_URL = "https://api.pearktrue.cn/api/kfc?type=json"
FALLBACK_TEXT = "今天的肯德基文案暂时没拿到，等我再去问问。"


def fetch_kfc_copy() -> str:
    try:
        with urllib.request.urlopen(API_URL, timeout=10) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return FALLBACK_TEXT

    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        # 该 API 偶尔返回双重转义的换行符（字面量 \n），在此统一还原
        return "<wechat-robot-text>" + text.replace("\\n", "\n") + "</wechat-robot-text>"
    return FALLBACK_TEXT


def main() -> int:
    sys.stdout.write(fetch_kfc_copy())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)