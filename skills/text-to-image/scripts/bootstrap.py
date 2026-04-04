#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path

sys.stderr = sys.stdout

def main() -> int:
    script_dir = Path(__file__).resolve().parent
    requirements_file = script_dir / "requirements.txt"

    if not requirements_file.is_file():
        sys.stdout.write(f"未找到依赖文件: {requirements_file}\n")
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
        subprocess.run(command, check=True, stdout=sys.stdout, stderr=sys.stdout)
    except subprocess.CalledProcessError as exc:
        sys.stdout.write(f"安装依赖失败，退出码: {exc.returncode}\n")
        return exc.returncode or 1

    sys.stdout.write("依赖安装完成\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)