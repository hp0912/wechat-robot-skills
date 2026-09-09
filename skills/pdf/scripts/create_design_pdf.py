#!/usr/bin/env python3
"""Render static HTML/CSS through the fixed, offline publication renderer."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from _pdf_common import SkillArgumentParser, new_temp_pdf, output_pdf, publish_temp_file, run_cli

MAX_SOURCE_BYTES = 2 * 1024 * 1024


class StaticHTML(HTMLParser):
    def handle_starttag(self, tag, attributes):
        tag = tag.lower()
        attrs = {key.lower(): value or '' for key, value in attributes}
        if tag in {'script', 'iframe', 'object', 'embed', 'base', 'frame', 'frameset'}:
            raise ValueError(f'HTML 不允许 {tag}；仅支持静态 HTML/CSS/SVG，公式和 Mermaid 由固定渲染器处理')
        if any(key.startswith('on') for key in attrs) or 'srcdoc' in attrs:
            raise ValueError('HTML 不允许事件处理程序或 srcdoc')
        if tag == 'meta' and 'http-equiv' in attrs:
            raise ValueError('HTML 不允许 http-equiv；网络和文档策略由固定渲染器设置')
        for key in ('href', 'src', 'xlink:href', 'action', 'formaction'):
            value = ''.join(attrs.get(key, '').split()).lower()
            if value.startswith(('javascript:', 'vbscript:', 'file:')):
                raise ValueError('HTML 不允许脚本 URL 或 file: 资源；使用任务目录内相对路径')

    handle_startendtag = handle_starttag


def read_source(value: str, suffixes: set[str]) -> Path:
    source = Path(value).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in suffixes:
        raise ValueError(f'输入必须是本地 {sorted(suffixes)} 文件')
    if not 0 < source.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError('HTML/CSS 文件必须非空且不超过 2 MiB')
    return source


def create(args) -> dict:
    from pypdf import PdfReader

    source = read_source(args.input, {'.html', '.htm'})
    text = source.read_text(encoding='utf-8-sig')
    validator = StaticHTML(convert_charrefs=True)
    validator.feed(text)
    validator.close()
    css = read_source(args.css, {'.css'}) if args.css else None
    if not 10 <= args.timeout <= 600:
        raise ValueError('timeout 必须在 10–600 秒之间')
    if args.expected_pages is not None and not 1 <= args.expected_pages <= 200:
        raise ValueError('expected-pages 必须在 1–200 之间')
    output = output_pdf(args.output, args.overwrite)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('基础镜像缺少预置 Node 运行时；需要更新镜像，任务中不能安装')
    temporary = new_temp_pdf(output)
    try:
        with tempfile.TemporaryDirectory(prefix='pdf-design-') as folder:
            request = Path(folder) / 'request.json'
            request.write_text(json.dumps({'input': str(source), 'output': str(temporary), 'css': str(css) if css else None,
                                           'page_size': args.page_size, 'timeout_ms': args.timeout * 1000,
                                           'assets': str(Path(__file__).resolve().parents[1] / 'assets')}, ensure_ascii=False))
            result = subprocess.run([node, str(Path(__file__).with_name('_render_html.cjs')), str(request)],
                                    capture_output=True, text=True, timeout=args.timeout + 20, check=False)
            try:
                details = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise RuntimeError('排版器未返回有效 JSON：' + (result.stderr or result.stdout)[-1500:])
            if result.returncode or not details.get('ok'):
                raise RuntimeError(details.get('error', 'HTML 排版失败'))
            with temporary.open('rb') as handle:
                reader = PdfReader(handle)
                page_count = len(reader.pages)
                if reader.is_encrypted or not page_count:
                    raise ValueError('排版器未生成有效 PDF')
                if page_count != details['page_count']:
                    raise ValueError(f'分页结果与 PDF 页数不一致：{details["page_count"]} / {page_count}')
                if args.expected_pages is not None and page_count != args.expected_pages:
                    raise ValueError(f'实际 {page_count} 页，与要求的 {args.expected_pages} 页不符；请调整排版后重试')
            publish_temp_file(temporary, output, args.overwrite)
    finally:
        temporary.unlink(missing_ok=True)
    details.pop('ok', None)
    return {'path': str(output), 'source': str(source), 'size_bytes': output.stat().st_size, **details,
            'requires_visual_review': True}


def main(argv=None) -> int:
    parser = SkillArgumentParser(description='静态 HTML/CSS 排版为 PDF；使用离线 Paged.js、KaTeX、Mermaid 和系统 Chromium')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--css')
    parser.add_argument('--page-size', choices=('A4', 'LETTER'), default='A4')
    parser.add_argument('--expected-pages', type=int)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--overwrite', action='store_true')
    return run_cli(lambda: create(parser.parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == '__main__':
    raise SystemExit(main())
