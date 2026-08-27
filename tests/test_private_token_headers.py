import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PrivateTokenHeaderTests(unittest.TestCase):
    def test_every_local_client_api_script_supports_optional_private_token(self) -> None:
        client_api_scripts: list[Path] = []
        for pattern in ("*.py", "*.ts"):
            for script in (REPO_ROOT / "skills").glob(f"*/scripts/{pattern}"):
                content = script.read_text(encoding="utf-8")
                if "/api/v1/robot" not in content:
                    continue
                client_api_scripts.append(script)
                self.assertIn("ROBOT_CLIENT_PRIVATE_TOKEN", content, script)
                self.assertIn("X-Private-Token", content, script)
                self.assertNotIn(
                    "环境变量 ROBOT_CLIENT_PRIVATE_TOKEN 未配置", content, script
                )

        self.assertTrue(client_api_scripts, "no local client API scripts were found")


if __name__ == "__main__":
    unittest.main()
