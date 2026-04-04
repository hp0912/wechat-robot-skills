#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    requirements_file = script_dir / "requirements.txt"

    if not requirements_file.is_file():
        sys.stderr.write(f"未找到依赖文件: {requirements_file}\n")
        return 1

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(requirements_file),
    ]

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"安装依赖失败，退出码: {exc.returncode}\n")
        return exc.returncode or 1

    sys.stdout.write("依赖安装完成\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())