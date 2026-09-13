#!/usr/bin/env python3
"""Compile a LaTeX project with cached Tectonic resources and shell escape disabled."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from _pdf_common import (
    SkillArgumentParser,
    output_pdf,
    new_temp_pdf,
    publish_temp_file,
    run_cli,
)


def compile_document(args):
    from pypdf import PdfReader

    source = Path(args.input).expanduser().resolve()
    if (
        not source.is_file()
        or source.suffix.lower() != ".tex"
        or not 0 < source.stat().st_size <= 2 * 1024 * 1024
    ):
        raise ValueError("输入需为不超过 2 MiB 的本地 .tex 文件")
    if not 1 <= args.timeout <= 600:
        raise ValueError("timeout 必须在 1–600 秒之间")
    executable = shutil.which("tectonic")
    if not executable:
        raise RuntimeError("基础镜像缺少预置 Tectonic，需要更新镜像")
    target = output_pdf(args.output, args.overwrite)
    temporary = new_temp_pdf(target)
    try:
        with tempfile.TemporaryDirectory(prefix="pdf-latex-") as folder:
            command = [
                executable,
                "--untrusted",
                "--only-cached",
                "--keep-logs",
                "--outdir",
                folder,
                str(source),
            ]
            completed = subprocess.run(
                command,
                cwd=source.parent,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
            )
            result = Path(folder) / (source.stem + ".pdf")
            messages = (completed.stdout + "\n" + completed.stderr).splitlines()
            if completed.returncode or not result.is_file():
                detail = "\n".join(messages[-15:])
                raise RuntimeError(
                    "LaTeX 编译失败；缺失 TeX 包需在基础镜像构建时预置，任务中不下载："
                    + detail
                )
            count = len(PdfReader(result).pages)
            if not count:
                raise ValueError("LaTeX 没有生成有效 PDF")
            logfile = Path(folder) / (source.stem + ".log")
            if logfile.exists():
                messages += logfile.read_text(errors="replace").splitlines()
            warnings = list(
                dict.fromkeys(
                    line.strip()
                    for line in messages
                    if re.search(
                        r"warning:|Overfull|Missing character|undefined references",
                        line,
                        re.I,
                    )
                )
            )[:30]
            shutil.copyfile(result, temporary)
        publish_temp_file(temporary, target, args.overwrite)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "source": str(source),
        "path": str(target),
        "page_count": count,
        "engine": "tectonic",
        "dependency_mode": "cached-only",
        "warnings": warnings,
        "requires_visual_review": True,
    }


def main(argv=None):
    parser = SkillArgumentParser(
        description="固定 LaTeX 编译接口，禁用 shell escape，只使用镜像内缓存包"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--overwrite", action="store_true")
    return run_cli(
        lambda: compile_document(
            parser.parse_args(sys.argv[1:] if argv is None else argv)
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
