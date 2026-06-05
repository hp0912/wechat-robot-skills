#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse, unquote

sys.stderr = sys.stdout


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


def _clean_text(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return value.strip()
    return ""


def _extract_model(record: dict | None) -> str:
    if record:
        model = _clean_text(record.get("image_recognition_model"))
        if model:
            return model
    return ""


def _normalize_ai_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized and not re.search(r"/v\d+$", normalized):
        normalized += "/v1"
    return normalized


def load_image_recognition_config(conn, from_wx_id: str) -> dict:
    global_fields = "chat_base_url, chat_api_key, image_recognition_model"
    global_record = _query_one(conn, f"SELECT {global_fields} FROM global_settings LIMIT 1")

    config = {"base_url": "", "api_key": "", "model": ""}
    if global_record:
        base_url = _clean_text(global_record.get("chat_base_url"))
        api_key = _clean_text(global_record.get("chat_api_key"))
        if base_url:
            config["base_url"] = base_url
        if api_key:
            config["api_key"] = api_key
        model = _extract_model(global_record)
        if model:
            config["model"] = model

    if from_wx_id.endswith("@chatroom"):
        override_fields = "chat_base_url, chat_api_key, image_recognition_model"
        override = _query_one(
            conn,
            f"SELECT {override_fields} FROM chat_room_settings WHERE chat_room_id = %s LIMIT 1",
            (from_wx_id,),
        )
    else:
        override_fields = "chat_base_url, chat_api_key, image_recognition_model"
        override = _query_one(
            conn,
            f"SELECT {override_fields} FROM friend_settings WHERE wechat_id = %s LIMIT 1",
            (from_wx_id,),
        )

    if override:
        base_url = _clean_text(override.get("chat_base_url"))
        api_key = _clean_text(override.get("chat_api_key"))
        if base_url:
            config["base_url"] = base_url
        if api_key:
            config["api_key"] = api_key
        model = _extract_model(override)
        if model:
            config["model"] = model

    config["base_url"] = _normalize_ai_base_url(config["base_url"])
    return config


def _local_image_path(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("不支持非本机 file URL")
        return Path(unquote(parsed.path)).expanduser()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _local_image_to_data_url(value: str) -> str:
    path = _local_image_path(value)
    if not path.is_file():
        raise ValueError(f"本地图片不存在: {path}")

    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"无法识别本地图片类型: {path}")

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _resolve_image_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    if parsed.scheme == "data" and value.startswith("data:image/"):
        return value
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"不支持的图片地址协议: {parsed.scheme}")
    return _local_image_to_data_url(value)


def _extract_response_text(response) -> str:
    if not response.choices:
        return ""

    content = response.choices[0].message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
            elif isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                texts.append(item["text"].strip())
        return "\n".join(texts)
    return ""


def recognize_image(prompt: str, image_url: str, config: dict) -> str:
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "")
    model = config.get("model", "")
    if not api_key or not base_url or not model:
        raise RuntimeError("AI图片识别未配置，请联系管理员进行配置")

    resolved_image_url = _resolve_image_url(image_url)
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": resolved_image_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        stream=False,
    )
    content = _extract_response_text(response)
    if not content:
        raise RuntimeError("图片识别失败，返回了空内容")
    return content


def _parse_cli_params(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--image_url", default="")

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    return {"prompt": namespace.prompt, "image_url": namespace.image_url}


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
    image_url = params.get("image_url", "").strip()
    if not prompt:
        sys.stdout.write("缺少图像识别提示词\n")
        return 1
    if not image_url:
        sys.stdout.write("缺少图片 URL\n")
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
        config = load_image_recognition_config(conn, from_wx_id)
    except Exception as exc:
        sys.stdout.write(f"加载图像识别配置失败: {exc}\n")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    try:
        content = recognize_image(prompt, image_url, config)
    except Exception as exc:
        sys.stdout.write(f"图片识别失败: {exc}\n")
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
