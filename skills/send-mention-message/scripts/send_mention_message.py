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


def _mysql_connect():
    _ensure_skill_venv_python()
    try:
        import pymysql  # type: ignore
    except ModuleNotFoundError:
        _run_bootstrap()
        venv_python = _skill_venv_python()
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])

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


def _http_post_json(url: str, body: dict, timeout: int = 300) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
        if not text.strip():
            return {}
        return json.loads(text)


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


def _parse_cli_params(argv: list[str]) -> tuple[list[str], str, bool, bool]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mention", action="append", default=[])
    parser.add_argument("--mentions", action="append", default=[])
    parser.add_argument("--all", "--mention-all", dest="mention_all", action="store_true")
    parser.add_argument("--content", default="")
    parser.add_argument("--ended", action="store_true", default=False)

    namespace, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"存在不支持的参数: {' '.join(unknown)}")

    mentions = _expand_json_array_values(namespace.mention + namespace.mentions, "mentions")
    deduped: list[str] = []
    seen = set()
    for mention in mentions:
        key = mention.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(mention)

    if namespace.mention_all and deduped:
        raise ValueError("all 不能和 mention 或 mentions 同时使用")

    return deduped, namespace.content, namespace.ended, namespace.mention_all


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _find_member(conn, chat_room_id: str, mention: str) -> dict | None:
    keyword = mention.strip()
    like_keyword = f"%{_escape_like(keyword)}%"
    sql = """
        SELECT wechat_id, remark, nickname
        FROM chat_room_members
        WHERE chat_room_id = %s
          AND (is_leaved IS NULL OR is_leaved = 0)
          AND (
            (remark IS NOT NULL AND remark LIKE %s ESCAPE '\\\\')
            OR (nickname IS NOT NULL AND nickname LIKE %s ESCAPE '\\\\')
          )
        ORDER BY id ASC
        LIMIT 50
    """

    with conn.cursor() as cursor:
        cursor.execute(sql, (chat_room_id, like_keyword, like_keyword))
        candidates = list(cursor.fetchall())

    keyword_folded = keyword.casefold()

    for field in ("remark", "nickname"):
        for candidate in candidates:
            if _normalize(candidate.get(field)).casefold() == keyword_folded:
                return candidate

    for field in ("remark", "nickname"):
        for candidate in candidates:
            value = _normalize(candidate.get(field)).casefold()
            if keyword_folded in value:
                return candidate

    return None


def _resolve_mentions(conn, chat_room_id: str, mentions: list[str]) -> tuple[list[str], list[str]]:
    at_wechat_ids: list[str] = []
    seen = set()
    missing: list[str] = []

    for mention in mentions:
        member = _find_member(conn, chat_room_id, mention)
        if not member:
            missing.append(mention)
            continue

        wechat_id = _normalize(member.get("wechat_id"))
        if wechat_id and wechat_id not in seen:
            seen.add(wechat_id)
            at_wechat_ids.append(wechat_id)

    return at_wechat_ids, missing


def _send_text_message(client_port: str, to_wxid: str, content: str, at_wechat_ids: list[str]) -> None:
    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/text"
    body = {
        "to_wxid": to_wxid,
        "content": content,
        "at": at_wechat_ids,
    }
    _http_post_json(send_url, body)


def main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write("缺少要艾特的成员昵称、备注或 --all\n")
        return 1

    try:
        mentions, content, ended, mention_all = _parse_cli_params(sys.argv[1:])
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    if not mention_all and not mentions:
        sys.stdout.write("缺少要艾特的成员昵称、备注或 --all\n")
        return 1

    chat_room_id = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not chat_room_id:
        sys.stdout.write("环境变量 ROBOT_FROM_WX_ID 未配置\n")
        return 1
    if not chat_room_id.endswith("@chatroom"):
        sys.stdout.write("当前会话不是群聊，不能发送艾特消息\n")
        return 1

    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        sys.stdout.write("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置\n")
        return 1

    if mention_all:
        at_wechat_ids = ["notify@all"]
    else:
        try:
            conn = _mysql_connect()
        except Exception as exc:
            sys.stdout.write(f"数据库连接失败: {exc}\n")
            return 1

        try:
            at_wechat_ids, missing = _resolve_mentions(conn, chat_room_id, mentions)
        except Exception as exc:
            sys.stdout.write(f"查询群成员失败: {exc}\n")
            return 1
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if missing:
            sys.stdout.write(f"未找到当前群内未退群成员: {', '.join(missing)}\n")
            return 1
        if not at_wechat_ids:
            sys.stdout.write("未找到可艾特的群成员\n")
            return 1

    try:
        _send_text_message(client_port, chat_room_id, content, at_wechat_ids)
        sys.stdout.write("艾特所有人消息发送成功\n" if mention_all else "艾特消息发送成功\n")
        if ended:
            sys.stdout.write("ended")
        return 0
    except Exception as exc:
        sys.stdout.write(f"艾特消息发送失败: {exc}\n")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        if exit_code == 0:
            # ended may have already been printed above in the success path.
            # If main() returned non-zero, ended is not printed.
            pass
        raise SystemExit(exit_code)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)
