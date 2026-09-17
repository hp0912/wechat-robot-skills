#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

sys.stderr = sys.stdout

MAX_HISTORY_SECONDS = 24 * 60 * 60
DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 200
SHANGHAI_TZ = timezone(timedelta(hours=8))
MESSAGE_TYPES = {
    "text": 1,
    "image": 3,
    "voice": 34,
    "card": 42,
    "video": 43,
    "emoji": 47,
    "location": 48,
    "app": 49,
    "system": 10000,
    "recall": 10002,
}


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


def _client_private_token() -> str:
    return os.environ.get("ROBOT_CLIENT_PRIVATE_TOKEN", "").strip()


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
        import pymysql
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
        headers={
            "Content-Type": "application/json",
            "X-Private-Token": _client_private_token(),
        },
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


def _positive_int(value: str) -> int:
    number = int(value)
    if not 0 < number <= 2**63 - 1:
        raise ValueError("必须是 int64 范围内的正整数")
    return number


def _message_type(value: str) -> int:
    normalized = value.strip().lower()
    if normalized in MESSAGE_TYPES:
        return MESSAGE_TYPES[normalized]
    return _positive_int(normalized)


def _parse_cli_params(argv: list[str]) -> argparse.Namespace:
    parser = SkillArgumentParser(description="发送消息或查询当前会话最近 24 小时内的聊天记录", allow_abbrev=False)
    parser.add_argument("--mention", action="append", default=[])
    parser.add_argument("--mentions", action="append", default=[])
    parser.add_argument("--mention-wxid", dest="mention_wxids", action="append", default=[],
                        help="按已确认的微信 ID 艾特当前群成员，可重复，不按昵称回退匹配")
    parser.add_argument("--all", "--mention-all", dest="mention_all", action="store_true")
    parser.add_argument("--refer-message-id", type=int)
    parser.add_argument("--content")
    parser.add_argument("--ended", action="store_true", default=False)
    parser.add_argument("--query-history", action="store_true", help="只查询历史聊天记录，不发送消息")
    parser.add_argument("--hours", type=float, help="查询最近多少小时，支持小数，最多 24 小时")
    parser.add_argument("--start-time", help="开始时间：Unix 秒或北京时间 YYYY-MM-DD HH:mm[:ss]")
    parser.add_argument("--end-time", help="结束时间：Unix 秒或北京时间 YYYY-MM-DD HH:mm[:ss]")
    parser.add_argument("--keyword", action="append", help="正文或显示内容包含的关键词，可重复，多个词须全部匹配")
    parser.add_argument("--message-type", action="append", type=_message_type, help="消息类型名称或编号，可重复")
    parser.add_argument("--app-msg-type", action="append", type=_positive_int, help="APP 消息子类型编号，可重复")
    parser.add_argument("--sender-wxid", help="按发送人微信 ID 精确过滤")
    parser.add_argument("--limit", type=int, help="单页条数，默认 50，最多 200")
    parser.add_argument("--offset", type=int, help="分页偏移量，默认 0")

    namespace = parser.parse_args(argv)

    if namespace.query_history:
        if (namespace.mention or namespace.mentions or namespace.mention_wxids or namespace.mention_all
                or namespace.refer_message_id is not None or namespace.content is not None
                or namespace.ended):
            raise ValueError("--query-history 不能与发送参数或 --ended 同时使用")
        _validate_history_params(namespace)
        return namespace

    history_fields = ("hours", "start_time", "end_time", "keyword", "message_type",
                      "app_msg_type", "sender_wxid", "limit", "offset")
    if any(getattr(namespace, field) is not None for field in history_fields):
        raise ValueError("聊天记录过滤参数必须与 --query-history 一起使用")
    namespace.content = namespace.content or ""

    mentions = _expand_json_array_values(namespace.mention + namespace.mentions, "mentions")
    deduped: list[str] = []
    seen = set()
    for mention in mentions:
        key = mention.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(mention)

    mention_wxids = [value.strip() for value in namespace.mention_wxids]
    if any(not value for value in mention_wxids):
        raise ValueError("mention-wxid 不能为空")
    namespace.mention_wxids = list(dict.fromkeys(mention_wxids))

    if namespace.mention_all and (deduped or namespace.mention_wxids):
        raise ValueError("all 不能和 mention、mentions 或 mention-wxid 同时使用")

    if namespace.refer_message_id is not None:
        if not 0 < namespace.refer_message_id <= 2**63 - 1:
            raise ValueError("refer_message_id 必须是正整数，且不能超过 int64 范围")
        if not namespace.content.strip():
            raise ValueError("发送引用消息必须提供非空 content")

    if not deduped and not namespace.mention_wxids and not namespace.mention_all and not namespace.content.strip():
        raise ValueError("请提供非空 content，或指定要艾特的成员/--all")

    namespace.mentions = deduped
    return namespace


def _parse_history_time(value: str, field_name: str) -> int:
    text = value.strip()
    if text.isascii() and text.isdigit():
        return int(text)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(text, pattern).replace(tzinfo=SHANGHAI_TZ).timestamp())
        except ValueError:
            continue
    raise ValueError(f"{field_name} 必须是 Unix 秒或北京时间 YYYY-MM-DD HH:mm[:ss]")


def _resolve_history_time_range(args: argparse.Namespace) -> tuple[int, int]:
    now = int(time.time())
    earliest = now - MAX_HISTORY_SECONDS
    if args.hours is not None:
        if args.start_time is not None or args.end_time is not None:
            raise ValueError("--hours 不能与 --start-time/--end-time 同时使用")
        if not math.isfinite(args.hours) or not 0 < args.hours <= 24:
            raise ValueError("hours 必须大于 0 且不超过 24")
        seconds = int(args.hours * 3600)
        if seconds < 1:
            raise ValueError("hours 对应的时间范围不能小于 1 秒")
        return now - seconds, now

    start = _parse_history_time(args.start_time, "start_time") if args.start_time is not None else earliest
    end = _parse_history_time(args.end_time, "end_time") if args.end_time is not None else now
    if start < earliest or end > now:
        raise ValueError("只能查询最近 24 小时内的聊天记录，不能查询更早或未来的时间")
    if start >= end:
        raise ValueError("结束时间必须晚于开始时间")
    return start, end


def _validate_history_params(args: argparse.Namespace) -> None:
    _resolve_history_time_range(args)
    args.limit = DEFAULT_HISTORY_LIMIT if args.limit is None else args.limit
    args.offset = 0 if args.offset is None else args.offset
    if not 1 <= args.limit <= MAX_HISTORY_LIMIT:
        raise ValueError(f"limit 必须在 1 到 {MAX_HISTORY_LIMIT} 之间")
    if not 0 <= args.offset <= 2**63 - 1:
        raise ValueError("offset 必须是 int64 范围内的非负整数")
    args.keyword = [keyword.strip() for keyword in (args.keyword or [])]
    if any(not keyword for keyword in args.keyword):
        raise ValueError("keyword 不能为空或纯空白")
    if args.sender_wxid is not None:
        args.sender_wxid = args.sender_wxid.strip()
        if not args.sender_wxid:
            raise ValueError("sender_wxid 不能为空或纯空白")
    if args.app_msg_type and args.message_type and set(args.message_type) != {49}:
        raise ValueError("app_msg_type 只能与 APP 消息类型 49（app）一起使用")


def _query_history(conn, conversation_id: str, args: argparse.Namespace) -> dict:
    # 建立连接/安装依赖可能耗时；在真正查询前重新确定最近 24 小时的边界。
    start, end = _resolve_history_time_range(args)
    is_chat_room = conversation_id.endswith("@chatroom")
    conditions = [
        "from_wxid = %s",
        "is_chat_room = %s",
        "created_at >= %s",
        "created_at <= %s",
    ]
    params: list[object] = [conversation_id, is_chat_room, start, end]
    for keyword in args.keyword:
        conditions.append("(content LIKE %s ESCAPE '\\\\' OR display_full_content LIKE %s ESCAPE '\\\\')")
        pattern = f"%{_escape_like(keyword)}%"
        params.extend((pattern, pattern))
    if args.sender_wxid:
        conditions.append("sender_wxid = %s")
        params.append(args.sender_wxid)
    if args.message_type:
        conditions.append(f"`type` IN ({', '.join(['%s'] * len(args.message_type))})")
        params.extend(args.message_type)
    if args.app_msg_type:
        conditions.append("`type` = 49")
        conditions.append(f"app_msg_type IN ({', '.join(['%s'] * len(args.app_msg_type))})")
        params.extend(args.app_msg_type)
    sql = f"""
        SELECT id, from_wxid, sender_wxid, to_wxid, is_chat_room,
               type, app_msg_type, content, display_full_content, is_recalled, created_at
        FROM messages
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
    """
    params.extend((args.limit + 1, args.offset))
    with conn.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        rows = list(cursor.fetchall())
    has_more = len(rows) > args.limit
    messages = rows[:args.limit]
    return {
        "conversation_id": conversation_id,
        "is_chat_room": is_chat_room,
        "start_time": start,
        "end_time": end,
        "limit": args.limit,
        "offset": args.offset,
        "count": len(messages),
        "has_more": has_more,
        "next_offset": args.offset + len(messages) if has_more else None,
        "messages": messages,
    }


def _run_history_query(conversation_id: str, args: argparse.Namespace) -> int:
    try:
        conn = _mysql_connect()
    except Exception as exc:
        sys.stdout.write(f"数据库连接失败: {exc}\n")
        return 1
    try:
        result = _query_history(conn, conversation_id, args)
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0
    except Exception as exc:
        sys.stdout.write(f"查询聊天记录失败: {exc}\n")
        return 1
    finally:
        conn.close()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _find_member(conn, chat_room_id: str, mention: str) -> dict | None:
    keyword = mention.strip()
    for condition, value in (
        ("= LOWER(%s)", keyword),
        ("LIKE LOWER(%s) ESCAPE '\\\\'", f"%{_escape_like(keyword)}%"),
    ):
        sql = f"""
            SELECT DISTINCT wechat_id
            FROM chat_room_members
            WHERE chat_room_id = %s
              AND (is_leaved IS NULL OR is_leaved = 0)
              AND (
                LOWER(TRIM(remark)) {condition}
                OR LOWER(TRIM(nickname)) {condition}
              )
            ORDER BY wechat_id
            LIMIT 2
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (chat_room_id, value, value))
            candidates = list(cursor.fetchall())
        if len(candidates) > 1:
            raise ValueError(f"成员名称有歧义: {mention}，匹配到多个当前群成员；本次未发送消息，请明确微信 ID")
        if candidates:
            return candidates[0]
    return None


def _find_member_by_wechat_id(conn, chat_room_id: str, wechat_id: str) -> dict | None:
    sql = """
        SELECT wechat_id, remark, nickname
        FROM chat_room_members
        WHERE chat_room_id = %s
          AND (is_leaved IS NULL OR is_leaved = 0)
          AND wechat_id = %s
        LIMIT 1
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (chat_room_id, wechat_id))
        members = list(cursor.fetchall())
    return members[0] if members else None


def _resolve_mentions(
    conn, chat_room_id: str, mentions: list[str], mention_wxids: list[str],
) -> tuple[list[str], list[str]]:
    at_wechat_ids: list[str] = []
    seen = set()
    missing: list[str] = []

    for values, find_member in ((mentions, _find_member), (mention_wxids, _find_member_by_wechat_id)):
        for mention in values:
            member = find_member(conn, chat_room_id, mention)
            if not member:
                missing.append(mention)
                continue

            wechat_id = _normalize(member.get("wechat_id"))
            if wechat_id and wechat_id not in seen:
                seen.add(wechat_id)
                at_wechat_ids.append(wechat_id)

    return at_wechat_ids, missing


def _send_message(
    client_port: str,
    to_wxid: str,
    content: str,
    at_wechat_ids: list[str],
    refer_message_id: int | None,
) -> None:
    # 未传引用 ID 时，客户端会调用普通文本发送方法，并保留原生艾特功能。
    send_url = f"http://127.0.0.1:{client_port}/api/v1/robot/message/send/refermessage"
    body = {
        "to_wxid": to_wxid,
        "content": content,
        "at": at_wechat_ids,
    }
    if refer_message_id is not None:
        body["refer_message_id"] = refer_message_id
    result = _http_post_json(send_url, body)
    if not isinstance(result, dict) or result.get("code") != 200:
        message = result.get("message") if isinstance(result, dict) else None
        raise RuntimeError(message or "客户端未返回成功状态")


def main() -> int:
    try:
        args = _parse_cli_params(sys.argv[1:])
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stdout.write(f"参数格式错误: {exc}\n")
        return 1

    to_wxid = os.environ.get("ROBOT_FROM_WX_ID", "").strip()
    if not to_wxid:
        sys.stdout.write("环境变量 ROBOT_FROM_WX_ID 未配置\n")
        return 1
    if args.query_history:
        return _run_history_query(to_wxid, args)

    mentions, mention_wxids, content = args.mentions, args.mention_wxids, args.content
    ended, mention_all, refer_message_id = args.ended, args.mention_all, args.refer_message_id
    if (mention_all or mentions or mention_wxids) and not to_wxid.endswith("@chatroom"):
        sys.stdout.write("当前会话不是群聊，不能发送艾特消息\n")
        return 1

    client_port = os.environ.get("ROBOT_WECHAT_CLIENT_PORT", "").strip()
    if not client_port:
        sys.stdout.write("环境变量 ROBOT_WECHAT_CLIENT_PORT 未配置\n")
        return 1

    at_wechat_ids: list[str] = []
    if mention_all:
        at_wechat_ids = ["notify@all"]
    elif mentions or mention_wxids:
        try:
            conn = _mysql_connect()
        except Exception as exc:
            sys.stdout.write(f"数据库连接失败: {exc}\n")
            return 1

        try:
            at_wechat_ids, missing = _resolve_mentions(conn, to_wxid, mentions, mention_wxids)
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
            if any(value in mentions for value in missing):
                sys.stdout.write("本次未发送消息；若还没查过记忆且工具可用，先用 search_chat_room_memory 查找成员，"
                                 "确认是谁后，用 --mention-wxid 传入微信 ID；查过仍无法确定时，请用户补充信息\n")
            return 1
        if not at_wechat_ids:
            sys.stdout.write("未找到可艾特的群成员\n")
            return 1

    try:
        _send_message(client_port, to_wxid, content, at_wechat_ids, refer_message_id)
        if refer_message_id is not None:
            sys.stdout.write("引用消息发送成功\n")
            if at_wechat_ids:
                sys.stdout.write("引用中的艾特仅作显示，当前客户端尚未实现原生艾特提醒\n")
        elif at_wechat_ids:
            sys.stdout.write("艾特所有人消息发送成功\n" if mention_all else "艾特消息发送成功\n")
        else:
            sys.stdout.write("文本消息发送成功\n")
        if ended:
            sys.stdout.write("ended")
        return 0
    except Exception as exc:
        sys.stdout.write(f"消息发送失败: {exc}\n")
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
