#!/usr/bin/env python3
"""Local OCR for a single image used in a PDF workflow."""
from pathlib import Path
import sys
import tempfile

from _pdf_common import SkillArgumentParser, run_cli
from ocr_text import _create_ocr_engine, _ocr_page, MAX_PIXELS_PER_PAGE


def recognize(args):
    from PIL import Image, ImageOps
    source = Path(args.input).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.tif','.tiff','.bmp'}:
        raise ValueError('输入必须是本地图像文件')
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('图像文件不能超过 25 MiB')
    if not 1 <= args.max_chars <= 60000 or args.start_offset < 0:
        raise ValueError('max-chars 需为 1–60000，start-offset 不能小于 0')
    with Image.open(source) as original:
        if original.width * original.height > MAX_PIXELS_PER_PAGE or getattr(original, 'n_frames', 1) != 1:
            raise ValueError('图像上限 2000 万像素，且必须为单帧；多页扫描件请按 PDF 分页 OCR')
        with tempfile.TemporaryDirectory(prefix='pdf-image-ocr-') as folder:
            normalized = Path(folder) / 'image.png'
            ImageOps.exif_transpose(original).convert('RGB').save(normalized)
            result = _ocr_page(_create_ocr_engine(), normalized)
    text = result.pop('text')
    if args.start_offset > len(text):
        raise ValueError('start-offset 超过可靠 OCR 文本长度')
    selection = text[args.start_offset:args.start_offset + args.max_chars]
    next_offset = args.start_offset + len(selection)
    return {'path': str(source), 'engine': 'rapidocr', 'offline': True, **result,
            'text': selection, 'char_count': len(text), 'offset_start': args.start_offset,
            'offset_end': next_offset, 'has_more': next_offset < len(text),
            'next_offset': next_offset if next_offset < len(text) else None}


def main(argv=None):
    parser = SkillArgumentParser(description='PDF 任务图像的本地 OCR，疑难区域保留复核标记')
    parser.add_argument('--input', required=True)
    parser.add_argument('--max-chars', type=int, default=24000)
    parser.add_argument('--start-offset', type=int, default=0)
    return run_cli(lambda: recognize(parser.parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == '__main__':
    raise SystemExit(main())
