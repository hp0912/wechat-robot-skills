#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.message import Message
from pathlib import Path
from typing import Any

sys.stderr = sys.stdout

MEDIA_TYPES = ("image", "video", "voice")
MEDIA_MESSAGE_TYPES: dict[str, set[int]] = {
    "image": {3},
    "video": {43},
    "voice": {34},
}
MESSAGE_TYPE_TO_MEDIA_TYPE = {
    message_type: media_type
    for media_type, message_types in MEDIA_MESSAGE_TYPES.items()
    for message_type in message_types
}
DOWNLOAD_PATHS = {
    "image": "/api/v1/robot/chat/image/download",
    "video": "/api/v1/robot/chat/video/download",
    "voice": "/api/v1/robot/chat/voice/download",
}
DEFAULT_EXTENSIONS = {
    "image": ".jpg",
    "video": ".mp4",
    "voice": ".wav",
}
MEDIA_LABELS = {
    "image": "图片",
    "video": "视频",
    "voice": "语音",
}

MAX_COUNT = 5
HISTORY_MINUTES = 10


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


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

    venv_dir = (_skill_root() / ".venv").resolve()
    if Path(sys.prefix).resolve() == venv_dir:
        return

    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_skill_venv_python()

try:
    import pymysql  # type: ignore[import-untyped]  # noqa: E402
except ModuleNotFoundError:
    _run_bootstrap()
    python_executable = _get_python_executable()
    os.execv(python_executable, [python_executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def _mysql_connect() -> Any:
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = int(os.environ.get("MYSQL_PORT", "3306"))
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("ROBOT_CODE", "")
    if not database:
        raise RuntimeError("环境变量 ROBOT_CODE 未配置")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        cursorclass=pymysql.cursors.DictCursor,
    )


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


def _parse_media_types(single_values: list[str], array_values: list[str]) -> list[str]:
    values = _expand_json_array_values(single_values + array_values, "media_types")
    if not values:
        raise ValueError("缺少 media_type")

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        media_type = value.strip().lower()
        media_candidates = list(MEDIA_TYPES) if media_type == "all" else [media_type]
        for candidate in media_candidates:
            if candidate not in MEDIA_MESSAGE_TYPES:
                raise ValueError(f"不支持的媒体类型: {value}")
            if candidate not in seen:
                seen.add(candidate)
                result.append(candidate)
    return result


def _parse_cli_params(argv: list[str]) -> tuple[list[str], int]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--media_type", action="append", default=[])
    parser.add_argument("--media_types", action="append", default=[])
    parser.add_argument("--count", type=int, default=1)

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    media_types = _parse_media_types(namespace.media_type, namespace.media_types)
    if namespace.count <= 0:
        raise ValueError("count 必须大于 0")
    count = min(namespace.count, MAX_COUNT)
    return media_types, count


def _client_base_url(client_port: str) -> str:
    return f"http://127.0.0.1:{client_port}"


def _build_url(base_url: str, path: str, params: dict[str, object] | None = None) -> str:
    url = f"{base_url}{path}"
    if not params:
        return url
    query = urllib.parse.urlencode(params)
    return f"{url}?{query}"


def _http_get_bytes(url: str, timeout: int = 300) -> tuple[bytes, dict[str, str]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            headers = {key.lower(): value for key, value in resp.headers.items()}
            return resp.read(), headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc


def _http_post_multipart(
    url: str,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    content_type: str,
    data: bytes,
    timeout: int = 300,
) -> dict[str, Any]:
    boundary = f"----wechatRobotSkill{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")

    safe_filename = filename.replace('"', "_")
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_filename}"\r\n'.encode("utf-8"))
    chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    chunks.append(data)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(chunks)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    if not text.strip():
        return {}
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError("接口响应不是 JSON 对象")
    return payload


def _check_api_payload(payload: dict[str, Any], action: str) -> Any:
    code = payload.get("code")
    if code not in (None, 200):
        message = payload.get("message") or "接口返回失败"
        raise RuntimeError(f"{action}失败: {message}")
    return payload.get("data")


def _to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _fetch_history_media_messages(
    conn: Any,
    from_wx_id: str,
    sender_wx_id: str,
    wanted_media_types: list[str],
    start_time: int,
    end_time: int,
    limit: int,
) -> list[dict[str, Any]]:
    message_types = sorted({message_type for media_type in wanted_media_types for message_type in MEDIA_MESSAGE_TYPES[media_type]})
    if not message_types:
        return []

    placeholders = ", ".join(["%s"] * len(message_types))
    sql = f"""
        SELECT id, type, from_wxid, sender_wxid, created_at
        FROM messages
        WHERE from_wxid = %s
          AND sender_wxid = %s
          AND created_at >= %s
          AND created_at <= %s
          AND `type` IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        LIMIT %s
    """
    params: tuple[Any, ...] = (from_wx_id, sender_wx_id, start_time, end_time, *message_types, limit)

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    messages: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        media_type = MESSAGE_TYPE_TO_MEDIA_TYPE.get(_to_int(row.get("type")), "")
        if not media_type:
            continue
        message = dict(row)
        message["media_type"] = media_type
        messages.append(message)

    return sorted(messages, key=lambda item: (_to_int(item.get("created_at")), _to_int(item.get("id"))))


def _filename_from_content_disposition(value: str) -> str:
    if not value:
        return ""
    message = Message()
    message["content-disposition"] = value
    filename = message.get_filename()
    if filename:
        return filename
    match = re.search(r'filename="?([^";]+)"?', value)
    if match:
        return match.group(1).strip()
    return ""


def _extension_from_download(headers: dict[str, str], media_type: str, message_id: int) -> tuple[str, str, str]:
    content_type = headers.get("content-type", "").split(";", 1)[0].strip() or "application/octet-stream"
    filename = _filename_from_content_disposition(headers.get("content-disposition", ""))
    extension = ""
    if "." in filename:
        extension = "." + filename.rsplit(".", 1)[-1].strip().lower()
    if not extension:
        extension = mimetypes.guess_extension(content_type) or ""
    if not extension:
        extension = DEFAULT_EXTENSIONS[media_type]
    if not extension.startswith("."):
        extension = "." + extension
    if not filename:
        filename = f"{message_id}{extension}"
    return filename, content_type, extension


def _download_media(base_url: str, message_id: int, media_type: str) -> tuple[bytes, str, str, str]:
    path = DOWNLOAD_PATHS[media_type]
    url = _build_url(base_url, path, {"message_id": message_id})
    data, headers = _http_get_bytes(url)
    if not data:
        raise RuntimeError(f"下载{MEDIA_LABELS[media_type]}失败: 响应为空")
    filename, content_type, extension = _extension_from_download(headers, media_type, message_id)
    return data, filename, content_type, extension


def _upload_media(base_url: str, message_id: int, media_type: str, data: bytes, filename: str, content_type: str, extension: str) -> str:
    url = _build_url(base_url, "/api/v1/robot/chat/media/upload")
    payload = _http_post_multipart(
        url,
        {
            "message_id": str(message_id),
            "media_type": media_type,
            "extension": extension,
        },
        "media",
        filename,
        content_type,
        data,
    )
    response_data = _check_api_payload(payload, "上传媒体到 CDN")
    if not isinstance(response_data, dict):
        raise RuntimeError("上传媒体到 CDN 失败: 响应 data 格式错误")
    media_url = str(response_data.get("url") or "").strip()
    if not media_url:
        raise RuntimeError("上传媒体到 CDN 失败: 未返回 URL")
    return media_url


def _media_label(media_types: list[str]) -> str:
    return "/".join(MEDIA_LABELS[media_type] for media_type in media_types)


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少媒体类型参数\n")
        return 1

    try:
        media_types, count = _parse_cli_params(sys.argv[1:])
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        sys.stdout.write("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置\n")
        return 1

    from_wx_id = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not from_wx_id:
        sys.stdout.write("环境变量 ROBOT_FROM_WX_ID 未配置\n")
        return 1

    sender_wx_id = os.environ.get("ROBOT_SENDER_WX_ID", "").strip()
    if not sender_wx_id:
        sys.stdout.write("环境变量 ROBOT_SENDER_WX_ID 未配置\n")
        return 1

    base_url = _client_base_url(client_port)
    end_time = int(time.time())
    start_time = end_time - HISTORY_MINUTES * 60

    try:
        conn = _mysql_connect()
    except Exception as exc:
        sys.stdout.write(f"数据库连接失败: {exc}\n")
        return 1

    try:
        selected_messages = _fetch_history_media_messages(conn, from_wx_id, sender_wx_id, media_types, start_time, end_time, count)
    except Exception as exc:
        sys.stdout.write(f"查询历史媒体失败: {exc}\n")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not selected_messages:
        label = _media_label(media_types)
        sys.stdout.write(f"未找到十分钟内由你在当前会话发送的{label}，你要先发送一条{label}再让我处理。\n")
        return 0

    items: list[dict[str, Any]] = []
    urls_by_type: dict[str, list[str]] = {"image": [], "video": [], "voice": []}
    try:
        for message in selected_messages:
            message_id = _to_int(message.get("id"))
            media_type = str(message.get("media_type") or "")
            if message_id <= 0 or media_type not in MEDIA_MESSAGE_TYPES:
                continue
            data, filename, content_type, extension = _download_media(base_url, message_id, media_type)
            media_url = _upload_media(base_url, message_id, media_type, data, filename, content_type, extension)
            urls_by_type[media_type].append(media_url)
            items.append(
                {
                    "message_id": message_id,
                    "media_type": media_type,
                    "created_at": _to_int(message.get("created_at")),
                    "url": media_url,
                }
            )
    except Exception as exc:
        sys.stdout.write(f"下载或上传历史媒体失败: {exc}\n")
        return 1

    result = {
        "media_urls": [item["url"] for item in items],
        "image_urls": urls_by_type["image"],
        "video_urls": urls_by_type["video"],
        "voice_urls": urls_by_type["voice"],
        "items": items,
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
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
