#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.stderr = sys.stdout

DEFAULT_PROMPT = "请用中文输出，分成三部分：1. 详细描述视频内容；2. 总结核心信息；3. 给出对视频的理解。"
DEFAULT_FPS = 2
DEFAULT_MAX_TOKENS = 800


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


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


def _table_has_column(conn, table_name: str, column_name: str) -> bool:
    sql = (
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s LIMIT 1"
    )
    database_name = conn.db
    if isinstance(database_name, (bytes, bytearray)):
        database_name = database_name.decode("utf-8")
    cur = conn.cursor()
    cur.execute(sql, (database_name, table_name, column_name))
    row = cur.fetchone()
    cur.close()
    return row is not None


def _decode_settings(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return {}


def _extract_model(record: dict | None, settings_json: dict) -> str:
    if record:
        model = record.get("image_recognition_model")
        if isinstance(model, (bytes, bytearray)):
            model = model.decode("utf-8")
        if isinstance(model, str) and model.strip():
            return model.strip()

    for key in ("image_recognition_model", "imageRecognitionModel"):
        value = settings_json.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def load_understanding_settings(conn, from_wx_id: str) -> tuple[bool, str]:
    global_has_model = _table_has_column(conn, "global_settings", "image_recognition_model")
    chatroom_has_model = _table_has_column(conn, "chat_room_settings", "image_recognition_model")
    friend_has_model = _table_has_column(conn, "friend_settings", "image_recognition_model")

    global_fields = "image_ai_enabled, image_ai_settings"
    if global_has_model:
        global_fields += ", image_recognition_model"
    global_record = _query_one(conn, f"SELECT {global_fields} FROM global_settings LIMIT 1")

    enabled = False
    settings_json: dict = {}
    model = ""
    if global_record:
        if global_record.get("image_ai_enabled") is not None:
            enabled = bool(global_record["image_ai_enabled"])
        settings_json = _decode_settings(global_record.get("image_ai_settings"))
        model = _extract_model(global_record, settings_json)

    if from_wx_id.endswith("@chatroom"):
        override_fields = "image_ai_enabled, image_ai_settings"
        if chatroom_has_model:
            override_fields += ", image_recognition_model"
        override = _query_one(
            conn,
            f"SELECT {override_fields} FROM chat_room_settings WHERE chat_room_id = %s LIMIT 1",
            (from_wx_id,),
        )
    else:
        override_fields = "image_ai_enabled, image_ai_settings"
        if friend_has_model:
            override_fields += ", image_recognition_model"
        override = _query_one(
            conn,
            f"SELECT {override_fields} FROM friend_settings WHERE wechat_id = %s LIMIT 1",
            (from_wx_id,),
        )

    if override:
        if override.get("image_ai_enabled") is not None:
            enabled = bool(override["image_ai_enabled"])
        override_settings = _decode_settings(override.get("image_ai_settings"))
        if override_settings:
            settings_json = override_settings
        override_model = _extract_model(override, settings_json)
        if override_model:
            model = override_model

    return enabled, model


def _http_post_json(url: str, body: dict, headers: dict, timeout: int = 300) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc


def _extract_response_text(payload: dict) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return ""

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                texts.append(item["text"].strip())
        return "\n".join(text for text in texts if text)
    return ""


def analyze_video(video_url: str, prompt: str, model: str, fps: int, max_tokens: int) -> str:
    api_key = os.environ.get("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("环境变量 ARK_API_KEY 未配置")
    if not model:
        raise RuntimeError("数据库中未配置 image_recognition_model")

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}, "fps": str(fps)},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
    }
    response = _http_post_json(
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        body,
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        timeout=300,
    )
    text = _extract_response_text(response)
    if not text:
        raise RuntimeError("视频理解接口未返回文本内容")
    return text


def _validate_video_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("video_url 必须是 https 链接")
    return value


def _parse_cli_params(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--video_url", default="")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--max_tokens", type=int, default=DEFAULT_MAX_TOKENS)

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")
    if namespace.fps <= 0:
        raise ValueError("fps 必须大于 0")
    if namespace.max_tokens <= 0:
        raise ValueError("max_tokens 必须大于 0")

    return {
        "video_url": namespace.video_url,
        "prompt": namespace.prompt,
        "fps": namespace.fps,
        "max_tokens": namespace.max_tokens,
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

    video_url = params.get("video_url", "").strip()
    if not video_url:
        sys.stdout.write("缺少视频链接\n")
        return 1
    try:
        _validate_video_url(video_url)
    except ValueError as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    prompt = params.get("prompt", "").strip() or DEFAULT_PROMPT
    fps = int(params.get("fps", DEFAULT_FPS))
    max_tokens = int(params.get("max_tokens", DEFAULT_MAX_TOKENS))

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
        enabled, model = load_understanding_settings(conn, from_wx_id)
    except Exception as exc:
        sys.stdout.write(f"加载视频理解配置失败: {exc}\n")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not enabled:
        sys.stdout.write("AI 图像识别未开启\n")
        return 0

    try:
        content = analyze_video(video_url, prompt, model, fps, max_tokens)
    except Exception as exc:
        sys.stdout.write(f"调用视频理解接口失败: {exc}\n")
        return 1

    sys.stdout.write(f"{content}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)