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


class MemberConnection(HistoryConnection):
    def __init__(self, members: list[dict]) -> None:
        super().__init__([])
        self.database.execute("""
            CREATE TABLE chat_room_members (
                id INTEGER PRIMARY KEY, chat_room_id TEXT, wechat_id TEXT,
                remark TEXT, nickname TEXT, is_leaved INTEGER
            )
        """)
        for member in members:
            row = {
                "chat_room_id": "room@chatroom", "remark": "", "nickname": "",
                "is_leaved": 0, **member,
            }
            self.database.execute(
                f"INSERT INTO chat_room_members ({', '.join(row)}) VALUES ({', '.join(['?'] * len(row))})",
                tuple(row.values()),
            )


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
                    resolve.assert_called_once_with(connect.return_value, "room@chatroom", ["张三"], [])
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
            ["--mention-wxid", " \t", "--content", "收到"],
            ["--mention-wxid", "wxid_alice", "--all"],
            ["--mention-wxid", "wxid_alice", "--refer-message-id", "12"],
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

    def run_member_send(self, members, args):
        connection = MemberConnection(members)
        self.addCleanup(connection.close)
        with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), *args, "--ended"]), \
                mock.patch.object(self.module, "_mysql_connect", return_value=connection), \
                mock.patch.object(self.module, "_http_post_json", return_value={"code": 200}) as post, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.module.main()
        self.assertTrue(connection.closed)
        return result, output.getvalue(), post

    def test_memory_resolved_id_targets_renamed_member_despite_duplicate_names(self) -> None:
        result, output, post = self.run_member_send([
            {"id": 1, "wechat_id": "wxid_other", "nickname": "现在的名字"},
            {"id": 2, "wechat_id": "wxid_renamed", "nickname": "现在的名字", "is_leaved": None},
        ], ["--mention-wxid", " wxid_renamed "])
        self.assertEqual(result, 0)
        post.assert_called_once_with(
            "http://127.0.0.1:9000/api/v1/robot/message/send/refermessage",
            {"to_wxid": "room@chatroom", "content": "", "at": ["wxid_renamed"]},
        )
        self.assertTrue(output.endswith("ended"))

    def test_explicit_ids_require_exact_current_room_membership(self) -> None:
        cases = [
            {"wechat_id": "wxid_target", "is_leaved": 1},
            {"wechat_id": "wxid_target", "chat_room_id": "other@chatroom"},
            {"wechat_id": "wxid_target_suffix"},
        ]
        for member in cases:
            with self.subTest(member=member):
                result, output, post = self.run_member_send([
                    {"id": 1, **member},
                    # 即使别人的当前昵称或备注恰好等于目标 ID，也不能回退匹配。
                    {"id": 2, "wechat_id": "wxid_other", "nickname": "wxid_target", "remark": "wxid_target"},
                ], ["--mention-wxid", "wxid_target", "--content", "请看公告"])
                self.assertEqual(result, 1)
                self.assertIn("未找到当前群内未退群成员: wxid_target", output)
                self.assertFalse(output.endswith("ended"))
                post.assert_not_called()

    def test_missing_old_name_stops_entire_send_then_accepts_resolved_id(self) -> None:
        members = [
            {"id": 1, "wechat_id": "wxid_zhangsan", "nickname": "张三"},
            {"id": 2, "wechat_id": "wxid_renamed", "nickname": "新名字"},
        ]
        common_args = ["--mention", "张三", "--content", "请看公告", "--refer-message-id", "12"]
        result, output, post = self.run_member_send(members, [*common_args, "--mention", "旧名字"])
        self.assertEqual(result, 1)
        self.assertIn("旧名字", output)
        self.assertIn("本次未发送消息", output)
        self.assertIn("search_chat_room_memory", output)
        self.assertIn("--mention-wxid", output)
        self.assertFalse(output.endswith("ended"))
        post.assert_not_called()

        result, _, post = self.run_member_send(members, [*common_args, "--mention-wxid", "wxid_renamed"])
        self.assertEqual(result, 0)
        post.assert_called_once_with(
            "http://127.0.0.1:9000/api/v1/robot/message/send/refermessage",
            {"to_wxid": "room@chatroom", "content": "请看公告",
             "at": ["wxid_zhangsan", "wxid_renamed"], "refer_message_id": 12},
        )

    def test_mixed_names_and_repeated_ids_mention_each_person_once(self) -> None:
        result, _, post = self.run_member_send([
            {"id": 1, "wechat_id": "wxid_zhangsan", "nickname": "张三"},
            {"id": 2, "wechat_id": "wxid_lisi", "nickname": "李四"},
        ], ["--mention", "张三", "--mention-wxid", "wxid_zhangsan",
            "--mention-wxid", "wxid_lisi", "--mention-wxid", "wxid_lisi"])
        self.assertEqual(result, 0)
        self.assertEqual(post.call_args.args[1]["at"], ["wxid_zhangsan", "wxid_lisi"])

    def test_current_name_fallback_rejects_exact_and_partial_ambiguity(self) -> None:
        cases = [
            [{"remark": "张三"}, {"nickname": "张三"}],
            [{"nickname": "张三"}, {"nickname": "张三"}],
            [{"remark": "张三甲"}, {"nickname": "张三乙"}],
        ]
        for names in cases:
            with self.subTest(names=names):
                members = [
                    {"id": index, "wechat_id": f"wxid_{index}", **name}
                    for index, name in enumerate(names, start=1)
                ]
                result, output, post = self.run_member_send(members, ["--mention", "张三"])
                self.assertEqual(result, 1)
                self.assertIn("成员名称有歧义", output)
                self.assertFalse(output.endswith("ended"))
                post.assert_not_called()

    def test_current_name_fallback_finds_exact_match_beyond_many_partial_matches(self) -> None:
        members = [
            {"id": index, "wechat_id": f"wxid_{index}", "nickname": f"张三{index}"}
            for index in range(1, 61)
        ]
        members.extend([
            {"id": 61, "wechat_id": "wxid_target", "nickname": " 张三 "},
            {"id": 62, "wechat_id": "wxid_left", "nickname": "张三", "is_leaved": 1},
            {"id": 63, "wechat_id": "wxid_other_room", "nickname": "张三", "chat_room_id": "other@chatroom"},
        ])
        result, _, post = self.run_member_send(members, ["--mention", "张三"])
        self.assertEqual(result, 0)
        self.assertEqual(post.call_args.args[1]["at"], ["wxid_target"])

    def test_current_name_partial_matching_treats_wildcards_as_literal(self) -> None:
        result, _, post = self.run_member_send([
            {"id": 1, "wechat_id": "wxid_target", "nickname": "前缀100%_\\后缀"},
            {"id": 2, "wechat_id": "wxid_other", "nickname": "前缀100AB\\后缀"},
        ], ["--mention", "100%_\\"])
        self.assertEqual(result, 0)
        self.assertEqual(post.call_args.args[1]["at"], ["wxid_target"])

    def test_unresolved_id_does_not_send_other_resolved_members(self) -> None:
        result, output, post = self.run_member_send([
            {"id": 1, "wechat_id": "wxid_zhangsan", "nickname": "张三"},
        ], ["--mention", "张三", "--mention-wxid", "wxid_missing", "--content", "请看公告"])
        self.assertEqual(result, 1)
        self.assertFalse(output.endswith("ended"))
        post.assert_not_called()

    def test_mention_ids_are_rejected_in_private_conversations(self) -> None:
        with mock.patch.dict(os.environ, {"ROBOT_FROM_WX_ID": "wxid_friend"}), \
                mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--mention-wxid", "wxid_alice"]), \
                mock.patch.object(self.module, "_mysql_connect") as connect, \
                mock.patch.object(self.module, "_http_post_json") as post, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.module.main(), 1)
            self.assertIn("当前会话不是群聊", output.getvalue())
            connect.assert_not_called()
            post.assert_not_called()

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
            ["--mention-wxid", "wxid_alice"],
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
