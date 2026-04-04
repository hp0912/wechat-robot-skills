#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request


sys.stderr = sys.stdout


FETCH_API_URL = "https://api.pearktrue.cn/api/today_wife"
FALLBACK_TEXT = "今天的美女图片暂时没拿到，等我再找找。"


def fetch_image_url() -> str | None:
    try:
        with urllib.request.urlopen(FETCH_API_URL, timeout=10) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    image_url = data.get("image_url")
    if isinstance(image_url, str) and image_url.strip():
        return image_url.strip()
    return None


def send_image(image_url: str) -> bool:
    robot_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    to_wxid = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not robot_port or not to_wxid:
        return False

    api_url = (
        f"http://127.0.0.1:{robot_port}/api/v1/robot/message/send/image/url"
    )
    body = json.dumps(
        {
            "to_wxid": to_wxid,
            "image_urls": [image_url],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if 200 <= response.status < 300:
                return True
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False

    code = payload.get("code")
    return code == 200 or code == 0


def main() -> int:
    image_url = fetch_image_url()
    if image_url and send_image(image_url):
        return 0

    sys.stdout.write(FALLBACK_TEXT)
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