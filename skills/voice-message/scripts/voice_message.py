#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.stderr = sys.stdout


VALID_EMOTIONS = {
    "happy",
    "sad",
    "angry",
    "surprised",
    "fear",
    "hate",
    "excited",
    "lovey-dovey",
    "shy",
    "comfort",
    "tension",
    "tender",
    "magnetic",
    "vocal-fry",
    "ASMR",
}

EMOTION_ALIASES = {
    "vocal - fry": "vocal-fry",
}

DEFAULT_SPEAKER = "zh_female_vv_uranus_bigtts"
DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_SAMPLE_RATE = 24000
MAX_CONTENT_LENGTH = 260
STREAM_END_CODE = 20000000


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
        read_timeout=300,
        write_timeout=300,
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


def _load_json_field(raw: object) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}


def load_tts_settings(conn, from_wx_id: str) -> tuple[bool, str, dict, str, str]:
    global_row = _query_one(
        conn,
        "SELECT tts_enabled, tts_model, tts_settings, chat_base_url, chat_api_key FROM global_settings LIMIT 1",
    )
    enabled = False
    tts_model: str = "doubao"
    settings_json: dict = {}
    fallback_base_url: str = ""
    fallback_api_key: str = ""

    if global_row:
        if global_row.get("tts_enabled") is not None:
            enabled = bool(global_row["tts_enabled"])
        if global_row.get("tts_model"):
            tts_model = str(global_row["tts_model"]).strip() or "doubao"
        settings_json = _load_json_field(global_row.get("tts_settings"))
        fallback_base_url = str(global_row.get("chat_base_url") or "").strip()
        fallback_api_key = str(global_row.get("chat_api_key") or "").strip()

    if from_wx_id.endswith("@chatroom"):
        override = _query_one(
            conn,
            "SELECT tts_enabled, tts_model, tts_settings, chat_base_url, chat_api_key FROM chat_room_settings WHERE chat_room_id = %s LIMIT 1",
            (from_wx_id,),
        )
    else:
        override = _query_one(
            conn,
            "SELECT tts_enabled, tts_model, tts_settings, chat_base_url, chat_api_key FROM friend_settings WHERE wechat_id = %s LIMIT 1",
            (from_wx_id,),
        )

    if override:
        if override.get("tts_enabled") is not None:
            enabled = bool(override["tts_enabled"])
        if override.get("tts_model"):
            tts_model = str(override["tts_model"]).strip() or tts_model
        override_settings = _load_json_field(override.get("tts_settings"))
        if override_settings:
            settings_json = override_settings
        if str(override.get("chat_base_url") or "").strip():
            fallback_base_url = str(override["chat_base_url"]).strip()
        if str(override.get("chat_api_key") or "").strip():
            fallback_api_key = str(override["chat_api_key"]).strip()

    return enabled, tts_model, settings_json, fallback_base_url, fallback_api_key


def _normalize_emotion(emotion: str) -> str:
    normalized = EMOTION_ALIASES.get(emotion.strip(), emotion.strip())
    if normalized not in VALID_EMOTIONS:
        raise ValueError("emotion 不在支持范围内")
    return normalized


def _parse_cli_params(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--content", default="")
    parser.add_argument("--emotion", default="")
    parser.add_argument("--context_texts", action="append", default=[])

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    return {
        "content": namespace.content,
        "emotion": namespace.emotion,
        "context_texts": [item for item in namespace.context_texts if item.strip()],
    }


def _build_request_headers(config: dict) -> dict[str, str]:
    request_header = config.get("request_header") or {}
    if not isinstance(request_header, dict):
        raise RuntimeError("request_header 配置格式错误")

    app_id = str(request_header.get("X-Api-App-Id") or "").strip()
    access_key = str(request_header.get("X-Api-Access-Key") or "").strip()
    resource_id = str(request_header.get("X-Api-Resource-Id") or "").strip()
    if not app_id or not access_key or not resource_id:
        raise RuntimeError("请求头参数不能为空")

    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Id": app_id,
        "X-Api-Access-Key": access_key,
        "X-Api-Resource-Id": resource_id,
    }
    request_id = str(request_header.get("X-Api-Request-Id") or "").strip()
    if request_id:
        headers["X-Api-Request-Id"] = request_id
    usage_header = str(request_header.get("X-Control-Require-Usage-Tokens-Return") or "").strip()
    if usage_header:
        headers["X-Control-Require-Usage-Tokens-Return"] = usage_header
    return headers


def _build_request_body(config: dict, content: str, emotion: str, context_texts: list[str]) -> dict:
    request_body = config.get("request_body") or {}
    if not isinstance(request_body, dict):
        raise RuntimeError("request_body 配置格式错误")

    body = json.loads(json.dumps(request_body))
    user = body.setdefault("user", {})
    if not isinstance(user, dict):
        raise RuntimeError("user 配置格式错误")
    user["uid"] = str(uuid.uuid4())

    req_params = body.setdefault("req_params", {})
    if not isinstance(req_params, dict):
        raise RuntimeError("req_params 配置格式错误")

    if not str(req_params.get("speaker") or "").strip():
        req_params["speaker"] = DEFAULT_SPEAKER
    req_params["text"] = content

    audio_params = req_params.setdefault("audio_params", {})
    if not isinstance(audio_params, dict):
        raise RuntimeError("audio_params 配置格式错误")
    audio_params["format"] = DEFAULT_AUDIO_FORMAT
    audio_params["sample_rate"] = DEFAULT_SAMPLE_RATE
    if emotion:
        audio_params["emotion"] = emotion
        audio_params["emotion_scale"] = 5

    additions = req_params.setdefault("x-additions", {})
    if not isinstance(additions, dict):
        raise RuntimeError("x-additions 配置格式错误")
    if context_texts:
        additions["context_texts"] = context_texts

    return body


def synthesize_audio(config: dict, content: str, emotion: str, context_texts: list[str]) -> tuple[bytes, str]:
    url = str(config.get("url") or "").strip()
    if not url:
        raise RuntimeError("语音合成地址不能为空")

    request_headers = _build_request_headers(config)
    request_body = _build_request_body(config, content, emotion, context_texts)
    request_data = json.dumps(request_body).encode("utf-8")

    req = urllib.request.Request(url, data=request_data, headers=request_headers, method="POST")
    try:
        response = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API请求失败，状态码 {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"发送请求失败: {exc}") from exc

    audio_chunks = bytearray()
    audio_format = str(
        ((request_body.get("req_params") or {}).get("audio_params") or {}).get("format") or DEFAULT_AUDIO_FORMAT
    ).strip() or DEFAULT_AUDIO_FORMAT

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"解析响应失败: {exc}, 行内容: {line}") from exc

            code = int(payload.get("code") or 0)
            message = str(payload.get("message") or "")
            audio_b64 = payload.get("data")

            if code == 0 and isinstance(audio_b64, str) and audio_b64:
                try:
                    audio_chunks.extend(base64.b64decode(audio_b64))
                except Exception as exc:
                    raise RuntimeError(f"解码音频数据失败: {exc}") from exc
                continue

            if code == 0 and isinstance(payload.get("sentence"), dict):
                continue

            if code == STREAM_END_CODE:
                break

            if code > 0:
                raise RuntimeError(f"合成失败，错误码: {code}, 错误信息: {message}")

    if not audio_chunks:
        raise RuntimeError("未接收到音频数据")

    return bytes(audio_chunks), audio_format


def _pcm16le_to_wav(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    import struct

    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        16,
        b"data",
        data_size,
    )
    return header + pcm_data


def synthesize_audio_mimo(config: dict, content: str, voice: str) -> tuple[bytes, str]:
    api_key = str(config.get("api_key") or "").strip()
    base_url = str(config.get("base_url") or "https://api.xiaomimimo.com/v1").strip().rstrip("/")
    model = str(config.get("model") or "mimo-v2.5-tts").strip()
    if not voice:
        voice = str(config.get("voice") or "mimo_default").strip()
    if not api_key:
        raise RuntimeError("mimo api_key 不能为空")

    url = f"{base_url}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "assistant", "content": content}],
        "audio": {"format": "pcm16", "voice": voice},
        "stream": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        method="POST",
    )

    pcm_chunks = bytearray()
    try:
        response = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"mimo API请求失败，状态码 {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"mimo 发送请求失败: {exc}") from exc

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            audio = delta.get("audio") or {}
            audio_data_b64 = audio.get("data") if isinstance(audio, dict) else None
            if audio_data_b64:
                try:
                    pcm_chunks.extend(base64.b64decode(audio_data_b64))
                except Exception as exc:
                    raise RuntimeError(f"解码 mimo 音频数据失败: {exc}") from exc

    if not pcm_chunks:
        raise RuntimeError("mimo 未接收到音频数据")

    wav_data = _pcm16le_to_wav(bytes(pcm_chunks))
    return wav_data, "wav"


def _guess_mime_type(audio_format: str) -> str:
    fmt = audio_format.lower()
    if fmt == "mp3":
        return "audio/mpeg"
    if fmt == "wav":
        return "audio/wav"
    if fmt == "amr":
        return "audio/amr"
    return "application/octet-stream"


def _encode_multipart_formdata(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----wechatrobot{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for field_name, filename, data, content_type in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def send_voice(from_wx_id: str, audio_data: bytes, audio_format: str) -> None:
    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        raise RuntimeError("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置")

    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/voice"
    suffix = f".{audio_format.lower() or DEFAULT_AUDIO_FORMAT}"

    with tempfile.NamedTemporaryFile(prefix="voice-message-", suffix=suffix, delete=False) as temp_file:
        temp_file.write(audio_data)
        temp_path = Path(temp_file.name)

    try:
        file_bytes = temp_path.read_bytes()
        body, boundary = _encode_multipart_formdata(
            {"to_wxid": from_wx_id},
            [("voice", temp_path.name, file_bytes, _guess_mime_type(audio_format))],
        )
        req = urllib.request.Request(
            send_url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"发送语音失败，状态码 {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"发送语音失败: {exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少输入参数\n")
        return 1

    try:
        params = _parse_cli_params(sys.argv[1:])
    except ValueError as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    content = params.get("content", "").strip()
    if not content:
        sys.stdout.write("文本转语音的输入文本不能为空\n")
        return 1
    if len(content) > MAX_CONTENT_LENGTH:
        sys.stdout.write("你要说的也太多了，要不你还是说点别的吧。\n")
        return 1

    emotion = params.get("emotion", "").strip()
    if emotion:
        try:
            emotion = _normalize_emotion(emotion)
        except ValueError as exc:
            sys.stdout.write(f"参数格式错误: {exc}\n")
            return 1

    context_texts = params.get("context_texts", [])

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
        enabled, tts_model, tts_settings, fallback_base_url, fallback_api_key = load_tts_settings(conn, from_wx_id)
    except Exception as exc:
        sys.stdout.write(f"加载文本转语音配置失败: {exc}\n")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not enabled:
        sys.stdout.write("文本转语音未开启\n")
        return 0

    if not isinstance(tts_settings, dict) or not tts_settings:
        sys.stdout.write("未找到文本转语音配置\n")
        return 1

    model_config = tts_settings.get(tts_model)
    if not isinstance(model_config, dict) or not model_config:
        sys.stdout.write(f"未找到 {tts_model} 的文本转语音配置\n")
        return 1

    try:
        if tts_model == "doubao":
            audio_data, audio_format = synthesize_audio(model_config, content, emotion, context_texts)
        elif tts_model == "mimo":
            if not str(model_config.get("api_key") or "").strip() and fallback_api_key:
                model_config = dict(model_config)
                model_config["api_key"] = fallback_api_key
            if not str(model_config.get("base_url") or "").strip() and fallback_base_url:
                model_config = dict(model_config)
                model_config["base_url"] = fallback_base_url
            audio_data, audio_format = synthesize_audio_mimo(model_config, content, "")
        else:
            sys.stdout.write(f"未知的 TTS 模型: {tts_model}\n")
            return 1
    except Exception as exc:
        sys.stdout.write(f"语音合成失败: {exc}\n")
        return 1

    try:
        send_voice(from_wx_id, audio_data, audio_format)
        sys.stdout.write("ended")
    except Exception as exc:
        sys.stdout.write(f"发送语音失败: {exc}\n")
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