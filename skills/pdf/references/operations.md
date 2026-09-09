# 原有固定操作接口

## 下载远程 PDF

只接受 HTTPS 地址。完整保留 URL 及查询参数，不在回复、日志摘要或文件名中复述敏感参数。

调用 `scripts/download_pdf.py`：

```text
--url 'https://example.com/document.pdf' --output '/usr/local/src/pdf/tmp/<任务名>/source.pdf'
```

可选参数：

- `--timeout <秒>`：默认 `60`。
- `--max-bytes <字节数>`：默认且最高 `26214400`（25 MiB），只允许设置更小的限制。
- `--overwrite`：仅在目标是本次任务生成的缓存时使用。

脚本会创建父目录、流式下载、阻止 HTTPS 重定向降级到 HTTP，并验证 PDF。成功结果包含 `path`、`size_bytes`、`page_count` 和 `encrypted`。

## 下载通用附件

需要下载作为 PDF 任务素材的图片、视频、音频、压缩包或其他文件时，调用 `scripts/download_attachment.py`：

```text
--url 'https://example.com/asset.bin?signature=...' --output '/usr/local/src/pdf/tmp/<任务名>/asset.bin'
```

只接受 HTTPS 地址，`output` 可使用任意附件扩展名。可选参数只有 `--timeout <1-600>`（默认 `60`）和 `--overwrite`。附件上限固定为 25 MiB（26214400 字节），不可调高：脚本先用 HEAD 探测远端声明大小，再检查 GET 响应声明，并在流式接收时持续兜底计数；任一阶段发现超限都会返回 `ok: false` 和明确的“已拒绝下载”错误，且不会发布部分文件。

成功结果包含 `path`、实际 `size_bytes`、`declared_size_bytes`、`size_limit_bytes`、`size_probe` 和 `content_type`。本脚本不校验文件业务格式；远程 PDF 源文件仍使用 `download_pdf.py`。

## 检查 PDF

调用 `scripts/inspect_pdf.py`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf'
```

使用返回的 `page_count`、`encrypted`、`metadata`、`page_layouts` 和 `form_field_count` 判断后续处理方式。不要直接调用 `pdfinfo`。

## 提取正文

首次调用 `scripts/extract_text.py`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf'
```

默认使用 `auto` 引擎：先由 Poppler `pdftotext` 提取；结果不可用或命令不可用时自动尝试 `pdfplumber`，并可逐页选择质量更好的结果。脚本使用 `pypdf` 获取标准页数，并拒绝把页数不一致的提取结果当作成功。不要直接执行 `pdftotext`。

默认单次最多处理 8 页、返回 24000 个字符。可使用：

- `--start-page <页码>`、`--end-page <页码>`：页码从 `1` 开始。
- `--start-offset <字符偏移>`：继续读取被字符上限截断的同一页；大于 `0` 时同时传入上次返回的 `next_engine`。
- `--max-pages <页数>`、`--max-chars <字符数>`：控制单次输出。
- `--layout`：仅在需要尽量保留版面空格时使用。
- `--engine <auto|poppler|pdfplumber>`：首次及跨页提取保持 `auto`；同页字符续读时传入上次返回的 `next_engine`。
- `--timeout <秒>`：Poppler 提取超时，默认 `120`。

先检查 `usable_for_summary` 和 `text_quality.status`：

- `usable_for_summary: true`：只使用 `pages[]` 中同样标为 `usable_for_summary: true` 的 `text`；可疑页的文本会被置空。如果 `has_more: true`，始终传回 `next_page` 和 `next_offset`。仅当 `next_offset` 大于 `0` 时，把非空的 `next_engine` 传给 `--engine` 以固定同页字符游标；这种调用只续读当前页。当前页完成后返回的 `next_offset` 为 `0`，此时不要传 `--engine`，让下一页重新使用 `auto`。保留首次调用的 `--end-page`（如果指定）及其他选项，直至 `has_more: false`。
- `usable_for_summary: false`：本批次没有可靠文本，不要使用返回内容。查看 `engine_attempts`、`text_quality.reasons`、`text_quality.suspect_pages` 和 `needs_ocr`；若 `has_more: true`，仍按跨页游标继续检查后续批次，避免漏掉后续可搜索文本。
- `needs_ocr: true`：一个或多个页面未得到可靠文本。把 `text_quality.suspect_pages` 中实际需要阅读的页码传给 `ocr_text.py`；先调用 OCR；只有 OCR 标记疑难、结果与页面结构冲突或需要理解非文本图形时，才渲染相关页供模型辅助复核。

`complete_text_coverage: true` 表示本批次所有页面均有可靠文本。`text_quality` 按页检测空白或过少文本、页面实际可见图像覆盖过大但文字不足、`(cid:...)`、Unicode 替换字符、异常控制字符及外观像汉字的部首字符；`pages[].extractor` 表示该页最终采用的引擎。`status: mixed` 表示同一批次同时包含可靠页和可疑页：可先使用可靠页文本，同时只核验 `suspect_pages`。不要只根据“肉眼看起来能读”判定提取结果可靠。

## OCR 与疑难复核

详见 [读取与 OCR](reading-and-ocr.md)，使用固定 `ocr_text.py` 或 `ocr_image.py`。

## 提取表格

调用 `scripts/extract_tables.py`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf' --start-page 1
```

默认单次最多处理 5 页、20 个表格和 2000 个单元格。可用 `--end-page`、`--start-table`、`--max-pages`、`--max-tables`、`--max-cells` 调整。若 `has_more: true`，把 `next_page` 传给 `--start-page`、`next_table` 传给 `--start-table` 后继续，并保留首次调用的 `--end-page`（如果指定）及其他提取选项。

## 渲染页面

只有满足以下任一条件时才调用 `scripts/render_pdf.py`：

- 用户明确要求审阅版式、图表、印章、公式或页面外观；
- 创建或修改 PDF 后进行最终视觉检查；
- OCR 标记低置信度、读取顺序冲突或无法识别区域，需要大模型辅助复核。

普通文本总结和扫描文字读取不自动增加整本视觉识别；OCR 的临时渲染由 `ocr_text.py` 内部完成。调用脚本时不要直接执行 `pdftoppm`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf' --output-dir '/usr/local/src/pdf/tmp/<任务名>/rendered' --start-page 1
```

默认 150 DPI、单次最多 10 页。可使用 `--end-page`、`--max-pages`、`--dpi`、`--timeout` 和 `--overwrite`。若 `has_more: true`，使用 `next_page` 继续，并保留首次调用的 `--end-page`（如果指定）、输出目录及其他渲染选项。脚本返回标准化的 `page-0001.png` 文件路径。

对文字较小或图表密集的页面提高 DPI。使用可用的图像查看工具检查返回的 PNG，不要尝试把图片路径交给下载脚本。

## 简单文本创建 PDF

先使用 `write_file` 把内容写为 UTF-8 `.txt` 或 `.md` 文件，再调用 `scripts/create_pdf.py`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/content.md' --output '/usr/local/src/pdf/<文件名>.pdf' --title '文档标题'
```

脚本支持 Markdown 标题、项目符号和简单表格，自动选择可嵌入的 Unicode 字体并添加页码。可选参数：

- `--page-size <A4|LETTER>`
- `--font-path <TTF或TTC路径>`
- `--font-size <字号>`
- `--margin <points>`
- `--overwrite`

输入内容只使用 ASCII 连字符 `-`；脚本也会把常见 Unicode 横线规范化为 ASCII 连字符。

## 合并、拆分与旋转

调用 `scripts/manage_pdf.py`，第一个参数必须是操作名。

合并：

```text
merge --input 'a.pdf' --input 'b.pdf' --output '/usr/local/src/pdf/merged.pdf'
```

拆分指定范围：

```text
split --input 'source.pdf' --output-dir '/usr/local/src/pdf/split' --range 1-3 --range 4-6
```

不传 `--range` 时每页生成一个 PDF。

旋转指定页面：

```text
rotate --input 'source.pdf' --output '/usr/local/src/pdf/rotated.pdf' --pages '1,3-5' --degrees 90
```

`--degrees` 只能是 `90`、`180` 或 `270`；不传 `--pages` 时旋转全部页面。目标已存在且确认可覆盖时添加 `--overwrite`。

## 清理临时目录

调用 `scripts/cleanup_pdf_temp.py`：

```text
--task-dir '/usr/local/src/pdf/tmp/<任务名>'
```

脚本只允许删除 `/usr/local/src/pdf/tmp/` 下一级任务目录，拒绝删除根目录、仓库目录或其他路径。

## 质量要求

- 不覆盖用户提供的源文件。
- 创建或修改后重新检查页数、页面尺寸、加密状态和文本可读性。
- 扫描件先做原生文字检测，再只 OCR 可疑页；不得把低置信度 OCR 文本当作可靠正文。
- 逐页确认没有裁切、重叠、溢出、乱码、黑方块、错误分页或异常空白页。
- 检查标题层级、段落间距、页边距、表格、图表、图片、页码及章节衔接。
- 引用和参考文献必须可读，不得残留工具令牌、占位符或临时路径。
- 只有最新渲染结果不存在可见缺陷时才交付创建或修改后的 PDF。
