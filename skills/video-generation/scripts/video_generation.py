#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
import urllib.request
from pathlib import Path

sys.stderr = sys.stdout


SUPPORTED_MODELS = {
    "jimeng-video-seedance-2.0",
    "jimeng-video-3.5-pro",
    "jimeng-video-veo3",
    "jimeng-video-veo3.1",
    "jimeng-video-sora2",
    "jimeng-video-3.0-pro",
    "jimeng-video-3.0",
    "jimeng-video-3.0-fast",
}
DEFAULT_MODEL = "jimeng-video-3.0-fast"
DEFAULT_RATIO = "4:3"
DEFAULT_RESOLUTION = "720p"
DEFAULT_DURATION = 5


def _skill_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent


def _skill_venv_python() -> Path:
    venv_dir = _skill_root() / ".venv"
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run_bootstrap() -> None:
    bootstrap = Path(__file__).resolve().parent / "bootstrap.py"
    result = subprocess.run([sys.executable, str(bootstrap)])
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
except ModuleNotFoundError:
    _run_bootstrap()
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def _mysql_connect():
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


def load_drawing_settings(conn, from_wx_id: str) -> tuple[bool, dict]:
    gs = _query_one(conn, "SELECT image_ai_enabled, image_ai_settings FROM global_settings LIMIT 1")
    enabled = False
    settings_json: dict = {}

    if gs:
        if gs.get("image_ai_enabled") is not None:
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


def _resolve_jimeng_config(settings_json: dict) -> dict:
    jimeng_config = settings_json.get("JiMeng")
    if isinstance(jimeng_config, dict) and jimeng_config:
        return jimeng_config
    if isinstance(settings_json, dict):
        return settings_json
    return {}


def _normalize_session_ids(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def _http_post_json(url: str, body: dict, headers: dict, timeout: int = 300) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_videos(from_wx_id: str, video_urls: list[str]) -> None:
    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        raise RuntimeError("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置")

    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/video/url"
    send_body = {
        "to_wxid": from_wx_id,
        "video_urls": [url for url in video_urls if url],
    }
    _http_post_json(send_url, send_body, {"Content-Type": "application/json"}, timeout=60)


def call_jimeng_video(
    config: dict,
    prompt: str,
    model: str,
    file_paths: list[str],
    ratio: str,
    resolution: str,
    duration: int,
) -> list[str]:
    base_url = str(config.get("base_url", "")).rstrip("/")
    session_ids = _normalize_session_ids(config.get("sessionid", []))
    if not base_url or not session_ids:
        raise RuntimeError("即梦视频配置缺少 base_url 或 sessionid")

    body = {
        "model": model or DEFAULT_MODEL,
        "prompt": prompt,
        "ratio": ratio or DEFAULT_RATIO,
        "resolution": resolution or DEFAULT_RESOLUTION,
        "duration": duration or DEFAULT_DURATION,
        "response_format": "url",
    }
    if file_paths:
        body["file_paths"] = file_paths

    resp = _http_post_json(
        f"{base_url}/v1/videos/generations",
        body,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {','.join(session_ids)}",
        },
        timeout=300,
    )

    urls: list[str] = []
    for item in resp.get("data", []):
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                urls.append(url)
    return urls


def _parse_cli_params(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--file_paths", action="append", default=[])
    parser.add_argument("--ratio", default="")
    parser.add_argument("--resolution", default="")
    parser.add_argument("--duration", type=int, default=0)

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    return {
        "prompt": namespace.prompt,
        "model": namespace.model,
        "file_paths": [path for path in namespace.file_paths if path.strip()],
        "ratio": namespace.ratio,
        "resolution": namespace.resolution,
        "duration": namespace.duration,
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
        sys.stdout.write("缺少视频提示词\n")
        return 1

    model = params.get("model", "").strip()
    if not model or model == "none":
        model = DEFAULT_MODEL
    if model not in SUPPORTED_MODELS:
        sys.stdout.write("不支持的 AI 视频模型\n")
        return 1

    file_paths = params.get("file_paths", [])
    if len(file_paths) > 2:
        sys.stdout.write("file_paths 最多只能传 2 个\n")
        return 1

    ratio = params.get("ratio", "").strip() or DEFAULT_RATIO
    resolution = params.get("resolution", "").strip() or DEFAULT_RESOLUTION
    duration = params.get("duration", 0) or DEFAULT_DURATION
    if duration <= 0:
        sys.stdout.write("duration 必须大于 0\n")
        return 1

    from_wx_id = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not from_wx_id:
        sys.stdout.write("环境变量 ROBOT_FROM_WX_ID 未配置\n")
        return 1

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
        sys.stdout.write("AI 生成视频未开启\n")
        return 0

    jimeng_config = _resolve_jimeng_config(settings_json)
    if not isinstance(jimeng_config, dict) or not jimeng_config:
        sys.stdout.write("未找到即梦视频配置\n")
        return 1
    if jimeng_config.get("enabled") is False:
        sys.stdout.write("即梦视频未开启\n")
        return 0

    try:
        video_urls = call_jimeng_video(
            jimeng_config,
            prompt,
            model,
            file_paths,
            ratio,
            resolution,
            duration,
        )
    except Exception as exc:
        sys.stdout.write(f"调用即梦生成视频接口失败: {exc}\n")
        return 1

    if not video_urls:
        sys.stdout.write("未生成任何视频\n")
        return 1

    try:
        send_videos(from_wx_id, video_urls)
        sys.stdout.write("ended")
    except Exception as exc:
        sys.stdout.write(f"发送视频失败: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)