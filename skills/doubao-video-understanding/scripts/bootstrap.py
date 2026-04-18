#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import subprocess
import sys
import traceback
from pathlib import Path

sys.stderr = sys.stdout


def _skill_root_from(script_dir: Path) -> Path:
    return script_dir.parent


def _venv_dir(script_dir: Path) -> Path:
    return _skill_root_from(script_dir) / ".venv"


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _stamp_file(venv_dir: Path) -> Path:
    return venv_dir / ".req_hash"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deps_up_to_date(requirements_file: Path, venv_dir: Path) -> bool:
    stamp = _stamp_file(venv_dir)
    if not stamp.is_file():
        return False
    return stamp.read_text().strip() == _file_hash(requirements_file)


def _write_stamp(requirements_file: Path, venv_dir: Path) -> None:
    _stamp_file(venv_dir).write_text(_file_hash(requirements_file))


def _ensure_venv(venv_dir: Path, venv_python: Path) -> int:
    if venv_python.is_file():
        return 0

    sys.stdout.write(f"未检测到技能虚拟环境，正在创建: {venv_dir}\n")
    command = [
        sys.executable,
        "-m",
        "venv",
        str(venv_dir),
    ]

    try:
        subprocess.run(command, check=True, stdout=sys.stdout, stderr=sys.stdout)
    except subprocess.CalledProcessError as exc:
        sys.stdout.write(f"创建虚拟环境失败，退出码: {exc.returncode}\n")
        return exc.returncode or 1

    return 0


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    requirements_file = script_dir / "requirements.txt"
    venv_dir = _venv_dir(script_dir)
    venv_python = _venv_python(venv_dir)

    if not requirements_file.is_file():
        sys.stdout.write(f"未找到依赖文件: {requirements_file}\n")
        return 1

    ensure_result = _ensure_venv(venv_dir, venv_python)
    if ensure_result != 0:
        return ensure_result

    if _deps_up_to_date(requirements_file, venv_dir):
        sys.stdout.write("依赖已是最新，跳过安装\n")
        return 0

    command = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    ]

    try:
        subprocess.run(command, check=True, stdout=sys.stdout, stderr=sys.stdout)
    except subprocess.CalledProcessError as exc:
        sys.stdout.write(f"升级 pip 失败，退出码: {exc.returncode}\n")
        return exc.returncode or 1

    command = [
        str(venv_python),
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

    _write_stamp(requirements_file, venv_dir)
    sys.stdout.write(f"依赖安装完成，当前技能虚拟环境: {venv_dir}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(1)