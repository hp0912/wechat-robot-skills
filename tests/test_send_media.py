import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILLS = Path(__file__).resolve().parents[1] / "skills"
CLIENT_URL = "http://127.0.0.1:9876/api/v1/robot/message/send/"
SUCCESS = {"code": 200, "data": None}


class SendMediaTests(unittest.TestCase):
    def run_sender(self, skill, args, replies):
        script = SKILLS / skill / "scripts" / (skill.replace("-", "_") + ".py")
        output = io.StringIO()
        env = {"ROBOT_WECHAT_CLIENT_PORT": "9876", "ROBOT_FROM_WX_ID": "test-room"}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(sys, "argv", [str(script), *args]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
            mock.patch("urllib.request.urlopen", side_effect=[
                io.BytesIO(json.dumps(reply).encode()) for reply in replies
            ]) as post,
            self.assertRaises(SystemExit) as stopped,
        ):
            runpy.run_path(str(script), run_name="__main__")
        requests = [
            (request.full_url, json.loads(request.data))
            for (request,), _ in post.call_args_list
        ]
        return stopped.exception.code, output.getvalue(), requests

    def test_image_paths_and_urls_use_image_api_and_forward_original(self):
        url = "https://example.test/reaction.gif"
        with tempfile.TemporaryDirectory() as directory:
            cases = [
                ("reaction.gif", [], False),
                ("photo.jpg", ["--send_original"], True),
                ("photo.png", ["--send_original", "false"], False),
            ]
            for filename, flags, original in cases:
                with self.subTest(flags=flags):
                    path = Path(directory, filename).resolve()
                    path.write_bytes(b"image")
                    code, _, requests = self.run_sender(
                        "send-image", ["--file_path", str(path), "--image_url", url, *flags],
                        [SUCCESS, {"code": 200, "data": [f"成功: {url}"]}],
                    )
                    self.assertEqual(code, 0)
                    common = {"to_wxid": "test-room", "send_original": original}
                    self.assertEqual(requests, [
                        (CLIENT_URL + "image/local", {**common, "file_path": str(path)}),
                        (CLIENT_URL + "image/url", {**common, "image_urls": [url]}),
                    ])

    def test_gif_as_an_attachment_and_large_files_use_file_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "reaction.gif").resolve()
            with path.open("wb") as source:
                source.truncate(30 * 1024 * 1024)
            code, _, requests = self.run_sender("send-file", ["--file_path", str(path)], [SUCCESS])
        self.assertEqual(code, 0)
        self.assertEqual(requests, [
            (CLIENT_URL + "file/local", {"to_wxid": "test-room", "file_path": str(path)}),
        ])

    def test_partial_url_failure_reports_the_client_reason(self):
        urls = ["https://example.test/one", "https://example.test/two"]
        failure = f"失败: {urls[1]} 错误: 上传失败"
        for skill, field in (("send-image", "image_urls"), ("send-file", "file_urls")):
            with self.subTest(skill=skill):
                code, output, requests = self.run_sender(
                    skill, ["--" + field, json.dumps(urls)],
                    [{"code": 200, "data": [f"成功: {urls[0]}", failure]}],
                )
                self.assertEqual(len(requests), 1)
                self.assertNotEqual(code, 0)
                self.assertIn(failure, output)

    def test_named_emoji_ends_only_on_client_success(self):
        for reply, expected in ((SUCCESS, 0), ({"code": 500, "message": "表情素材不可用"}, 1)):
            with self.subTest(reply=reply):
                code, output, requests = self.run_sender(
                    "send-emoji", ["--name", "[开心]", "--ended"], [reply],
                )
                self.assertEqual(code, expected)
                body = {
                    "to_wxid": "test-room",
                    "Md5": "4aae9dbe27651859ed526bcaad5f1f34",
                    "TotalLen": 19788,
                }
                self.assertEqual(requests, [(CLIENT_URL + "emoji", body)])
                self.assertEqual(output.splitlines()[-1] == "ended", expected == 0)
                if expected:
                    self.assertIn("表情素材不可用", output)
