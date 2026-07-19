#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, NoReturn, TypedDict

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore[assignment,misc]

sys.stderr = sys.stdout

SCHEDULE_TYPES = ("delay_once", "daily", "weekly", "cn_workday")
MAX_DELAY_SECONDS = 24 * 60 * 60
MENTION_ALL_WECHAT_ID = "notify@all"


class ScheduledTaskTarget(TypedDict):
    wechat_id: str
    type: Literal["chat_room", "friend"]
    mention_wechat_ids: list[str]


class ScheduledTaskCreator(TypedDict):
    type: Literal["chat_room", "friend"]
    wechat_id: str
    chat_room_id: str


class AmbiguousCreateError(RuntimeError):
    """The POST may have reached the server, so retrying could create a duplicate."""


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def _shanghai_timezone():
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            pass
    return timezone(timedelta(hours=8), name="Asia/Shanghai")


SHANGHAI_TZ = _shanghai_timezone()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = SkillArgumentParser(description="创建当前微信会话的定时任务")
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--schedule-type",
        "--schedule_type",
        dest="schedule_type",
        choices=SCHEDULE_TYPES,
        required=True,
    )
    parser.add_argument("--content", default="")
    parser.add_argument("--ai-prompt", "--ai_prompt", dest="ai_prompt", default="")
    parser.add_argument("--time", default="")
    parser.add_argument("--weekday", action="append", default=[])
    parser.add_argument("--weekdays", action="append", default=[])
    parser.add_argument("--mention", action="append", default=[])
    parser.add_argument("--mentions", action="append", default=[])
    parser.add_argument("--mention-all", "--all", dest="mention_all", action="store_true")
    parser.add_argument("--no-mention", action="store_true")

    delay = parser.add_mutually_exclusive_group()
    delay.add_argument("--delay-seconds", "--delay_seconds", dest="delay_seconds", type=int)
    delay.add_argument("--delay-minutes", "--delay_minutes", dest="delay_minutes", type=int)
    delay.add_argument("--delay-hours", "--delay_hours", dest="delay_hours", type=int)
    delay.add_argument("--run-at", "--run_at", dest="run_at")

    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"环境变量 {name} 未配置")
    return value


def _conversation_identity() -> tuple[ScheduledTaskTarget, ScheduledTaskCreator, str]:
    from_wechat_id = _require_env("ROBOT_FROM_WX_ID")
    sender_wechat_id = os.environ.get("ROBOT_SENDER_WX_ID", "").strip()

    if from_wechat_id.endswith("@chatroom"):
        if not sender_wechat_id:
            raise ValueError("当前群聊缺少消息发送人微信 ID")
        target: ScheduledTaskTarget = {
            "wechat_id": from_wechat_id,
            "type": "chat_room",
            "mention_wechat_ids": [],
        }
        creator: ScheduledTaskCreator = {
            "type": "chat_room",
            "wechat_id": sender_wechat_id,
            "chat_room_id": from_wechat_id,
        }
        return target, creator, "当前群聊"

    creator_wechat_id = sender_wechat_id or from_wechat_id
    target = {
        "wechat_id": from_wechat_id,
        "type": "friend",
        "mention_wechat_ids": [],
    }
    creator = {"type": "friend", "wechat_id": creator_wechat_id, "chat_room_id": ""}
    return target, creator, "当前私聊"


def _validate_clock(value: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("执行时间必须使用 HH:mm 格式，例如 08:30")
    return value


def _parse_weekdays(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for raw in values:
        text = raw.strip()
        if not text:
            continue
        if text.startswith("["):
            try:
                items = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"weekdays JSON 格式错误: {exc.msg}") from exc
            if not isinstance(items, list):
                raise ValueError("weekdays 必须是 JSON 数组或逗号分隔列表")
        else:
            items = [item.strip() for item in text.split(",") if item.strip()]

        for item in items:
            if isinstance(item, bool):
                raise ValueError("星期必须是 1 到 7 的整数")
            try:
                weekday = int(item)
            except (TypeError, ValueError) as exc:
                raise ValueError("星期必须是 1 到 7 的整数") from exc
            if weekday < 1 or weekday > 7:
                raise ValueError("星期必须在 1 到 7 之间，1 代表周一，7 代表周日")
            parsed.append(weekday)

    result = sorted(set(parsed))
    if not result:
        raise ValueError("每周任务至少需要一个星期")
    return result


def _parse_mentions(mention_values: list[str], mentions_values: list[str]) -> list[str]:
    parsed = [value.strip() for value in mention_values if value.strip()]
    for raw in mentions_values:
        text = raw.strip()
        if not text:
            continue
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"mentions JSON 格式错误: {exc.msg}") from exc
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError("mentions 必须是字符串 JSON 数组")

        parsed.extend(item.strip() for item in items if item.strip())

    result: list[str] = []
    seen: set[str] = set()
    for mention in parsed:
        key = mention.casefold()
        if key not in seen:
            seen.add(key)
            result.append(mention)
    return result


def _parse_run_at(value: str, now: datetime) -> int:
    text = value.strip()
    parsed: datetime | None = None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=SHANGHAI_TZ)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("run-at 必须使用 YYYY-MM-DD HH:mm 或 YYYY-MM-DD HH:mm:ss 格式")

    delay_seconds = math.ceil((parsed - now).total_seconds())
    if delay_seconds < 1:
        raise ValueError("一次性任务的执行时间必须晚于当前时间")
    if delay_seconds > MAX_DELAY_SECONDS:
        raise ValueError("一次性任务最多只能设置到未来 24 小时内")
    return delay_seconds


def _delay_seconds(args: argparse.Namespace, now: datetime) -> int:
    if args.delay_seconds is not None:
        seconds = args.delay_seconds
    elif args.delay_minutes is not None:
        seconds = args.delay_minutes * 60
    elif args.delay_hours is not None:
        seconds = args.delay_hours * 60 * 60
    elif args.run_at is not None:
        seconds = _parse_run_at(args.run_at, now)
    else:
        raise ValueError(
            "一次性任务必须提供 delay-seconds、delay-minutes、delay-hours 或 run-at"
        )

    if seconds < 1 or seconds > MAX_DELAY_SECONDS:
        raise ValueError("一次性任务延时必须在 1 秒到 24 小时之间")
    return seconds


def _build_schedule_config(args: argparse.Namespace, now: datetime) -> dict[str, Any]:
    has_delay = any(
        value is not None
        for value in (args.delay_seconds, args.delay_minutes, args.delay_hours, args.run_at)
    )
    weekday_values = [*args.weekday, *args.weekdays]

    if args.schedule_type == "delay_once":
        if args.time or weekday_values:
            raise ValueError("一次性任务不能同时设置 time 或 weekdays")
        return {"delay_seconds": _delay_seconds(args, now)}

    if has_delay:
        raise ValueError(f"{args.schedule_type} 任务不能设置延时参数")
    clock = _validate_clock(args.time.strip())

    if args.schedule_type == "weekly":
        return {"time": clock, "weekdays": _parse_weekdays(weekday_values)}
    if weekday_values:
        raise ValueError(f"{args.schedule_type} 任务不能设置 weekdays")
    return {"time": clock}


def _member_text(member: dict[str, Any], field: str) -> str:
    value = member.get(field)
    return str(value).strip() if value is not None else ""


def _member_candidates(data: Any, chat_room_id: str) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise RuntimeError("群成员查询接口返回的数据不是数组")

    candidates: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if _member_text(item, "chat_room_id") != chat_room_id:
            continue
        if item.get("is_leaved") not in (None, False, 0):
            continue
        if _member_text(item, "wechat_id"):
            candidates.append(item)
    return candidates


def _describe_member(member: dict[str, Any]) -> str:
    remark = _member_text(member, "remark")
    nickname = _member_text(member, "nickname")
    if remark and nickname and remark != nickname:
        return f"{remark}（昵称：{nickname}）"
    return remark or nickname or _member_text(member, "wechat_id")


def _pick_unique_member(
    mention: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    keyword = mention.casefold()
    match_groups = [
        [item for item in candidates if _member_text(item, "remark").casefold() == keyword],
        [item for item in candidates if _member_text(item, "nickname").casefold() == keyword],
        [item for item in candidates if keyword in _member_text(item, "remark").casefold()],
        [item for item in candidates if keyword in _member_text(item, "nickname").casefold()],
    ]

    for matches in match_groups:
        unique: dict[str, dict[str, Any]] = {}
        for item in matches:
            unique.setdefault(_member_text(item, "wechat_id"), item)
        if len(unique) == 1:
            return next(iter(unique.values()))
        if len(unique) > 1:
            labels = "、".join(_describe_member(item) for item in list(unique.values())[:5])
            raise ValueError(
                f"群成员“{mention}”匹配到多人（{labels}），请使用唯一的完整群备注或昵称"
            )

    raise ValueError(f"未找到当前群内未退群成员：“{mention}”")


def _get_json(url: str, timeout: int = 15) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"查询群成员接口返回 HTTP {exc.code}: {error_body}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        raise RuntimeError(f"查询群成员失败：{exc}") from exc

    if not response_text.strip():
        raise RuntimeError("群成员查询接口返回空响应")
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("群成员查询接口返回了无效 JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("群成员查询接口响应不是 JSON 对象")
    return result


def _resolve_mentions(
    client_port: str,
    chat_room_id: str,
    mentions: list[str],
) -> list[dict[str, str]]:
    resolved: list[dict[str, str]] = []
    seen_wechat_ids: set[str] = set()
    for mention in mentions:
        query = urllib.parse.urlencode({"chat_room_id": chat_room_id, "keyword": mention})
        url = (
            f"http://127.0.0.1:{client_port}"
            f"/api/v1/robot/chat-room/not-left-members?{query}"
        )
        response = _get_json(url)
        if response.get("code") != 200:
            raise RuntimeError(str(response.get("message") or "查询群成员失败"))
        member = _pick_unique_member(
            mention,
            _member_candidates(response.get("data"), chat_room_id),
        )
        wechat_id = _member_text(member, "wechat_id")
        if wechat_id in seen_wechat_ids:
            continue
        seen_wechat_ids.add(wechat_id)
        resolved.append(
            {
                "query": mention,
                "wechat_id": wechat_id,
                "display_name": _member_text(member, "remark")
                or _member_text(member, "nickname")
                or mention,
            }
        )
    return resolved


def _build_payload(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    name = args.name.strip()
    content = args.content.strip()
    ai_prompt = args.ai_prompt.strip()

    if not name:
        raise ValueError("任务名称不能为空")
    if len(name) > 100:
        raise ValueError("任务名称不能超过 100 个字符")
    if len(content) > 500:
        raise ValueError("固定提醒文本不能超过 500 个字符")
    if not content and not ai_prompt:
        raise ValueError("content 和 ai-prompt 至少需要提供一项")

    target, creator, target_label = _conversation_identity()
    mentions = _parse_mentions(args.mention, args.mentions)
    mention_mode_count = int(bool(mentions)) + int(args.mention_all) + int(args.no_mention)
    if mention_mode_count > 1:
        raise ValueError("mention-all、no-mention 和 mention/mentions 不能同时使用")

    mention_summary: dict[str, Any]
    if args.mention_all:
        if target["type"] != "chat_room":
            raise ValueError("当前会话不是群聊，不能在定时任务中艾特所有人")
        target["mention_wechat_ids"] = [MENTION_ALL_WECHAT_ID]
        mention_summary = {"mode": "all", "display_names": ["所有人"]}
    elif mentions:
        if target["type"] != "chat_room":
            raise ValueError("当前会话不是群聊，不能在定时任务中艾特群成员")
        client_port = _require_env("ROBOT_WECHAT_CLIENT_PORT")
        resolved_mentions = _resolve_mentions(client_port, target["wechat_id"], mentions)
        target["mention_wechat_ids"] = [item["wechat_id"] for item in resolved_mentions]
        mention_summary = {
            "mode": "custom",
            "display_names": [item["display_name"] for item in resolved_mentions],
        }
    elif args.no_mention:
        target["mention_wechat_ids"] = []
        mention_summary = {"mode": "none", "display_names": []}
    elif target["type"] == "chat_room":
        target["mention_wechat_ids"] = [creator["wechat_id"]]
        mention_summary = {"mode": "creator", "display_names": []}
    else:
        target["mention_wechat_ids"] = []
        mention_summary = {"mode": "none", "display_names": []}

    schedule_config = _build_schedule_config(args, datetime.now(SHANGHAI_TZ))
    payload: dict[str, Any] = {
        "name": name,
        "enabled": True,
        "schedule_type": args.schedule_type,
        "schedule_config": schedule_config,
        "targets": [target],
        "fixed_text": content,
        "images": [],
        "ai_prompt": ai_prompt,
        "creator": creator,
    }
    return payload, target_label, mention_summary


def _post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"接口返回 HTTP {exc.code}: {error_body}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        raise AmbiguousCreateError(
            f"请求定时任务接口失败，结果可能未知：{exc}。请先在定时任务列表核对，勿直接重试"
        ) from exc

    if not response_text.strip():
        raise AmbiguousCreateError(
            "定时任务接口返回空响应，任务可能已创建。请先在定时任务列表核对，勿直接重试"
        )
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise AmbiguousCreateError(
            "定时任务接口返回了无效 JSON，任务可能已创建。请先在定时任务列表核对，勿直接重试"
        ) from exc
    if not isinstance(result, dict):
        raise AmbiguousCreateError(
            "定时任务接口响应不是 JSON 对象，任务可能已创建。请先在定时任务列表核对，勿直接重试"
        )
    return result


def _unwrap_api_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("code") != 200:
        message = str(response.get("message") or "接口返回未知业务错误")
        if "任务已保存，但刷新调度器失败" in message:
            raise AmbiguousCreateError(f"{message}。任务可能已经入库，请先核对任务列表，勿直接重试")
        raise RuntimeError(message)

    data = response.get("data")
    if not isinstance(data, dict):
        raise AmbiguousCreateError(
            "接口成功响应中缺少任务数据，任务可能已创建。请先在定时任务列表核对，勿直接重试"
        )
    return data


def _format_next_run(timestamp: Any) -> str | None:
    if timestamp is None:
        return None
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(value, SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")


def _success_output(
    data: dict[str, Any],
    target_label: str,
    mention_summary: dict[str, Any],
) -> dict[str, Any]:
    targets = data.get("targets")
    first_target = targets[0] if isinstance(targets, list) and targets else {}
    if not isinstance(first_target, dict):
        first_target = {}
    return {
        "ok": True,
        "message": "定时任务创建成功",
        "task": {
            "id": data.get("id"),
            "name": data.get("name"),
            "schedule_type": data.get("schedule_type"),
            "schedule_summary": data.get("schedule_summary"),
            "next_run_at": data.get("next_run_at"),
            "next_run_time": _format_next_run(data.get("next_run_at")),
            "fixed_text": data.get("fixed_text"),
            "uses_ai_prompt": bool(data.get("ai_prompt")),
            "target_label": target_label,
            "target_type": first_target.get("type"),
            "mention": mention_summary,
        },
    }


def main(argv: list[str]) -> int:
    try:
        args = _parse_args(argv)
        payload, target_label, mention_summary = _build_payload(args)

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "target_label": target_label,
                        "mention": mention_summary,
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        client_port = _require_env("ROBOT_WECHAT_CLIENT_PORT")
        url = f"http://127.0.0.1:{client_port}/api/v1/robot/scheduled-tasks"
        response = _post_json(url, payload)
        data = _unwrap_api_response(response)
        print(
            json.dumps(
                _success_output(data, target_label, mention_summary),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"创建定时任务失败：{exc}")
        return 1
    except Exception as exc:  # Keep script failures visible to the agent without hiding them.
        print(f"创建定时任务失败：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
