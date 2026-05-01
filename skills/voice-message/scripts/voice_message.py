#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
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
DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_MODEL = "mimo-v2.5-tts"
DEFAULT_MIMO_VOICE = "mimo_default"
DEFAULT_MIMO_AUDIO_FORMAT = "wav"
MIMO_STREAM_AUDIO_FORMAT = "pcm16"
MIMO_PCM_SAMPLE_RATE = 24000
MIMO_VOICE_DESIGN_MODEL = "mimo-v2.5-tts-voicedesign"
MIMO_VOICE_CLONE_MODEL = "mimo-v2.5-tts-voiceclone"
WECHAT_VOICE_MESSAGE_TYPE = 34
MAX_CONTENT_LENGTH = 260
STREAM_END_CODE = 20000000


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


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _clean_text_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [item for item in (_clean_text(value) for value in values) if item]


def _coerce_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _normalize_emotion(emotion: str) -> str:
    normalized = EMOTION_ALIASES.get(emotion.strip(), emotion.strip())
    return normalized if normalized in VALID_EMOTIONS else ""


def _download_referenced_voice_clone(message_id: str) -> str:
    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        raise RuntimeError("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置")

    encoded_message_id = urllib.parse.quote(message_id, safe="")
    download_url = (
        f"http://127.0.0.1:{client_port}/api/v1/robot/chat/voice/download"
        f"?message_id={encoded_message_id}"
    )
    req = urllib.request.Request(download_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            wav_data = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"下载引用语音失败，状态码 {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载引用语音失败: {exc}") from exc

    if not wav_data:
        raise RuntimeError("下载引用语音失败: 响应为空")

    audio_b64 = base64.b64encode(wav_data).decode("utf-8")
    return f"data:audio/wav;base64,{audio_b64}"


def _load_referenced_voice_clone(conn) -> str:
    ref_message_id = os.environ.get("ROBOT_REF_MESSAGE_ID", "").strip()
    if not ref_message_id:
        return ""

    message = _query_one(conn, "SELECT * FROM messages WHERE msg_id = %s LIMIT 1", (ref_message_id,))
    if not message:
        return ""

    try:
        message_type = int(message.get("type") or 0)
    except (TypeError, ValueError):
        return ""

    if message_type != WECHAT_VOICE_MESSAGE_TYPE:
        return ""

    return _download_referenced_voice_clone(ref_message_id)


def _parse_cli_params(argv: list[str]) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--content", default="")
    parser.add_argument("--emotion", default="")
    parser.add_argument("--context_texts", action="append", default=[])
    parser.add_argument("--voice", default="")
    parser.add_argument("--style_prompt", action="append", default=[])
    parser.add_argument("--voice_prompt", default="")
    parser.add_argument("--audio_tags", action="append", default=[])
    parser.add_argument("--speaking_rate", default="")
    parser.add_argument("--pitch", default="")
    parser.add_argument("--volume", default="")
    parser.add_argument("--dialect", default="")

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    return {
        "content": namespace.content,
        "emotion": _clean_text(namespace.emotion),
        "context_texts": _clean_text_list(namespace.context_texts),
        "voice": _clean_text(namespace.voice),
        "style_prompt": _clean_text_list(namespace.style_prompt),
        "voice_prompt": _clean_text(namespace.voice_prompt),
        "audio_tags": _clean_text_list(namespace.audio_tags),
        "speaking_rate": _clean_text(namespace.speaking_rate),
        "pitch": _clean_text(namespace.pitch),
        "volume": _clean_text(namespace.volume),
        "dialect": _clean_text(namespace.dialect),
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


def _build_control_texts(params: dict) -> list[str]:
    controls = list(params.get("context_texts") or [])
    controls.extend(params.get("style_prompt") or [])

    labeled_fields = [
        ("emotion", "情绪/风格"),
        ("voice_prompt", "音色描述"),
        ("speaking_rate", "语速"),
        ("pitch", "音高"),
        ("volume", "音量"),
        ("dialect", "方言/口音"),
    ]
    for field_name, label in labeled_fields:
        value = _clean_text(params.get(field_name))
        if value:
            controls.append(f"{label}: {value}")

    for tag in params.get("audio_tags") or []:
        controls.append(f"音频标签: {tag}")

    return [item for item in controls if item]


def _build_request_body(config: dict, params: dict) -> dict:
    request_body = config.get("request_body") or {}
    if not isinstance(request_body, dict):
        raise RuntimeError("request_body 配置格式错误")

    content = params.get("content", "")

    body = json.loads(json.dumps(request_body))
    user = body.setdefault("user", {})
    if not isinstance(user, dict):
        raise RuntimeError("user 配置格式错误")
    user["uid"] = str(uuid.uuid4())

    req_params = body.setdefault("req_params", {})
    if not isinstance(req_params, dict):
        raise RuntimeError("req_params 配置格式错误")

    voice = _clean_text(params.get("voice"))
    if voice:
        req_params["speaker"] = voice
    elif not str(req_params.get("speaker") or "").strip():
        req_params["speaker"] = DEFAULT_SPEAKER
    req_params["text"] = content

    audio_params = req_params.setdefault("audio_params", {})
    if not isinstance(audio_params, dict):
        raise RuntimeError("audio_params 配置格式错误")
    audio_params["format"] = DEFAULT_AUDIO_FORMAT
    audio_params["sample_rate"] = DEFAULT_SAMPLE_RATE
    emotion = _normalize_emotion(_clean_text(params.get("emotion")))
    if emotion:
        audio_params["emotion"] = emotion
        audio_params["emotion_scale"] = 5

    additions = req_params.setdefault("x-additions", {})
    if not isinstance(additions, dict):
        raise RuntimeError("x-additions 配置格式错误")
    context_texts = _build_control_texts(params)
    if context_texts:
        additions["context_texts"] = context_texts

    return body


def synthesize_audio(config: dict, params: dict) -> tuple[bytes, str]:
    url = str(config.get("url") or "").strip()
    if not url:
        raise RuntimeError("语音合成地址不能为空")

    request_headers = _build_request_headers(config)
    request_body = _build_request_body(config, params)
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


def _config_texts(config: dict, key: str) -> list[str]:
    value = config.get(key)
    if isinstance(value, list):
        return _clean_text_list(value)
    text = _clean_text(value)
    return [text] if text else []


def _resolve_mimo_model(config: dict, params: dict) -> str:
    configured_model = _clean_text(config.get("model"))
    if _clean_text(params.get("voice_clone_audio")):
        return MIMO_VOICE_CLONE_MODEL

    auto_model = _coerce_bool(config.get("auto_model"), True)
    if auto_model and _clean_text(config.get("voice_clone_audio")):
        return MIMO_VOICE_CLONE_MODEL
    if auto_model and (_clean_text(params.get("voice_prompt")) or _clean_text(config.get("voice_prompt"))):
        return MIMO_VOICE_DESIGN_MODEL
    if configured_model:
        return configured_model
    return DEFAULT_MIMO_MODEL


def _format_mimo_audio_tags(tags: list[str]) -> str:
    cleaned_tags = [tag.strip("()[]（） ") for tag in tags if tag.strip("()[]（） ")]
    if not cleaned_tags:
        return ""
    return f"({' '.join(cleaned_tags)})"


def _build_mimo_assistant_content(params: dict) -> str:
    content = _clean_text(params.get("content"))
    tags = _format_mimo_audio_tags(params.get("audio_tags") or [])
    return f"{tags}{content}" if tags else content


def _build_mimo_user_content(config: dict, params: dict, model: str) -> str:
    parts: list[str] = []
    voice_prompt = _clean_text(params.get("voice_prompt")) or _clean_text(config.get("voice_prompt"))
    if voice_prompt:
        if model == MIMO_VOICE_DESIGN_MODEL:
            parts.append(voice_prompt)
        else:
            parts.append(f"音色/声线: {voice_prompt}")

    parts.extend(_config_texts(config, "style_prompt"))
    parts.extend(params.get("style_prompt") or [])
    parts.extend(_config_texts(config, "context_texts"))
    parts.extend(params.get("context_texts") or [])

    labeled_fields = [
        ("emotion", "情绪/风格"),
        ("speaking_rate", "语速"),
        ("pitch", "音高"),
        ("volume", "音量"),
        ("dialect", "方言/口音"),
    ]
    for field_name, label in labeled_fields:
        value = _clean_text(params.get(field_name)) or _clean_text(config.get(field_name))
        if value:
            parts.append(f"{label}: {value}")

    if model == MIMO_VOICE_DESIGN_MODEL and not parts:
        raise RuntimeError("mimo 文本音色设计模型需要 voice_prompt 或 style_prompt")

    return "\n".join(parts)


def _resolve_mimo_voice(config: dict, params: dict, model: str) -> str:
    if model == MIMO_VOICE_DESIGN_MODEL:
        return ""

    if model == MIMO_VOICE_CLONE_MODEL:
        voice_clone_audio = _clean_text(params.get("voice_clone_audio")) or _clean_text(config.get("voice_clone_audio"))
        if not voice_clone_audio:
            raise RuntimeError("mimo 音色复刻模型需要引用一条语音消息或配置 voice_clone_audio")
        if voice_clone_audio.startswith("data:"):
            return voice_clone_audio
        mime_type = (
            _clean_text(params.get("voice_clone_mime_type"))
            or _clean_text(config.get("voice_clone_mime_type"))
            or "audio/mpeg"
        )
        return f"data:{mime_type};base64,{voice_clone_audio}"

    return _clean_text(params.get("voice")) or _clean_text(config.get("voice")) or DEFAULT_MIMO_VOICE


def _build_mimo_payload(config: dict, params: dict) -> tuple[dict, str, bool]:
    model = _resolve_mimo_model(config, params)
    stream = _coerce_bool(config.get("stream"), False)
    audio_format = MIMO_STREAM_AUDIO_FORMAT if stream else (
        _clean_text(config.get("audio_format")) or _clean_text(config.get("format")) or DEFAULT_MIMO_AUDIO_FORMAT
    )

    messages = []
    user_content = _build_mimo_user_content(config, params, model)
    if user_content or model == MIMO_VOICE_CLONE_MODEL:
        messages.append({"role": "user", "content": user_content})
    messages.append({"role": "assistant", "content": _build_mimo_assistant_content(params)})

    audio = {"format": audio_format}
    voice = _resolve_mimo_voice(config, params, model)
    if voice:
        audio["voice"] = voice

    payload = {
        "model": model,
        "messages": messages,
        "audio": audio,
    }
    if stream:
        payload["stream"] = True

    return payload, audio_format, stream


def _decompress_response_bytes(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return raw
    if encoding == "gzip":
        return gzip.decompress(raw)
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    if encoding == "br":
        try:
            import brotli  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "mimo 响应使用了 brotli 压缩，但当前环境未安装 brotli，请安装后重试"
            ) from exc
        return brotli.decompress(raw)
    raise RuntimeError(f"mimo 响应使用了不支持的 Content-Encoding: {encoding}")


def _read_response_text(response) -> str:
    raw = response.read()
    encoding = response.headers.get("Content-Encoding", "")
    raw = _decompress_response_bytes(raw, encoding)
    return raw.decode("utf-8", errors="replace")


def _decode_mimo_audio(audio_b64: object, audio_format: str) -> tuple[bytes, str]:
    if not isinstance(audio_b64, str) or not audio_b64:
        raise RuntimeError("mimo 响应未包含音频数据")
    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as exc:
        raise RuntimeError(f"解码 mimo 音频数据失败: {exc}") from exc
    if audio_format == MIMO_STREAM_AUDIO_FORMAT:
        return _pcm16le_to_wav(audio_bytes, sample_rate=MIMO_PCM_SAMPLE_RATE), "wav"
    return audio_bytes, audio_format


def _read_mimo_non_stream_response(response, audio_format: str) -> tuple[bytes, str]:
    raw_body = _read_response_text(response)
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        snippet = raw_body[:300]
        if "<html" in raw_body.lower() or "<!doctype" in raw_body.lower():
            raise RuntimeError(
                "mimo 响应不是 JSON，疑似 base_url 配置错误（被网关前端 SPA 拦截），"
                "请检查 base_url 是否配置为带 /v1 的完整地址，例如 https://api.xiaomimimo.com/v1。"
                f"响应片段: {snippet}"
            ) from exc
        raise RuntimeError(f"解析 mimo 响应失败: {exc}, 响应内容: {snippet}") from exc

    if isinstance(payload.get("error"), dict):
        error = payload["error"]
        message = _clean_text(error.get("message")) or json.dumps(error, ensure_ascii=False)
        raise RuntimeError(f"mimo 合成失败: {message}")

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"mimo 响应缺少 choices: {raw_body}")
    message = choices[0].get("message") or {}
    audio = message.get("audio") or {}
    audio_b64 = audio.get("data") if isinstance(audio, dict) else None
    return _decode_mimo_audio(audio_b64, audio_format)


def _read_mimo_stream_response(response) -> tuple[bytes, str]:
    pcm_chunks = bytearray()
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
            if isinstance(chunk.get("error"), dict):
                message = _clean_text(chunk["error"].get("message")) or json.dumps(chunk["error"], ensure_ascii=False)
                raise RuntimeError(f"mimo 合成失败: {message}")
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

    return _pcm16le_to_wav(bytes(pcm_chunks), sample_rate=MIMO_PCM_SAMPLE_RATE), "wav"


def synthesize_audio_mimo(config: dict, params: dict) -> tuple[bytes, str]:
    api_key = str(config.get("api_key") or "").strip()
    base_url = str(config.get("base_url") or DEFAULT_MIMO_BASE_URL).strip().rstrip("/")
    if not api_key:
        raise RuntimeError("mimo api_key 不能为空")

    # 兼容用户把 base_url 配成不带 /v1 的根地址（如 New API / OneAPI 等网关），
    # 避免请求被前端 SPA 兜底返回 index.html。
    parsed_base = urllib.parse.urlsplit(base_url)
    base_path = parsed_base.path or ""
    if not base_path or base_path == "/":
        base_url = f"{base_url}/v1"

    url = f"{base_url}/chat/completions"
    payload, audio_format, stream = _build_mimo_payload(config, params)
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=request_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        try:
            error_body = _read_response_text(exc)
        except Exception:
            error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"mimo API请求失败，状态码 {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"mimo 发送请求失败: {exc}") from exc

    if stream:
        return _read_mimo_stream_response(response)

    with response:
        return _read_mimo_non_stream_response(response, audio_format)


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
        try:
            enabled, tts_model, tts_settings, fallback_base_url, fallback_api_key = load_tts_settings(conn, from_wx_id)
        except Exception as exc:
            sys.stdout.write(f"加载文本转语音配置失败: {exc}\n")
            return 1

        try:
            if tts_model == "mimo":
                voice_clone_audio = _load_referenced_voice_clone(conn)
                if voice_clone_audio:
                    params = dict(params)
                    params["voice_clone_audio"] = voice_clone_audio
        except Exception as exc:
            sys.stdout.write(f"加载引用语音失败: {exc}\n")
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
            audio_data, audio_format = synthesize_audio(model_config, params)
        elif tts_model == "mimo":
            if not str(model_config.get("api_key") or "").strip() and fallback_api_key:
                model_config = dict(model_config)
                model_config["api_key"] = fallback_api_key
            if not str(model_config.get("base_url") or "").strip() and fallback_base_url:
                model_config = dict(model_config)
                model_config["base_url"] = fallback_base_url
            audio_data, audio_format = synthesize_audio_mimo(model_config, params)
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