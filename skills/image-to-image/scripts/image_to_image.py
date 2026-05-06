#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
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


def _get_python_executable() -> str:
    if sys.executable:
        return sys.executable
    import shutil
    for candidate in ("python3", "python"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("无法找到 Python 解释器路径")


def _run_bootstrap() -> None:
    bootstrap = Path(__file__).resolve().parent / "bootstrap.py"
    result = subprocess.run([_get_python_executable(), str(bootstrap)])
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _ensure_skill_venv_python() -> None:
    venv_python = _skill_venv_python()
    if not venv_python.is_file():
        _run_bootstrap()
        venv_python = _skill_venv_python()
        if not venv_python.is_file():
            sys.stdout.write("bootstrap 后仍未找到虚拟环境\n")
            raise SystemExit(1)

    venv_dir = _skill_root() / ".venv"
    if Path(sys.prefix) == venv_dir.resolve():
        return

    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_skill_venv_python()

try:
    import pymysql  # type: ignore  # noqa: E402
    from openai import OpenAI  # type: ignore  # noqa: E402
except ModuleNotFoundError:
    _run_bootstrap()
    _py = _get_python_executable()
    os.execv(_py, [_py, str(Path(__file__).resolve()), *sys.argv[1:]])


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


def _coerce_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _openai_output_format(config: dict) -> str:
    output_format = str(config.get("output_format", "png") or "png").lower()
    if output_format not in {"png", "jpeg", "webp"}:
        return "png"
    return output_format


def _openai_size(config: dict, ratio: str, resolution: str) -> str:
    configured = str(config.get("size", "") or "").strip()
    if configured:
        return configured

    normalized_ratio = (ratio or "").replace(" ", "").lower()
    normalized_resolution = (resolution or "").replace(" ", "").lower()

    if normalized_resolution in {"4k", "2160p", "3840x2160"}:
        sizes = {
            "16:9": "3840x2160",
            "9:16": "2160x3840",
            "1:1": "2048x2048",
            "3:2": "3072x2048",
            "2:3": "2048x3072",
        }
    elif normalized_resolution in {"2k", "1440p", "2048"}:
        sizes = {
            "16:9": "2048x1152",
            "9:16": "1152x2048",
            "1:1": "2048x2048",
            "3:2": "2048x1360",
            "2:3": "1360x2048",
        }
    elif normalized_resolution in {"1k", "1024", "1024p"}:
        sizes = {
            "16:9": "1536x864",
            "9:16": "864x1536",
            "1:1": "1024x1024",
            "3:2": "1536x1024",
            "2:3": "1024x1536",
        }
    else:
        return "auto"

    return sizes.get(normalized_ratio, "auto")


def _openai_prompt(prompt: str, negative_prompt: str) -> str:
    if not negative_prompt:
        return prompt
    return f"{prompt}\n\n不要包含: {negative_prompt}"


def _openai_client(config: dict) -> OpenAI:
    api_key = str(config.get("api_key", "")).strip()
    if not api_key:
        raise RuntimeError("OpenAI 绘图配置缺少 api_key")

    base_url = str(config.get("base_url", "") or "").strip()
    organization = str(config.get("organization", "") or "").strip()
    project = str(config.get("project", "") or "").strip()
    timeout: float | None = None
    timeout_value = config.get("timeout")
    if timeout_value not in (None, ""):
        timeout = float(timeout_value)

    return OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        organization=organization or None,
        project=project or None,
        timeout=timeout,
    )


def _truncate_debug_payload(value):
    if isinstance(value, dict):
        return {
            key: (
                f"{item[:50]}..." if key == "b64_json" and isinstance(item, str) and len(item) > 50 else _truncate_debug_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_truncate_debug_payload(item) for item in value]
    return value


def _debug_response(label: str, payload) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    payload = _truncate_debug_payload(payload)
    sys.stdout.write(f"[debug] {label}: {json.dumps(payload, ensure_ascii=False)}\n")


def _rewrite_openai_image_url(url: str) -> str:
    internal_host = "http://chatgpt2api:80"
    external_host = "https://chatgpt2api.houhoukang.com"
    if url.startswith(internal_host):
        return f"{external_host}{url[len(internal_host):]}"
    return url


def _extension_from_output_format(output_format: str) -> str:
    if output_format == "jpeg":
        return ".jpg"
    if output_format == "webp":
        return ".webp"
    return ".png"


def _openai_response_value(item, key: str):
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _write_openai_b64_image(b64_json: str, output_format: str) -> str:
    encoded = b64_json.strip()
    suffix = _extension_from_output_format(output_format)
    if encoded.startswith("data:"):
        header, encoded = encoded.split(",", 1)
        mime_type = header[5:].split(";", 1)[0].strip().lower()
        if mime_type:
            suffix = _extension_from_mime(mime_type)

    encoded = "".join(encoded.split())
    padding = len(encoded) % 4
    if padding:
        encoded = f"{encoded}{'=' * (4 - padding)}"

    image_bytes = base64.b64decode(encoded)
    with tempfile.NamedTemporaryFile(prefix="wechat-openai-image-", suffix=suffix, delete=False) as temp_file:
        temp_file.write(image_bytes)
        return temp_file.name


def _openai_images_from_response(response, output_format: str) -> list[str]:
    outputs: list[str] = []
    try:
        for item in getattr(response, "data", []) or []:
            b64_json = _openai_response_value(item, "b64_json")
            if b64_json:
                outputs.append(_write_openai_b64_image(str(b64_json), output_format))
                continue

            url = _openai_response_value(item, "url")
            if url:
                outputs.append(_rewrite_openai_image_url(str(url)))
    except Exception:
        _cleanup_openai_temp_files(outputs)
        raise
    return outputs


def _is_remote_image_url(value: str) -> bool:
    return urllib.parse.urlparse(value).scheme in {"http", "https"}


def _send_image_outputs(client_port: str, from_wx_id: str, image_outputs: list[str]) -> None:
    remote_urls = [value for value in image_outputs if value and _is_remote_image_url(value)]
    local_paths = [value for value in image_outputs if value and not _is_remote_image_url(value)]

    if remote_urls:
        send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/image/url"
        send_body = {
            "to_wxid": from_wx_id,
            "image_urls": remote_urls,
        }
        response = _http_post_json(send_url, send_body, {"Content-Type": "application/json"}, timeout=300)
        _debug_response("send image url response", response)

    for file_path in local_paths:
        send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/image/local"
        send_body = {
            "to_wxid": from_wx_id,
            "file_path": file_path,
        }
        response = _http_post_json(send_url, send_body, {"Content-Type": "application/json"}, timeout=300)
        _debug_response("send image local response", response)


def _cleanup_openai_temp_files(image_outputs: list[str]) -> None:
    for value in image_outputs:
        path = Path(value)
        if path.name.startswith("wechat-openai-image-") and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def _extension_from_mime(mime_type: str) -> str:
    if mime_type == "image/jpeg":
        return ".jpg"
    guessed = mimetypes.guess_extension(mime_type)
    if guessed in {".png", ".jpg", ".jpeg", ".webp"}:
        return guessed
    return ".png"


def _download_openai_input_image(image: str, directory: str, index: int) -> Path:
    stripped = image.strip()
    if stripped.startswith("data:"):
        header, encoded = stripped.split(",", 1)
        mime_type = header[5:].split(";", 1)[0] or "image/png"
        path = Path(directory) / f"input-{index}{_extension_from_mime(mime_type)}"
        path.write_bytes(base64.b64decode(encoded))
        return path

    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme in {"http", "https"}:
        request = urllib.request.Request(stripped, headers={"User-Agent": "wechat-robot-skills/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "image/png").split(";", 1)[0].strip()
            suffix = Path(parsed.path).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = _extension_from_mime(content_type)
            path = Path(directory) / f"input-{index}{suffix}"
            path.write_bytes(response.read())
            return path

    path = Path(stripped).expanduser()
    if path.is_file():
        return path
    raise RuntimeError(f"无法读取图片: {image}")


def call_jimeng(config: dict, prompt: str, model: str, images: list[str],
                negative_prompt: str, ratio: str, resolution: str) -> list[str]:
    """Call JiMeng (即梦) image compositions API (图生图)."""
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
        "images": images,
        "ratio": ratio,
        "resolution": resolution,
        "response_format": "url",
        "sample_strength": 0.5,
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt

    # 图生图使用 /v1/images/compositions 端点
    resp = _http_post_json(
        f"{base_url}/v1/images/compositions",
        body,
        {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=300,
    )
    urls = [item["url"] for item in resp.get("data", []) if item.get("url")]
    return urls


def call_doubao(config: dict, prompt: str, model: str, image: str) -> list[str]:
    """Call DouBao (豆包) image-to-image API."""
    api_key = config.get("api_key", "")
    if not api_key:
        raise RuntimeError("豆包绘图配置缺少 api_key")

    if not model or model == "none":
        model = "doubao-seededit-3.0-i2i"

    model_map = {
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
    if image:
        body["image"] = image

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


def call_zimage(config: dict, prompt: str, model: str, images: list[str]) -> list[str]:
    """Call Z-Image (造相) image generation API (async task-based)."""
    base_url = config.get("base_url", "").rstrip("/")
    api_key = config.get("api_key", "")
    if not base_url or not api_key:
        raise RuntimeError("造相绘图配置缺少 base_url 或 api_key")

    if not model or model == "none":
        model = "Qwen-Image-Edit-2511"

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
        "image_url": images,
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
            images_result = task_resp.get("output_images", [])
            if images_result:
                return images_result
            raise RuntimeError("造相任务成功但未返回图片")
        if status == "FAILED":
            raise RuntimeError("造相绘图任务失败")
        time.sleep(5)

    raise RuntimeError("造相绘图任务超时")


def call_openai(config: dict, prompt: str, model: str, images: list[str],
                negative_prompt: str, ratio: str, resolution: str) -> list[str]:
    """Call OpenAI GPT Image API for image editing."""
    client = _openai_client(config)
    output_format = _openai_output_format(config)
    quality = str(config.get("quality", "auto") or "auto")
    background = str(config.get("background", "auto") or "auto")
    if background == "transparent":
        background = "auto"

    with tempfile.TemporaryDirectory() as temp_dir:
        input_paths = [
            _download_openai_input_image(image, temp_dir, index)
            for index, image in enumerate(images[:16], start=1)
        ]
        input_files = [path.open("rb") for path in input_paths]
        try:
            kwargs = {
                "model": model or "gpt-image-2",
                "prompt": _openai_prompt(prompt, negative_prompt),
                "image": input_files,
                "n": _coerce_int(config.get("n"), 1, 1, 10),
                "size": _openai_size(config, ratio, resolution),
                "quality": quality,
                "background": background,
                "output_format": output_format,
            }
            if output_format in {"jpeg", "webp"} and config.get("output_compression") is not None:
                kwargs["output_compression"] = _coerce_int(config.get("output_compression"), 100, 0, 100)

            response = client.images.edit(**kwargs)
        finally:
            for input_file in input_files:
                input_file.close()

    _debug_response("openai images.edit response", response)
    return _openai_images_from_response(response, output_format)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

JIMENG_MODELS = {"jimeng-4.5", "jimeng-4.6", "jimeng-5.0"}
DOUBAO_MODELS = {"doubao-seededit-3.0-i2i"}
ZIMAGE_MODELS = {"Z-Image", "Z-Image-Turbo", "Qwen-Image-Edit-2511"}
OPENAI_MODELS = {"gpt-image-2"}


def _parse_cli_params(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--images", action="append", default=[])
    parser.add_argument("--model", default="")
    parser.add_argument("--negative_prompt", default="")
    parser.add_argument("--ratio", default="")
    parser.add_argument("--resolution", default="")

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    return {
        "prompt": namespace.prompt,
        "images": [img for img in namespace.images if img.strip()],
        "model": namespace.model,
        "negative_prompt": namespace.negative_prompt,
        "ratio": namespace.ratio,
        "resolution": namespace.resolution,
    }


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少输入参数\n")
        return 1

    try:
        params = _parse_cli_params(sys.argv[1:])
    except ValueError as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    prompt = params.get("prompt", "").strip()
    if not prompt:
        sys.stdout.write("缺少提示词\n")
        return 1

    images = params.get("images", [])
    if not images:
        sys.stdout.write("图片链接列表为空\n")
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
            image_urls = call_jimeng(jimeng_config, prompt, model, images, negative_prompt, ratio, resolution)

        elif model in DOUBAO_MODELS:
            doubao_config = settings_json.get("DouBao", {})
            if not doubao_config.get("enabled", False):
                sys.stdout.write("豆包绘图未开启\n")
                return 0
            # 豆包图生图只支持单张图片
            image_urls = call_doubao(doubao_config, prompt, model, images[0])

        elif model in ZIMAGE_MODELS:
            zimage_config = settings_json.get("Z-Image", {})
            if not zimage_config.get("enabled", False):
                sys.stdout.write("造相绘图未开启\n")
                return 0
            image_urls = call_zimage(zimage_config, prompt, model, images)

        elif model in OPENAI_MODELS:
            openai_config = settings_json.get("OpenAI", {})
            if not openai_config.get("enabled", False):
                sys.stdout.write("OpenAI 绘图未开启\n")
                return 0
            image_urls = call_openai(openai_config, prompt, model, images, negative_prompt, ratio, resolution)

        else:
            sys.stdout.write("不支持的 AI 图像模型\n")
            return 1

    except Exception as exc:
        sys.stdout.write(f"调用绘图接口失败: {exc}\n")
        return 1

    if not image_urls:
        sys.stdout.write("未生成任何图像\n")
        return 1

    # 通过客户端接口发送图片
    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        _cleanup_openai_temp_files(image_urls)
        sys.stdout.write("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置\n")
        return 1

    try:
        _send_image_outputs(client_port, from_wx_id, image_urls)
        sys.stdout.write("图片发送成功\n")
    except Exception as exc:
        sys.stdout.write(f"发送图片失败: {exc}\n")
        return 1
    finally:
        _cleanup_openai_temp_files(image_urls)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)
