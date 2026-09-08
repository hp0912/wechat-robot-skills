# Word 文件操作参数

所有脚本通过 `execute_skill_script` 调用，`skill_name="docx"`，`script_path` 从 skill 根目录开始。下文只列参数，不是 shell 命令。文件路径使用容器内绝对路径。

## 下载远程文档

只接受 HTTPS 地址。完整保留 URL 及查询参数传给脚本，但不要在回复、日志摘要或输出文件名中复述敏感参数。

调用 `scripts/download_document.py`：

```text
--url 'https://example.com/report.docx?signature=...' --output '/usr/local/src/word/tmp/<任务名>/source.docx'
```

可选参数：

- `--timeout <1-600>`：连接和读取超时秒数，默认 `60`。
- `--max-bytes <字节数>`：默认且最高 `26214400`（25 MiB），只允许设置更小的限制。
- `--overwrite`：只在目标是本次任务生成的旧缓存时使用。

`output` 扩展名必须是 `.docx`、`.dotx` 或 `.doc`。脚本阻止 HTTPS 重定向降级到 HTTP，流式限制大小，先写同目录临时文件，再原子发布；DOCX/DOTX 会检查 ZIP 路径、成员大小、必要部件和内容类型，并用 `python-docx` 打开。实际 OOXML 格式与 `output` 扩展名不一致时，根据错误中的实际格式更正缓存扩展名，再调用同一脚本。

成功结果包含 `path`、`size_bytes`、`format` 和 `validation`；OOXML 还包含段落、表格和章节数量。后续脚本只使用返回的本地 `path`，不再访问原 URL。

## 下载通用附件

需要下载作为 Word 任务素材的图片、视频、音频、压缩包或其他文件时，调用 `scripts/download_attachment.py`：

```text
--url 'https://example.com/asset.bin?signature=...' --output '/usr/local/src/word/tmp/<任务名>/asset.bin'
```

只接受 HTTPS 地址，`output` 可使用任意附件扩展名。可选参数只有 `--timeout <1-600>`（默认 `60`）和 `--overwrite`。附件上限固定为 25 MiB（26214400 字节），不可调高：脚本先用 HEAD 探测远端声明大小，再检查 GET 响应声明，并在流式接收时持续兜底计数；任一阶段发现超限都会返回 `ok: false` 和明确的“已拒绝下载”错误，且不会发布部分文件。

成功结果包含 `path`、实际 `size_bytes`、`declared_size_bytes`、`size_limit_bytes`、`size_probe` 和 `content_type`。本脚本不校验文件业务格式；远程 Word 源文档仍使用 `download_document.py`。

## 检查文档

调用 `scripts/inspect_document.py`：

```text
--input '/usr/local/src/word/tmp/<任务名>/source.docx'
```

可选参数：

- `--start-paragraph <索引>`、`--max-paragraphs <1-300>`：分段读取正文，索引从 `0` 开始。
- `--start-table <索引>`、`--max-tables <0-50>`、`--max-table-cells <数量>`：限制表格输出。
- `--max-chars <1000-200000>`：限制单次正文字符数。
- `--include-runs`：需要检查局部字体、粗体、斜体或跨 Run 替换问题时使用。

重点检查：

- `tracked_changes.total` 和 `authors`：是否存在修订及修订作者。
- `comments`：批注正文和作者。
- `sections`：纸张、方向、页边距、页眉、页脚。
- `has_images`、`inline_image_count`、`media_part_count`：是否需要进一步读取图片文字；浮动图片可能只计入媒体部件。
- `archive.missing_required_parts`、`duplicate_members`：结构异常。
- `has_more`、`next_paragraph`、`next_table`：继续读取长文档。

## 识别图片中的文字

需要读取图片、截图或扫描页中的文字时调用：

```text
--input '/usr/local/src/word/tmp/<任务名>/source.docx'
```

省略 `--pages` 时，脚本会把 Word 临时转换为 PDF，自动选择包含足够大图片的页面，每次最多处理 4 页。需要识别较小图片或指定页面时传：

```text
--input '/usr/local/src/word/tmp/<任务名>/source.docx' --pages '2,5-6'
```

脚本通过 LibreOffice 和 Poppler 临时渲染页面，使用本地 RapidOCR 识别图片区域；临时 PDF 和 PNG 会自动删除，不联网，也不调用大模型识图。PDF 原生文本层用于过滤正文、页眉、页脚和页码产生的重复 OCR，因此 `pages[].text` 只返回可靠的额外图片文字。

检查：

- `candidate_pages`：自动检测到的图片页；`selection_mode` 表示自动或显式选页。
- `status: good` 且 `usable_for_summary: true`：可以把 `text` 补充到原生文档内容中。
- `status: no_image_text`：图片区域没有识别到额外文字，不是错误。
- `status: sparse` 或 `low_confidence`：不要使用返回文字；根据 `needs_review` 人工核验。
- `filtered_native_line_count` 和 `filtered_outside_image_line_count`：被当作原生文字或图片区域外文字过滤的 OCR 行数。

默认 260 DPI，可用 `--dpi 150-400` 调整。若 `has_more: true`：`next_offset > 0` 时传 `--pages <next_page> --start-offset <next_offset>`；`next_offset = 0` 时把 `remaining_pages` 作为下一次 `--pages`。普通小徽标和面积不足页面约 1.5% 的图片不会进入自动候选，但仍可用 `--pages` 显式识别。

## 创建文档

调用 `scripts/create_document.py`（专利结构可加 `--preset patent`，见 `references/patent-writing.md`）：

```text
--output '/usr/local/src/word/result.docx' --spec '<JSON对象>'
```

内容较长时先把 JSON 写到任务临时目录，再传 `--spec-file`。目标是本次任务旧产物且确认可覆盖时才传 `--overwrite`。

文档说明顶层结构：

```json
{
  "properties": {
    "title": "2026 年度经营报告",
    "author": "示例公司",
    "subject": "经营分析"
  },
  "page": {
    "size": "A4",
    "orientation": "portrait",
    "margins": {
      "top": 0.85,
      "bottom": 0.85,
      "left": 0.9,
      "right": 0.9
    }
  },
  "default_font": {
    "name": "Arial",
    "east_asia": "Noto Sans CJK SC",
    "size": 10.5,
    "line_spacing": 1.15,
    "space_after": 6
  },
  "styles": {
    "Title": {"size": 24, "bold": true, "color": "1F4E78"},
    "Heading 1": {"size": 16, "bold": true, "color": "1F4E78"}
  },
  "header": {
    "text": "示例公司 · 年度报告",
    "alignment": "right"
  },
  "footer": {
    "text": "",
    "alignment": "center",
    "page_number": true,
    "page_number_prefix": "第 ",
    "page_number_suffix": " 页"
  },
  "blocks": [],
  "sections": []
}
```

支持的 `blocks[].type`：

| 类型 | 关键字段 |
| --- | --- |
| `paragraph` | `text` 或 `runs`；可选 `style/alignment/space_before/space_after/left_indent/right_indent/first_line_indent` |
| `heading` | `level`（1–9）、`text` 或 `runs` |
| `bullet_list` | `items[]`，可选 `level` |
| `numbered_list` | `items[]`，可选 `level` |
| `table` | `rows[][]`；可选 `column_widths/header_rows/style/header_fill/merges` |
| `image` | `path`；可选 `width_inches/height_inches/alignment/caption` |
| `toc` | 可选 `title`、`levels`，如 `1-3` |
| `horizontal_rule` | 可选 `color/size/style` |
| `page_break` | 无其他必填字段 |
| `section_break` | 可选 `break_type/page size/orientation/margins` |
| `spacer` | 可选 `points` |

带局部格式和链接的段落：

```json
{
  "type": "paragraph",
  "alignment": "justify",
  "runs": [
    {"text": "重要：", "bold": true, "color": "C00000"},
    {"text": "本报告数据截至 2026-06-30。"},
    {
      "text": "查看来源",
      "hyperlink": "https://example.com/source"
    }
  ]
}
```

表格示例：

```json
{
  "type": "table",
  "rows": [
    ["指标", "本期", "同比"],
    ["收入", "1,250 万元", "12.5%"],
    ["毛利率", "38.2%", "2.1 个百分点"]
  ],
  "column_widths": [2.2, 1.7, 1.7],
  "header_rows": 1,
  "header_fill": "1F4E78",
  "style": "Table Grid"
}
```

`runs[]` 支持 `bold/italic/underline/strike/color/font/east_asia_font/size/superscript/subscript/style/hyperlink`。不要用换行符模拟段落或分页；使用独立 `paragraph` 或 `page_break`。项目符号和编号必须使用列表块，不要手写 `•` 或数字前缀。

## 编辑文档

调用 `scripts/edit_document.py`：

```text
--input '/usr/local/src/word/tmp/<任务名>/source.docx' --output '/usr/local/src/word/edited.docx' --spec '<JSON对象>'
```

JSON 顶层只有 `operations`。支持：

| `operations[].type` | 关键字段 |
| --- | --- |
| `replace_text` | `find`、`replace`；可选 `scope/match_case/whole_word/count/required` |
| `append_blocks` | `blocks[]`，格式与创建脚本相同 |
| `insert_blocks_after` | `find`、`blocks[]`；可选 `match: exact|contains` |
| `remove_paragraphs` | `text`；可选 `match/count/required` |
| `set_paragraph_style` | `style`，以及 `indexes[]` 或 `contains` |
| `set_properties` | `properties` |
| `set_page` | `page`；`section` 为索引或 `all` |
| `set_header_footer` | 可选 `header`、`footer` |
| `remove_tables` | `indexes[]` |

查找替换会处理 Word 把可见短语拆成多个 `<w:r>` 的情况，并尽量保留首个匹配 Run 的格式。默认范围 `all` 包含正文、表格、页眉和页脚；可指定 `body/tables/headers/footers`。

仅当用户要求记录文本替换时传 `--track-changes --author <作者>`，此时所有操作必须是 `replace_text`。脚本生成真实的删除/插入修订，保留未改动 Run 的格式。含已有修订的输入、跨书签/批注范围、含域/图片等复杂节点或带换行的修订替换会明确拒绝；需要先缩小目标或按用户已明确的意图处理原修订。

输入含现有修订时默认停止：

- 用户希望干净副本：先用 `accept_changes.py`。
- 用户明确要求保留修订：才传 `--allow-existing-revisions`；修改后的内容本身不会自动变成新的修订。
- 新增、删除段落、调整格式等操作暂不支持自动记录修订。不要把这些操作作为“已显示修订”交付。

## 添加批注

调用 `scripts/add_comment.py`：

```text
--input '/usr/local/src/word/tmp/<任务名>/source.docx' --output '/usr/local/src/word/commented.docx' --find '费用上限' --comment '请确认该上限是否含税' --author '审阅人' --initials 'SR'
```

可选：

- `--scope <body|tables|headers|footers|all>`。
- `--occurrence <序号>`：为全文第几个匹配添加批注，默认 `1`。
- `--ignore-case`。

脚本会在必要时拆分 Run，让批注尽量精确锚定到目标文本，而不是整个段落。

## 接受修订

调用 `scripts/accept_changes.py`：

```text
--input '/usr/local/src/word/tmp/<任务名>/redlined.docx' --output '/usr/local/src/word/clean.docx'
```

脚本只执行固定的“接受全部修订”宏，不能运行用户提供的宏。必须检查：

- `revision_markers_before` 大于 `0` 时，`revision_markers_after` 必须为 `0`。
- `status` 必须为 `success`。
- 之后仍要执行结构校验和逐页渲染，特别检查删除段落、编号列表和空白段落。

## 转换

调用 `scripts/convert_document.py`：

```text
--input '/usr/local/src/word/tmp/<任务名>/legacy.doc' --output '/usr/local/src/word/tmp/task/source.docx'
```

支持：

- `.doc` / `.dotx` → `.docx`。
- `.docx` → `.pdf`，用于预览或用户明确要求的 PDF 副本。
- `.docx` → `.md` / `.txt`，默认按接受修订后的视图导出；可传 `--track-changes reject|all`。

不要把转换为 Markdown 的结果当作版式等价副本；表格宽度、浮动图片、页眉页脚、脚注和分页可能简化。

## 高级 OOXML 编辑

只有 `edit_document.py` 无法完成且确实需要编辑底层部件时：

1. 调用 `scripts/unpack_document.py --input <docx> --output-dir <空目录>`。
2. 用可用的文件编辑工具最小化修改 `word/document.xml` 或相关部件；不要重排、格式化或重写无关 XML。
3. 调用 `scripts/pack_document.py --input-dir <目录> --output <新docx>`。
4. 调用 `validate_document.py --check-convert` 和 `render_document.py`。

解包脚本拒绝路径穿越、符号链接和压缩炸弹；打包脚本拒绝缺少 `[Content_Types].xml`、`_rels/.rels` 或 `word/document.xml` 的目录。不要手动调用 `unzip`、`zip`、`find` 或删除命令。

## 校验

调用 `scripts/validate_document.py`：

```text
--input '/usr/local/src/word/result.docx' --check-convert
```

必须满足：

- `status: valid`。
- `issue_count: 0`。
- `archive.missing_required_parts` 和 `duplicate_members` 为空。
- 批注引用完整，内部关系目标存在，所有 XML 可安全解析。
- 修订元素有作者和时间；干净副本的 `tracked_changes.total` 应为 `0`。
- `render_check.success: true` 且页数大于 `0`。

该检查验证结构和可打开性，不替代人工视觉检查。

## 渲染与视觉检查

调用 `scripts/render_document.py`：

```text
--input '/usr/local/src/word/result.docx' --output-dir '/usr/local/src/word/tmp/task/rendered'
```

默认 150 DPI、单次最多 20 页。可传：

- `--start-page`、`--end-page`、`--max-pages`：分批渲染。
- `--dpi <72-300>`：小字、复杂表格或页眉脚注可提高到 180–220。
- `--include-pdf`：同时保留 `document.pdf`。
- `--overwrite`：只覆盖本次任务旧渲染。

若 `has_more: true`，用 `next_page` 继续。通过可用的图片查看工具逐页检查。

## 质量要求

- 默认采用 A4、合理页边距、清晰标题层级；现有文档的规范优先。
- 字体必须实际安装；检查方法和可用字体见 `references/fonts.md`。已有模板优先保留字体，缺失时明确报告；普通新文档可用 Noto/Fandol 与 Arial/Times New Roman。
- 标题必须使用内置 `Heading 1`–`Heading 9` 或具有大纲级别的样式，目录才能收录。
- 表格明确设置列宽、重复表头并禁止跨页拆分关键行；不要使用百分比列宽假设不同客户端一致。
- 表格底色使用明确填充色；不要用表格模拟水平线。
- 页码、目录和交叉引用使用字段，不手写空格或点号对齐。
- 图片保持纵横比，注明来源或说明文字，确认没有超出版心。
- 不用 `\n` 代替独立段落，不用空格填充对齐，不把分页符直接放在正文字符串中。
- 检查孤行孤字、标题落在页尾、表格断裂、图片拉伸、文字裁切、异常空白页、乱码、重叠和页码连续性。
- 最终文档必须内容准确、结构有效、可由 LibreOffice 打开，并通过全部页面视觉检查。

## 分节页眉、页脚和页码

创建说明可带 `sections`，每项使用已存在且不重复的 `index`（从 0 开始），可设置 `header`、`footer` 和 `page_number_start`。先用 `section_break` 块创建分节，再覆盖对应节。全局 `header/footer` 用作默认值，分节设置优先且解除与上一节的链接。

```json
{"sections": [{"index": 1, "header": {"text": "说明书", "alignment": "center"}, "page_number_start": 1}]}
```

页边距、列宽和缩进的单位是英寸，`2.5 cm = 2.5 / 2.54` 英寸。字号与段前段后间距使用 pt。`default_font.line_spacing` 是倍数；段落块的 `line_spacing` 是固定 pt，不能把 `1.5` 当成 1.5 倍行距传入段落块。
