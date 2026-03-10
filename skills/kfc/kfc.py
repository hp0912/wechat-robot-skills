#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


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
        return text.replace("\\n", "\n")
    return FALLBACK_TEXT


def main() -> int:
    sys.stdout.write(fetch_kfc_copy())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())