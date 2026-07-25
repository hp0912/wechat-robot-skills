#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, NoReturn, Optional
from urllib.parse import quote


EXCEL_INPUT_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm"}
TABULAR_INPUT_SUFFIXES = EXCEL_INPUT_SUFFIXES | {".xls", ".csv", ".tsv"}
EXCEL_OUTPUT_SUFFIXES = {".xlsx", ".xlsm"}
FORMULA_ERROR_VALUES = {
    "#NULL!",
    "#DIV/0!",
    "#VALUE!",
    "#REF!",
    "#NAME?",
    "#NUM!",
    "#N/A",
    "#GETTING_DATA",
    "#SPILL!",
    "#CALC!",
    "#FIELD!",
    "#BLOCKED!",
    "#UNKNOWN!",
}
CELL_REFERENCE_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
CELL_RANGE_RE = re.compile(
    r"^(?P<start>[A-Z]{1,3}[1-9][0-9]{0,6}):"
    r"(?P<end>[A-Z]{1,3}[1-9][0-9]{0,6})$"
)


class SkillArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"参数错误：{message}")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=json_default))


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def failure_message(exc: Exception) -> str:
    if isinstance(exc, FileExistsError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return str(exc)
    if isinstance(exc, PermissionError):
        return "文件处理失败：没有目标路径的访问权限"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"外部程序执行超时（{exc.timeout} 秒）"
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        return f"外部程序执行失败：{stderr or exc}"
    if isinstance(exc, OSError):
        return f"文件处理失败：{exc}"
    return str(exc)


def run_cli(action: Callable[[], dict[str, Any]]) -> int:
    try:
        result = action()
    except Exception as exc:
        emit({"ok": False, "error": failure_message(exc)})
        return 1
    emit({"ok": True, **result})
    return 0


def input_file(value: str, suffixes: Optional[set[str]] = None) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"输入文件不存在：{path}")
    if not path.is_file():
        raise ValueError(f"输入路径不是文件：{path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"输入文件为空：{path}")
    if suffixes is not None and path.suffix.lower() not in suffixes:
        expected = "、".join(sorted(suffixes))
        raise ValueError(f"不支持的输入格式 {path.suffix}；允许：{expected}")
    return path


def output_file(
    value: str,
    suffixes: Optional[set[str]] = None,
    *,
    overwrite: bool = False,
) -> Path:
    path = Path(value).expanduser().resolve()
    if suffixes is not None and path.suffix.lower() not in suffixes:
        expected = "、".join(sorted(suffixes))
        raise ValueError(f"不支持的输出格式 {path.suffix}；允许：{expected}")
    if path.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在：{path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"目标路径不是文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def output_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"输出路径不是目录：{path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish_file(source: Path, destination: Path, *, overwrite: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        os.replace(source, destination)
        return
    try:
        os.link(source, destination)
    except FileExistsError as exc:
        raise FileExistsError(f"目标文件已存在：{destination}") from exc
    except OSError:
        if destination.exists():
            raise FileExistsError(f"目标文件已存在：{destination}")
        shutil.copy2(source, destination)
    finally:
        if source.exists():
            source.unlink()


def load_json_argument(
    inline_value: Optional[str],
    file_value: Optional[str],
    *,
    label: str,
    max_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    if bool(inline_value) == bool(file_value):
        raise ValueError(f"{label} 必须且只能通过内联 JSON 或 JSON 文件提供一次")
    if file_value:
        source = input_file(file_value, {".json"})
        if source.stat().st_size > max_bytes:
            raise ValueError(f"{label} JSON 文件超过 {max_bytes} 字节限制")
        raw = source.read_text(encoding="utf-8")
    else:
        raw = inline_value or ""
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValueError(f"{label} 内联 JSON 超过 {max_bytes} 字节限制")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} JSON 无效：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} JSON 顶层必须是对象")
    return parsed


def validate_cell_reference(value: str, *, label: str = "单元格") -> str:
    normalized = value.strip().upper()
    if not CELL_REFERENCE_RE.fullmatch(normalized):
        raise ValueError(f"{label}引用无效：{value}")
    return normalized


def validate_cell_range(value: str, *, label: str = "区域") -> str:
    normalized = value.replace("$", "").strip().upper()
    if CELL_REFERENCE_RE.fullmatch(normalized):
        return normalized
    if not CELL_RANGE_RE.fullmatch(normalized):
        raise ValueError(f"{label}引用无效：{value}")
    return normalized


def find_program(*names: str) -> str:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(f"运行环境缺少命令：{' / '.join(names)}")


def office_profile_uri(profile: Path) -> str:
    return "file://" + quote(str(profile.resolve()), safe="/:")


def run_program(
    args: list[str],
    *,
    timeout: int,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    if timeout < 1 or timeout > 900:
        raise ValueError("timeout 必须在 1 到 900 秒之间")
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def run_soffice_convert(
    source: Path,
    *,
    target_format: str,
    output_dir: Path,
    timeout: int,
    filter_name: Optional[str] = None,
) -> tuple[Path, dict[str, str]]:
    soffice = find_program("soffice", "libreoffice")
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="xlsx-soffice-profile-"))
    try:
        cache_dir = profile / "cache"
        cache_dir.mkdir()
        process_env = os.environ.copy()
        process_env["XDG_CACHE_HOME"] = str(cache_dir)
        convert_arg = target_format
        if filter_name:
            convert_arg = f"{target_format}:{filter_name}"
        completed = run_program(
            [
                soffice,
                f"-env:UserInstallation={office_profile_uri(profile)}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                "--convert-to",
                convert_arg,
                "--outdir",
                str(output_dir),
                str(source),
            ],
            timeout=timeout,
            env=process_env,
        )
        expected = output_dir / f"{source.stem}.{target_format}"
        if not expected.exists():
            candidates = sorted(output_dir.glob(f"{source.stem}.*"))
            if len(candidates) == 1:
                expected = candidates[0]
            else:
                raise RuntimeError(
                    "LibreOffice 未生成预期文件；"
                    f"stdout={completed.stdout[-1000:]!r} "
                    f"stderr={completed.stderr[-1000:]!r}"
                )
        return expected, {
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def workbook_has_external_links(path: Path) -> bool:
    if path.suffix.lower() not in EXCEL_INPUT_SUFFIXES:
        return False
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            return any(
                name.startswith("xl/externalLinks/") and name.endswith(".xml")
                for name in archive.namelist()
            )
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Excel 文件不是有效的 OOXML 压缩包：{path}") from exc


def openpyxl_load_options(path: Path) -> dict[str, Any]:
    keep_vba = path.suffix.lower() in {".xlsm", ".xltm"}
    return {
        "read_only": False,
        "keep_vba": keep_vba,
        "keep_links": True,
    }


def normalize_formula_error(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if normalized in FORMULA_ERROR_VALUES:
        return normalized
    return None
