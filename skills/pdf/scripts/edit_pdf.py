#!/usr/bin/env python3
"""AcroForm, crop, metadata and embedded-image operations using existing pypdf."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from _pdf_common import (SkillArgumentParser, input_pdf, output_pdf, output_directory,
                         new_temp_pdf, publish_temp_file, parse_page_spec, run_cli)


def load_data(inline, filename):
    if bool(inline) == bool(filename):
        raise ValueError('必须且只能提供 --data 或 --data-file')
    if filename and Path(filename).stat().st_size > 2 * 1024 * 1024:
        raise ValueError('JSON 上限为 2 MiB')
    raw = Path(filename).read_bytes() if filename else inline.encode('utf-8')
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('JSON 上限为 2 MiB')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('JSON 必须是对象')
    return data


def inherited_value(field, key, default=None):
    """Read an inheritable field attribute without following malformed cycles."""
    seen = set()
    while field is not None:
        field = field.get_object()
        if id(field) in seen:
            raise ValueError('PDF 表单字段的父级引用存在循环')
        seen.add(id(field))
        if key in field:
            return field[key]
        field = field.get('/Parent')
    return default


def field_info(reader):
    result = {}
    for name, field in (reader.get_fields() or {}).items():
        # get_fields() is a summary: /AP and /MaxLen live on the full object.
        original = field.indirect_reference.get_object()
        ft = str(inherited_value(original, '/FT', ''))
        flags = int(inherited_value(original, '/Ff', 0))
        kind = {'/Tx':'text', '/Ch':'choice', '/Sig':'signature'}.get(ft, 'unknown')
        if ft == '/Btn':
            kind = 'pushbutton' if flags & (1 << 16) else ('radio' if flags & (1 << 15) else 'checkbox')
        widgets = [original, *[kid.get_object() for kid in original.get('/Kids', [])]]
        states = set()
        if kind in {'checkbox', 'radio'}:
            for widget in widgets:
                appearance = widget.get('/AP')
                normal = appearance.get_object().get('/N') if appearance is not None else None
                if normal is not None:
                    states.update(str(key) for key in normal.get_object().keys())
            if kind == 'radio' and flags & (1 << 14):
                states.discard('/Off')
        options = [{'value':str(option[0]), 'label':str(option[1])} if isinstance(option, list) else {'value':str(option), 'label':str(option)}
                   for option in inherited_value(original, '/Opt', [])]
        value = inherited_value(original, '/V')
        result[name] = {'id':name, 'type':kind, 'read_only':bool(flags & 1), 'flags':flags,
                        'current_value':[str(v) for v in value] if isinstance(value, list) else (str(value) if value is not None else None),
                        'states':sorted(states), 'options':options, 'max_length':inherited_value(original, '/MaxLen')}
    return result


def validated_values(infos, data):
    from pypdf.generic import NameObject
    if not data or len(data) > 500:
        raise ValueError('填表数据需为 1–500 个字段')
    values = {}
    for name, value in data.items():
        if name not in infos:
            raise ValueError(f'表单字段不存在：{name}')
        field = infos[name]
        if field['read_only']:
            raise ValueError(f'字段为只读：{name}')
        kind = field['type']
        if kind in {'signature','pushbutton','unknown'}:
            raise ValueError(f'不支持填写 {kind} 字段：{name}')
        if kind in {'checkbox','radio'}:
            states = field['states']
            if type(value) is bool and kind == 'checkbox':
                choices = [s for s in states if s != '/Off']
                if value and len(choices) != 1:
                    raise ValueError(f'{name} 的选中状态不唯一，请使用具体状态名')
                value = choices[0] if value else '/Off'
            elif isinstance(value, str):
                value = '/' + value.lstrip('/')
            else:
                raise ValueError(f'{name} 需布尔值或有效状态名')
            if value not in states:
                raise ValueError(f'{name} 状态无效；可选：{states}')
            values[name] = NameObject(value)
        elif kind == 'choice':
            choices = {entry['value'] for entry in field['options']}
            selected = value if isinstance(value, list) else [value]
            if isinstance(value, list) and not field['flags'] & (1 << 21):
                raise ValueError(f'{name} 不支持多选')
            editable = bool(field['flags'] & (1 << 18))
            if not all(isinstance(item, str) and (editable or item in choices) for item in selected):
                raise ValueError(f'{name} 选项无效；可选：{sorted(choices)}')
            values[name] = value
        else:
            if not isinstance(value, str) or (field['max_length'] and len(value) > field['max_length']):
                raise ValueError(f'{name} 需文本，且不得超过字段长度上限')
            values[name] = value
    return values


def execute(args):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    source = input_pdf(args.input)
    with source.open('rb') as handle:
        reader = PdfReader(handle)
        if reader.is_encrypted:
            raise ValueError('PDF 已加密，请提供已解密副本')
        if args.operation == 'form-info':
            fields = list(field_info(reader).values())
            if args.offset < 0 or not 1 <= args.limit <= 200:
                raise ValueError('offset 不能小于 0，limit 需为 1–200')
            stop = min(len(fields), args.offset + args.limit)
            acroform = reader.trailer['/Root'].get('/AcroForm')
            return {'field_count':len(fields), 'fields':fields[args.offset:stop], 'has_more':stop < len(fields),
                    'next_offset':stop if stop < len(fields) else None,
                    'has_xfa':bool(acroform and '/XFA' in acroform.get_object())}
        if args.operation == 'extract-images':
            return extract_images(reader, args)
        output = output_pdf(args.output, args.overwrite)
        if output == source:
            raise ValueError('输出文件不能覆盖源 PDF')
        writer = PdfWriter(clone_from=reader)
        temporary = new_temp_pdf(output)
        extra = {}
        try:
            if args.operation == 'form-fill':
                acroform = reader.trailer['/Root'].get('/AcroForm')
                if acroform and '/XFA' in acroform.get_object():
                    raise ValueError('XFA 表单不属于 AcroForm 固定接口，不能声称填写成功')
                values = validated_values(field_info(reader), load_data(args.data, args.data_file))
                writer.update_page_form_field_values(None, values, auto_regenerate=False)
                extra = {'fields_filled':list(values), 'requires_visual_review':True}
            elif args.operation == 'metadata':
                data = load_data(args.data, args.data_file)
                allowed = {'Title','Author','Subject','Keywords','Creator','Producer'}
                if set(data) - allowed or not all(isinstance(v,str) and len(v) <= 4096 for v in data.values()):
                    raise ValueError('元数据仅支持 Title/Author/Subject/Keywords/Creator/Producer 文本字段')
                writer.add_metadata({'/'+key:value for key,value in data.items()})
                extra = {'updated_keys':list(data), 'xmp_preserved':'/Metadata' in reader.trailer['/Root']}
            elif args.operation == 'crop':
                box = [float(v) for v in args.box.split(',')]
                if len(box) != 4 or not all(math.isfinite(v) for v in box) or box[0] >= box[2] or box[1] >= box[3]:
                    raise ValueError('box 需为左,下,右,上四个有限坐标，单位 pt')
                pages = parse_page_spec(args.pages, len(writer.pages))
                for number in pages:
                    media = writer.pages[number-1].mediabox
                    if box[0] < media.left or box[1] < media.bottom or box[2] > media.right or box[3] > media.top:
                        raise ValueError(f'裁剪框超出第 {number} 页 MediaBox')
                    writer.pages[number-1].cropbox = RectangleObject(box)
                extra = {'cropped_pages':pages, 'box':box, 'warning':'裁剪只改变可见范围，不删除隐藏内容，不能用于脱敏。'}
            with temporary.open('wb') as stream:
                writer.write(stream)
            check = PdfReader(temporary)
            if len(check.pages) != len(reader.pages):
                raise ValueError('编辑后页数异常')
            if args.operation == 'form-fill':
                actual = field_info(check)
                for name, value in values.items():
                    wanted = [str(v) for v in value] if isinstance(value, list) else str(value)
                    if name not in actual or actual[name]['current_value'] != wanted:
                        raise ValueError(f'字段回读校验失败：{name}')
            publish_temp_file(temporary, output, args.overwrite)
        finally:
            writer.close()
            temporary.unlink(missing_ok=True)
        return {'path':str(output), 'source':str(source), 'page_count':len(reader.pages), 'operation':args.operation, **extra}


def extract_images(reader, args):
    if not 1 <= args.max_images <= 50 or args.start_image < 0:
        raise ValueError('max-images 需为 1–50，start-image 不能小于 0')
    pages = parse_page_spec(args.pages, len(reader.pages))
    if len(pages) > 4:
        raise ValueError('一次最多处理 4 页，请指定 pages')
    destination = output_directory(args.output_dir)
    result, total_bytes = [], 0
    for page_index, number in enumerate(pages):
        images = reader.pages[number-1].images
        start = args.start_image if page_index == 0 else 0
        for index in range(start, len(images)):
            if len(result) >= args.max_images:
                return {'images':result, 'has_more':True, 'next_page':number, 'next_image':index,
                        'remaining_pages':pages[page_index:], 'note':'嵌入图片不是整页截图；页面外观请用 render_pdf.py。'}
            item = images[index]
            if item.image.width * item.image.height > 20_000_000:
                raise ValueError('嵌入图像超过 2000 万像素，请改为限制 DPI 的页面渲染')
            total_bytes += len(item.data)
            if total_bytes > 25 * 1024 * 1024:
                raise ValueError('本批嵌入图片超过 25 MiB，请减少页数或图片数')
            extension = Path(item.name).suffix.lower()
            if extension not in {'.png','.jpg','.jpeg','.jp2','.tif','.tiff'}:
                raise ValueError(f'不支持的嵌入图片编码：{extension}，请用页面渲染')
            output = destination / f'page-{number:04d}-image-{index:03d}{extension}'
            temporary = new_temp_pdf(output)
            try:
                temporary.write_bytes(item.data)
                publish_temp_file(temporary, output, args.overwrite)
            finally:
                temporary.unlink(missing_ok=True)
            result.append({'page':number, 'image_index':index, 'path':str(output)})
    return {'images':result, 'has_more':False, 'note':'嵌入图片不是整页截图；页面外观请用 render_pdf.py。'}


def main(argv=None):
    parser = SkillArgumentParser(description='PDF 表单、裁剪、元数据和嵌入图片固定接口')
    commands = parser.add_subparsers(dest='operation', required=True)
    for name in ('form-info','form-fill','metadata','crop','extract-images'):
        command = commands.add_parser(name)
        command.add_argument('--input', required=True)
        if name == 'form-info':
            command.add_argument('--offset', type=int, default=0)
            command.add_argument('--limit', type=int, default=50)
        else:
            command.add_argument('--overwrite', action='store_true')
            command.add_argument('--output-dir' if name == 'extract-images' else '--output', required=True)
        if name in {'form-fill','metadata'}:
            command.add_argument('--data')
            command.add_argument('--data-file')
        if name in {'crop','extract-images'}:
            command.add_argument('--pages')
        if name == 'crop':
            command.add_argument('--box', required=True)
        if name == 'extract-images':
            command.add_argument('--start-image', type=int, default=0)
            command.add_argument('--max-images', type=int, default=20)
    return run_cli(lambda: execute(parser.parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == '__main__':
    raise SystemExit(main())
