from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/send-complex-message/scripts/send_complex_message.py"
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


if __name__ == "__main__":
    unittest.main()
