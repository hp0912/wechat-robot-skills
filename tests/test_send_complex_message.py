from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/send-complex-message/scripts/send_complex_message.py"
)
NOW = 1_789_272_000  # 2026-09-13 12:00:00 +08:00


class HistoryCursor:
    """在内存数据库执行实际查询，仅转换 MySQL 的占位符和转义字符串语法。"""

    def __init__(self, database) -> None:
        self.cursor = database.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cursor.close()

    def execute(self, sql, params) -> None:
        sql = sql.replace("%s", "?").replace("ESCAPE '\\\\'", "ESCAPE '\\'")
        self.cursor.execute(sql, params)

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class HistoryConnection:
    def __init__(self, messages: list[dict]) -> None:
        self.database = sqlite3.connect(":memory:")
        self.database.row_factory = sqlite3.Row
        self.closed = False
        self.database.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, from_wxid TEXT, sender_wxid TEXT, to_wxid TEXT,
                is_chat_room INTEGER, type INTEGER, app_msg_type INTEGER,
                content TEXT, display_full_content TEXT, is_recalled INTEGER, created_at INTEGER
            )
        """)
        for message in messages:
            row = {
                "from_wxid": "room@chatroom", "sender_wxid": "wxid_alice",
                "to_wxid": "wxid_robot", "is_chat_room": 1, "type": 1,
                "app_msg_type": 0, "content": "安排会议", "display_full_content": "",
                "is_recalled": 0, "created_at": NOW - 60,
                **message,
            }
            self.database.execute(
                f"INSERT INTO messages ({', '.join(row)}) VALUES ({', '.join(['?'] * len(row))})",
                tuple(row.values()),
            )

    def cursor(self):
        return HistoryCursor(self.database)

    def close(self) -> None:
        self.closed = True
        self.database.close()


class SendComplexMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("send_complex_message", SCRIPT_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载测试脚本：{SCRIPT_PATH}")
        cls.module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "stderr", sys.stderr):
            spec.loader.exec_module(cls.module)

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {
            "ROBOT_FROM_WX_ID": "room@chatroom",
            "ROBOT_WECHAT_CLIENT_PORT": "9000",
            # 已有上下文消息也不能让不引用的请求自动带上引用 ID。
            "ROBOT_MESSAGE_ID": "123",
            "ROBOT_REF_MESSAGE_ID": "456",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_sending_modes_and_optional_reference(self) -> None:
        cases = [
            ("mention only", ["--mention", "张三"], "", ["wxid_zhangsan"], None),
            ("text only", ["--content", "收到"], "收到", [], None),
            ("quote only", ["--refer-message-id", "12", "--content", "收到"], "收到", [], 12),
            ("quote and mention", ["--refer-message-id", "12", "--mention", "张三", "--content", "收到"], "收到", ["wxid_zhangsan"], 12),
            ("mention all only", ["--all"], "", ["notify@all"], None),
            ("text and mention", ["--mention", "张三", "--content", "收到"], "收到", ["wxid_zhangsan"], None),
            ("large primary ID", ["--refer-message-id", "9007199254740993", "--content", "收到"], "收到", [], 9007199254740993),
        ]
        for name, args, content, at, reference in cases:
            with self.subTest(name=name), contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT_PATH), *args, "--ended"]))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                connect = stack.enter_context(mock.patch.object(self.module, "_mysql_connect"))
                resolve = stack.enter_context(mock.patch.object(self.module, "_resolve_mentions", return_value=(["wxid_zhangsan"], [])))
                post = stack.enter_context(mock.patch.object(self.module, "_http_post_json", return_value={"code": 200, "data": None}))

                self.assertEqual(self.module.main(), 0)
                body = {"to_wxid": "room@chatroom", "content": content, "at": at}
                if reference is not None:
                    body["refer_message_id"] = reference
                post.assert_called_once_with(
                    "http://127.0.0.1:9000/api/v1/robot/message/send/refermessage",
                    body,
                )
                if "--mention" in args:
                    resolve.assert_called_once_with(connect.return_value, "room@chatroom", ["张三"])
                    connect.return_value.close.assert_called_once()
                else:
                    connect.assert_not_called()
                    resolve.assert_not_called()

    def test_invalid_combinations_do_not_send(self) -> None:
        cases = [
            [],
            ["--content", " \t\n"],
            ["--mentions", "[]"],
            ["--refer-message-id", "12"],
            ["--refer-message-id", "12", "--mention", "张三"],
            ["--refer-message-id", "12", "--content", " \t\n", "--all"],
            ["--refer-message-id", "0", "--content", "收到"],
            ["--refer-message-id", "-1", "--content", "收到"],
            ["--refer-message-id", "9223372036854775808", "--content", "收到"],
        ]
        for args in cases:
            with self.subTest(args=args), contextlib.ExitStack() as stack:
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT_PATH), *args, "--ended"]))
                connect = stack.enter_context(mock.patch.object(self.module, "_mysql_connect"))
                post = stack.enter_context(mock.patch.object(self.module, "_http_post_json"))

                self.assertEqual(self.module.main(), 1)
                connect.assert_not_called()
                post.assert_not_called()
                self.assertFalse(output.getvalue().endswith("ended"))

    def run_history(self, rows, args=(), conversation="room@chatroom") -> dict:
        connection = HistoryConnection(rows)
        self.addCleanup(connection.close)
        with contextlib.ExitStack() as stack:
            # 查询无须客户端端口；也不应该调用任何发送接口。
            stack.enter_context(mock.patch.dict(os.environ, {"ROBOT_FROM_WX_ID": conversation}, clear=True))
            stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--query-history", *args]))
            stack.enter_context(mock.patch.object(self.module.time, "time", return_value=NOW))
            stack.enter_context(mock.patch.object(self.module, "_mysql_connect", return_value=connection))
            send = stack.enter_context(mock.patch.object(self.module, "_http_post_json"))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

            self.assertEqual(self.module.main(), 0, output.getvalue())
            self.assertTrue(connection.closed)
            send.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(result["conversation_id"], conversation)
            return result

    def test_history_group_isolation_and_24_hour_boundaries(self) -> None:
        result = self.run_history([
            {"id": 1, "created_at": NOW - 86400},
            {"id": 2, "created_at": NOW},
            {"id": 3, "created_at": NOW - 86401},
            {"id": 4, "created_at": NOW + 1},
            {"id": 5, "from_wxid": "other@chatroom"},
            {"id": 6, "from_wxid": "wxid_alice", "is_chat_room": 0},
            {"id": 7, "is_chat_room": 0},
        ])
        self.assertEqual([row["id"] for row in result["messages"]], [2, 1])
        self.assertTrue(result["is_chat_room"])
        self.assertEqual((result["start_time"], result["end_time"]), (NOW - 86400, NOW))

    def test_history_private_chat_includes_both_directions_only_for_current_friend(self) -> None:
        result = self.run_history([
            {"id": 1, "from_wxid": "wxid_alice", "is_chat_room": 0},
            {"id": 2, "from_wxid": "wxid_alice", "is_chat_room": 0, "sender_wxid": "wxid_robot"},
            {"id": 3, "from_wxid": "wxid_bob", "is_chat_room": 0},
            {"id": 4},  # 同一个人的群消息不能混入私聊。
            {"id": 5, "from_wxid": "wxid_alice", "is_chat_room": 1},
            {"id": 6, "from_wxid": "wxid_alice", "is_chat_room": 0, "created_at": NOW - 86401},
        ], conversation="wxid_alice")
        self.assertEqual([row["id"] for row in result["messages"]], [2, 1])
        self.assertFalse(result["is_chat_room"])

    def test_history_accepts_shorter_relative_and_absolute_ranges(self) -> None:
        rows = [
            {"id": 1, "created_at": NOW - 1801},
            {"id": 2, "created_at": NOW - 1800},
            {"id": 3, "created_at": NOW - 900},
            {"id": 4, "created_at": NOW - 899},
            {"id": 5, "created_at": NOW},
        ]
        cases = [
            (["--hours", "0.5"], [5, 4, 3, 2], NOW - 1800, NOW),
            (["--hours", "24"], [5, 4, 3, 2, 1], NOW - 86400, NOW),
            (["--start-time", str(NOW - 1800), "--end-time", str(NOW - 900)], [3, 2], NOW - 1800, NOW - 900),
            (["--start-time", "2026-09-13 11:30", "--end-time", "2026-09-13 11:45:00"], [3, 2], NOW - 1800, NOW - 900),
            (["--start-time", str(NOW - 900)], [5, 4, 3], NOW - 900, NOW),
            (["--end-time", str(NOW - 900)], [3, 2, 1], NOW - 86400, NOW - 900),
        ]
        for args, ids, start, end in cases:
            with self.subTest(args=args):
                result = self.run_history(rows, args)
                self.assertEqual([row["id"] for row in result["messages"]], ids)
                self.assertEqual((result["start_time"], result["end_time"]), (start, end))

    def test_history_combines_keywords_types_and_sender(self) -> None:
        result = self.run_history([
            {"id": 1, "content": "安排", "display_full_content": "会议通知", "type": 49, "app_msg_type": 57},
            {"id": 2, "content": "安排会议", "type": 49, "app_msg_type": 6},
            {"id": 3, "content": "安排会议", "type": 49, "app_msg_type": 5},
            {"id": 4, "content": "安排会议", "type": 1, "app_msg_type": 57},
            {"id": 5, "content": "安排会议", "type": 49, "app_msg_type": 57, "sender_wxid": "wxid_bob"},
            {"id": 6, "content": "安排", "type": 49, "app_msg_type": 57},
            {"id": 7, "content": "安排会议", "type": 49, "app_msg_type": 57, "from_wxid": "other@chatroom"},
            {"id": 8, "content": "安排会议", "type": 49, "app_msg_type": 57, "created_at": NOW - 3601},
        ], ["--hours", "1", "--keyword", "安排", "--keyword", "会议", "--message-type", "app",
            "--app-msg-type", "57", "--app-msg-type", "6", "--sender-wxid", "wxid_alice"])
        self.assertEqual([row["id"] for row in result["messages"]], [2, 1])

    def test_history_accepts_multiple_message_types_and_app_subtype_alone(self) -> None:
        rows = [
            {"id": 1, "type": 3}, {"id": 2, "type": 43}, {"id": 3, "type": 34},
            {"id": 4, "type": 49, "app_msg_type": 57}, {"id": 5, "type": 1, "app_msg_type": 57},
        ]
        result = self.run_history(rows, ["--message-type", "image", "--message-type", "43"])
        self.assertEqual([row["id"] for row in result["messages"]], [2, 1])
        result = self.run_history(rows, ["--app-msg-type", "57"])
        self.assertEqual([row["id"] for row in result["messages"]], [4])

    def test_history_keywords_are_literal_and_cannot_bypass_scope(self) -> None:
        for keyword in ["100%_\\", "' OR 1=1 --"]:
            with self.subTest(keyword=keyword):
                result = self.run_history([
                    {"id": 1, "content": f"前缀{keyword}后缀"},
                    {"id": 2, "content": "100AB\\"},
                    {"id": 3, "content": keyword, "from_wxid": "other@chatroom"},
                ], ["--keyword", keyword])
                self.assertEqual([row["id"] for row in result["messages"]], [1])

    def test_history_pagination_preserves_order_and_primary_ids(self) -> None:
        rows = [{"id": 1}, {"id": 2}, {"id": 9007199254740993}]
        first = self.run_history(rows, ["--limit", "2"])
        self.assertEqual([row["id"] for row in first["messages"]], [9007199254740993, 2])
        self.assertEqual(first["count"], 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        last = self.run_history(rows, ["--limit", "2", "--offset", str(first["next_offset"])])
        self.assertEqual([row["id"] for row in last["messages"]], [1])
        self.assertFalse(last["has_more"])
        self.assertIsNone(last["next_offset"])
        empty = self.run_history(rows, ["--offset", "3"])
        self.assertEqual(empty["messages"], [])
        self.assertEqual(empty["count"], 0)
        self.assertFalse(empty["has_more"])

    def test_invalid_history_requests_neither_query_nor_send(self) -> None:
        cases = [
            ["--hours", "0"], ["--hours", "-1"], ["--hours", "24.01"],
            ["--hours", "nan"], ["--hours", "inf"], ["--hours", "0.00001"], ["--hours", "bad"],
            ["--start-time", str(NOW - 86401)],
            ["--start-time", str(NOW - 172800), "--end-time", str(NOW - 86400)],
            ["--start-time", str(NOW - 172800), "--end-time", str(NOW - 169200)],
            ["--end-time", str(NOW + 1)], ["--end-time", str(NOW - 86401)],
            ["--start-time", str(NOW)], ["--start-time", str(NOW + 1)],
            ["--start-time", "invalid"], ["--start-time", ""], ["--end-time", "2026-09-31 09:00"],
            ["--start-time", str(NOW - 60), "--end-time", str(NOW - 120)],
            ["--hours", "1", "--start-time", str(NOW - 60)],
            ["--hours", "1", "--end-time", str(NOW - 60)],
            ["--limit", "0"], ["--limit", "201"], ["--offset", "-1"],
            ["--keyword", "  "], ["--sender-wxid", " "],
            ["--message-type", "bad"], ["--message-type", "0"], ["--app-msg-type", "-1"],
            ["--message-type", "text", "--app-msg-type", "57"],
            ["--content", "你好"], ["--content", ""], ["--mention", "张三"], ["--mentions", "[]"],
            ["--all"], ["--refer-message-id", "1"], ["--ended"],
            ["--conversation-id", "other@chatroom"], ["--from-wxid", "other@chatroom"],
        ]
        for args in cases:
            with self.subTest(args=args), contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--query-history", *args]))
                stack.enter_context(mock.patch.object(self.module.time, "time", return_value=NOW))
                connect = stack.enter_context(mock.patch.object(self.module, "_mysql_connect"))
                send = stack.enter_context(mock.patch.object(self.module, "_http_post_json"))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(self.module.main(), 1)
                connect.assert_not_called()
                send.assert_not_called()
                self.assertFalse(output.getvalue().endswith("ended"))

    def test_history_filters_require_explicit_query_mode(self) -> None:
        for flag, value in [("--hours", "1"), ("--limit", "50"), ("--keyword", "会议")]:
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    self.module._parse_cli_params(["--content", "你好", flag, value])

    def test_history_requires_current_conversation(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--query-history"]), \
                mock.patch.object(self.module, "_mysql_connect") as connect, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.module.main(), 1)
            self.assertIn("ROBOT_FROM_WX_ID", output.getvalue())
            connect.assert_not_called()

    def test_history_recomputes_window_after_connecting(self) -> None:
        connection = HistoryConnection([{"id": 1, "created_at": NOW - 86400}])
        with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--query-history"]), \
                mock.patch.object(self.module.time, "time", side_effect=[NOW, NOW + 10]), \
                mock.patch.object(self.module, "_mysql_connect", return_value=connection), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.module.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["messages"], [])
            self.assertTrue(connection.closed)

    def test_history_query_failure_closes_connection(self) -> None:
        connection = mock.MagicMock()
        connection.cursor.side_effect = RuntimeError("查询失败")
        with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--query-history"]), \
                mock.patch.object(self.module, "_mysql_connect", return_value=connection), \
                mock.patch.object(self.module, "_http_post_json") as send, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.module.main(), 1)
            self.assertIn("查询失败", output.getvalue())
            self.assertFalse(output.getvalue().endswith("ended"))
            connection.close.assert_called_once()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
