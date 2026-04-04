#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
import urllib.request
from pathlib import Path

# The skill runner consumes stdout, so route Python error output there as well.
sys.stderr = sys.stdout


def _skill_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent


def _skill_venv_python() -> Path:
    venv_dir = _skill_root() / ".venv"
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_skill_venv_python() -> None:
    venv_python = _skill_venv_python()
    if not venv_python.is_file():
        return

    current_python = Path(sys.executable).resolve()
    if current_python == venv_python.resolve():
        return

    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_skill_venv_python()

try:
    import pymysql  # type: ignore  # noqa: E402
except ModuleNotFoundError:
    sys.stdout.write(
        "缺少依赖 pymysql，请先执行 python3 text-to-image/scripts/bootstrap.py 安装当前 skill 的依赖\n"
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _mysql_connect():
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = int(os.environ.get("MYSQL_PORT", "3306"))
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("ROBOT_CODE", "")
    if not database:
        raise RuntimeError("环境变量 ROBOT_CODE 未配置")

    return pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=database, charset="utf8mb4",
        connect_timeout=10, read_timeout=30,
    )


def _query_one(conn, sql: str, params: tuple = ()) -> dict | None:
    cur = conn.cursor()
    cur.execute(sql, params)
    columns = [desc[0] for desc in cur.description] if cur.description else []
    row = cur.fetchone()
    cur.close()
    if row is None:
        return None
    return dict(zip(columns, row))


# ---------------------------------------------------------------------------
# Settings resolution (mirrors the Go service logic)
# ---------------------------------------------------------------------------

def load_drawing_settings(conn, from_wx_id: str) -> tuple[bool, dict]:
    """Return (enabled, image_ai_settings_dict)."""
    # 1. global_settings
    gs = _query_one(conn, "SELECT image_ai_enabled, image_ai_settings FROM global_settings LIMIT 1")
    enabled = False
    settings_json: dict = {}

    if gs:
        if gs.get("image_ai_enabled"):
            enabled = bool(gs["image_ai_enabled"])
        raw = gs.get("image_ai_settings")
        if raw:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            if isinstance(raw, str) and raw.strip():
                settings_json = json.loads(raw)

    # 2. override from chatroom / friend settings
    if from_wx_id.endswith("@chatroom"):
        override = _query_one(
            conn,
            "SELECT image_ai_enabled, image_ai_settings FROM chat_room_settings WHERE chat_room_id = %s LIMIT 1",
            (from_wx_id,),
        )
    else:
        override = _query_one(
            conn,
            "SELECT image_ai_enabled, image_ai_settings FROM friend_settings WHERE wechat_id = %s LIMIT 1",
            (from_wx_id,),
        )

    if override:
        if override.get("image_ai_enabled") is not None:
            enabled = bool(override["image_ai_enabled"])
        raw = override.get("image_ai_settings")
        if raw:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            if isinstance(raw, str) and raw.strip():
                settings_json = json.loads(raw)

    return enabled, settings_json


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def _http_post_json(url: str, body: dict, headers: dict, timeout: int = 300) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_jimeng(config: dict, prompt: str, model: str,
                negative_prompt: str, ratio: str, resolution: str) -> list[str]:
    """Call JiMeng (即梦) image generation API."""
    base_url = config.get("base_url", "").rstrip("/")
    session_ids = config.get("sessionid", [])
    if not base_url or not session_ids:
        raise RuntimeError("即梦绘图配置缺少 base_url 或 sessionid")

    if not model or model == "none":
        model = "jimeng-5.0"

    if not ratio:
        ratio = "16:9"
    if not resolution:
        resolution = "2k"

    # 如果分辨率大于4k，重置为2k
    m = re.search(r"(\d+)", resolution)
    if m and int(m.group(1)) > 4:
        resolution = "2k"

    token = ",".join(session_ids)
    body = {
        "model": model,
        "prompt": prompt,
        "ratio": ratio,
        "resolution": resolution,
        "response_format": "url",
        "sample_strength": 0.5,
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt

    resp = _http_post_json(
        f"{base_url}/v1/images/generations",
        body,
        {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=300,
    )
    urls = [item["url"] for item in resp.get("data", []) if item.get("url")]
    return urls


def call_doubao(config: dict, prompt: str, model: str) -> list[str]:
    """Call DouBao (豆包) image generation API."""
    api_key = config.get("api_key", "")
    if not api_key:
        raise RuntimeError("豆包绘图配置缺少 api_key")

    if not model or model == "none":
        model = "doubao-seedream-4.5"

    # Map friendly model names to actual endpoint model IDs
    model_map = {
        "doubao-seedream-4.5": "doubao-seedream-4-5-251128",
        "doubao-seedream-4.0": "doubao-seedream-4-0-251128",
        "doubao-seedream-3.0-t2i": "doubao-seedream-3-0-t2i-250415",
        "doubao-seededit-3.0-i2i": "doubao-seededit-3-0-i2i-250628",
    }
    actual_model = model_map.get(model, model)

    body = {
        "model": actual_model,
        "prompt": prompt,
        "response_format": "url",
        "size": config.get("size", "2K"),
        "sequential_image_generation": config.get("sequential_image_generation", "auto"),
        "watermark": config.get("watermark", False),
    }
    image_val = config.get("image", "")
    if image_val:
        body["image"] = image_val

    resp = _http_post_json(
        "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        body,
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        timeout=300,
    )
    urls = []
    for item in resp.get("data", []):
        url = item.get("url")
        if url:
            urls.append(url)
    return urls


def call_zimage(config: dict, prompt: str, model: str) -> list[str]:
    """Call Z-Image (造相) image generation API (async task-based)."""
    base_url = config.get("base_url", "").rstrip("/")
    api_key = config.get("api_key", "")
    if not base_url or not api_key:
        raise RuntimeError("造相绘图配置缺少 base_url 或 api_key")

    if not model or model == "none":
        model = "Z-Image-Turbo"

    # Map model names
    model_map = {
        "Z-Image": "Tongyi-MAI/Z-Image",
        "Z-Image-Turbo": "Tongyi-MAI/Z-Image-Turbo",
        "Qwen-Image-Edit-2511": "Qwen/Qwen-Image-Edit-2511",
    }
    actual_model = model_map.get(model)
    if actual_model is None:
        raise RuntimeError(f"不支持的造相模型: {model}")

    body = {
        "model": actual_model,
        "prompt": prompt,
        "image_url": config.get("image_url", []),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-ModelScope-Async-Mode": "true",
    }

    # Step 1: create task
    resp = _http_post_json(f"{base_url}/v1/images/generations", body, headers, timeout=30)
    task_id = resp.get("task_id", "")
    if not task_id:
        raise RuntimeError("造相接口未返回 task_id")

    # Step 2: poll for result
    poll_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-ModelScope-Task-Type": "image_generation",
    }
    deadline = time.time() + 15 * 60  # 15 minutes
    while time.time() < deadline:
        task_resp = _http_get_json(f"{base_url}/v1/tasks/{task_id}", poll_headers, timeout=30)
        status = task_resp.get("task_status", "")
        if status == "SUCCEED":
            images = task_resp.get("output_images", [])
            if images:
                return images
            raise RuntimeError("造相任务成功但未返回图片")
        if status == "FAILED":
            raise RuntimeError("造相绘图任务失败")
        time.sleep(5)

    raise RuntimeError("造相绘图任务超时")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

JIMENG_MODELS = {"jimeng-4.5", "jimeng-4.6", "jimeng-5.0"}
DOUBAO_MODELS = {"doubao-seedream-4.5", "doubao-seedream-4.0", "doubao-seedream-3.0-t2i", "doubao-seededit-3.0-i2i"}
ZIMAGE_MODELS = {"Z-Image", "Z-Image-Turbo", "Qwen-Image-Edit-2511"}


def main() -> int:
    # Parse input params from first CLI argument
    if len(sys.argv) < 2:
        sys.stdout.write("缺少输入参数\n")
        return 1

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    prompt = params.get("prompt", "").strip()
    if not prompt:
        sys.stdout.write("缺少画图提示词\n")
        return 1

    model = params.get("model", "").strip()
    negative_prompt = params.get("negative_prompt", "").strip()
    ratio = params.get("ratio", "").strip()
    resolution = params.get("resolution", "").strip()

    from_wx_id = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not from_wx_id:
        sys.stdout.write("环境变量 ROBOT_FROM_WX_ID 未配置\n")
        return 1

    # Connect to DB and load settings
    try:
        conn = _mysql_connect()
    except Exception as exc:
        sys.stdout.write(f"数据库连接失败: {exc}\n")
        return 1

    try:
        enabled, settings_json = load_drawing_settings(conn, from_wx_id)
    except Exception as exc:
        conn.close()
        sys.stdout.write(f"加载绘图配置失败: {exc}\n")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not enabled:
        sys.stdout.write("AI 绘图未开启\n")
        return 0

    # Default model
    if not model or model == "none":
        model = "jimeng-5.0"

    # Route to correct API
    try:
        image_urls: list[str] = []

        if model in JIMENG_MODELS:
            jimeng_config = settings_json.get("JiMeng", {})
            if not jimeng_config.get("enabled", False):
                sys.stdout.write("即梦绘图未开启\n")
                return 0
            image_urls = call_jimeng(jimeng_config, prompt, model, negative_prompt, ratio, resolution)

        elif model in DOUBAO_MODELS:
            doubao_config = settings_json.get("DouBao", {})
            if not doubao_config.get("enabled", False):
                sys.stdout.write("豆包绘图未开启\n")
                return 0
            image_urls = call_doubao(doubao_config, prompt, model)

        elif model in ZIMAGE_MODELS:
            zimage_config = settings_json.get("Z-Image", {})
            if not zimage_config.get("enabled", False):
                sys.stdout.write("造相绘图未开启\n")
                return 0
            image_urls = call_zimage(zimage_config, prompt, model)

        else:
            sys.stdout.write("不支持的 AI 图像模型\n")
            return 1

    except Exception as exc:
        sys.stdout.write(f"调用绘图接口失败: {exc}\n")
        return 1

    if not image_urls:
        sys.stdout.write("未生成任何图像\n")
        return 1

    for url in image_urls:
        if url:
            sys.stdout.write(f"<wechat-robot-image-url>{url}</wechat-robot-image-url>")

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
