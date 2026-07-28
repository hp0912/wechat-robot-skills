from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "pdf": {
        "common_module": "_pdf_common",
        "root_name": "PDF_OUTPUT_ROOT",
        "source_downloader": "download_pdf.py",
        "source_suffix": ".pdf",
    },
    "docx": {
        "common_module": "_docx_common",
        "root_name": "WORD_OUTPUT_ROOT",
        "source_downloader": "download_document.py",
        "source_suffix": ".docx",
    },
    "xlsx": {
        "common_module": "_xlsx_common",
        "root_name": "EXCEL_OUTPUT_ROOT",
        "source_downloader": "download_workbook.py",
        "source_suffix": ".xlsx",
    },
    "pptx": {
        "common_module": "_pptx_common",
        "root_name": "PPT_OUTPUT_ROOT",
        "source_downloader": "download_presentation.py",
        "source_suffix": ".pptx",
    },
}


def load_script(skill: str, filename: str):
    script_path = REPOSITORY_ROOT / "skills" / skill / "scripts" / filename
    scripts_directory = str(script_path.parent)
    sys.path.insert(0, scripts_directory)
    try:
        module_name = f"_test_{skill}_{script_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载测试脚本：{script_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(scripts_directory)


class FakeResponse:
    def __init__(
        self,
        *,
        headers: dict[str, str],
        payload: bytes = b"",
        generated_size: int = 0,
    ) -> None:
        self.headers = headers
        self._payload = payload
        self._payload_offset = 0
        self._remaining = generated_size

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self) -> str:
        return "https://example.test/attachment"

    def read(self, size: int) -> bytes:
        if self._remaining:
            chunk_size = min(size, self._remaining)
            self._remaining -= chunk_size
            return b"x" * chunk_size
        if self._payload_offset >= len(self._payload):
            return b""
        end = min(self._payload_offset + size, len(self._payload))
        chunk = self._payload[self._payload_offset : end]
        self._payload_offset = end
        return chunk


class FakeOpener:
    def __init__(
        self,
        *,
        head_headers: dict[str, str],
        get_headers: dict[str, str],
        payload: bytes = b"",
        generated_size: int = 0,
    ) -> None:
        self.head_headers = head_headers
        self.get_headers = get_headers
        self.payload = payload
        self.generated_size = generated_size
        self.methods: list[str] = []

    def open(self, request, timeout: int):
        del timeout
        method = request.get_method()
        self.methods.append(method)
        if method == "HEAD":
            return FakeResponse(headers=self.head_headers)
        return FakeResponse(
            headers=self.get_headers,
            payload=self.payload,
            generated_size=self.generated_size,
        )


class DownloadAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.modules = {
            skill: load_script(skill, "download_attachment.py")
            for skill in SKILLS
        }
        cls.source_modules = {
            skill: load_script(skill, details["source_downloader"])
            for skill, details in SKILLS.items()
        }

    def test_each_skill_downloads_an_arbitrary_attachment_type(self) -> None:
        payload = b"small video payload"
        for skill, module in self.modules.items():
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "material.mp4"
                common = sys.modules[SKILLS[skill]["common_module"]]
                opener = FakeOpener(
                    head_headers={"Content-Length": str(len(payload))},
                    get_headers={
                        "Content-Length": str(len(payload)),
                        "Content-Type": "video/mp4; charset=binary",
                    },
                    payload=payload,
                )
                with mock.patch.object(
                    common,
                    SKILLS[skill]["root_name"],
                    Path(tmp).resolve(),
                ):
                    with mock.patch.object(
                        module.urllib.request,
                        "build_opener",
                        return_value=opener,
                    ):
                        args = module._parse_args(
                            [
                                "--url",
                                "https://example.test/material.mp4",
                                "--output",
                                str(output),
                            ]
                        )
                        result = module._download(args)

                self.assertEqual(output.read_bytes(), payload)
                self.assertEqual(opener.methods, ["HEAD", "GET"])
                self.assertEqual(result["size_bytes"], len(payload))
                self.assertEqual(
                    result["size_limit_bytes"],
                    25 * 1024 * 1024,
                )
                self.assertEqual(result["content_type"], "video/mp4")

    def test_head_probe_rejects_oversize_without_get(self) -> None:
        for skill, module in self.modules.items():
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "too-large.zip"
                common = sys.modules[SKILLS[skill]["common_module"]]
                opener = FakeOpener(
                    head_headers={
                        "Content-Length": str(module.MAX_ATTACHMENT_BYTES + 1)
                    },
                    get_headers={},
                )
                stdout = io.StringIO()
                with mock.patch.object(
                    common,
                    SKILLS[skill]["root_name"],
                    Path(tmp).resolve(),
                ):
                    with mock.patch.object(
                        module.urllib.request,
                        "build_opener",
                        return_value=opener,
                    ):
                        with contextlib.redirect_stdout(stdout):
                            return_code = module.main(
                                [
                                    "--url",
                                    "https://example.test/too-large.zip",
                                    "--output",
                                    str(output),
                                ]
                            )

                response = json.loads(stdout.getvalue())
                self.assertEqual(return_code, 1)
                self.assertFalse(response["ok"])
                self.assertRegex(
                    response["error"],
                    "超过 25 MiB.*已拒绝下载",
                )
                self.assertEqual(opener.methods, ["HEAD"])
                self.assertFalse(output.exists())

    def test_stream_limit_rejects_and_removes_partial_file(self) -> None:
        for skill, module in self.modules.items():
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "unknown-size.bin"
                opener = FakeOpener(
                    head_headers={},
                    get_headers={"Content-Type": "application/octet-stream"},
                    generated_size=module.MAX_ATTACHMENT_BYTES + 1,
                )
                args = argparse.Namespace(
                    url="https://example.test/unknown-size.bin",
                    output=output,
                    timeout=60,
                    overwrite=False,
                )
                with mock.patch.object(
                    module.urllib.request,
                    "build_opener",
                    return_value=opener,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "超过 25 MiB.*已拒绝下载",
                    ):
                        module._download(args)

                self.assertEqual(opener.methods, ["HEAD", "GET"])
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_get_content_length_rejects_when_head_has_no_size(self) -> None:
        for skill, module in self.modules.items():
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "get-declared-large.mov"
                opener = FakeOpener(
                    head_headers={},
                    get_headers={
                        "Content-Length": str(module.MAX_ATTACHMENT_BYTES + 1)
                    },
                )
                args = argparse.Namespace(
                    url="https://example.test/get-declared-large.mov",
                    output=output,
                    timeout=60,
                    overwrite=False,
                )
                with mock.patch.object(
                    module.urllib.request,
                    "build_opener",
                    return_value=opener,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "超过 25 MiB.*已拒绝下载",
                    ):
                        module._download(args)

                self.assertEqual(opener.methods, ["HEAD", "GET"])
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_source_downloaders_cannot_raise_the_25_mib_limit(self) -> None:
        expected_limit = 25 * 1024 * 1024
        for skill, module in self.source_modules.items():
            with self.subTest(skill=skill):
                self.assertEqual(module.DEFAULT_MAX_BYTES, expected_limit)
                self.assertEqual(module.MAX_ALLOWED_BYTES, expected_limit)
                with self.assertRaisesRegex(
                    ValueError,
                    f"1 到 {expected_limit}",
                ):
                    module._parse_args(
                        [
                            "--url",
                            "https://example.test/source",
                            "--output",
                            "/outside/source"
                            + SKILLS[skill]["source_suffix"],
                            "--max-bytes",
                            str(expected_limit + 1),
                        ]
                    )


if __name__ == "__main__":
    unittest.main()
