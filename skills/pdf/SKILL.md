---
name: pdf
description: "处理本地 PDF 文件或远程 HTTPS PDF 链接，包括安全下载、元数据与页面检查、多引擎分段文本提取和质量检测、扫描页本地 OCR、表格提取、按需页面 PNG 渲染、从文本创建 PDF、合并、拆分、旋转及最终质量校验。当用户提供 .pdf 文件或 HTTPS PDF 地址，或要求总结、读取、识别扫描件、生成、编辑、转换或审阅 PDF 时使用。"
---

# PDF 处理

## 强制执行规则

当前智能体不能执行 shell、任意 Python 代码或系统命令。只能通过 `execute_skill_script` 调用本 Skill 中真实存在的固定脚本。

- 只调用下表列出的可执行脚本。
- 不执行 `scripts/` 目录，不执行内部模块 `scripts/_pdf_common.py`。
- 不传 `-c`、`python3`、`ls`、`pdftoppm` 或其他 shell/系统命令作为脚本参数。
- 不创建或猜测脚本清单以外的文件。
- 每次检查脚本返回的 JSON；只有 `ok` 为 `true` 时才继续。
- 收到 `ok: false` 时，依据 `error` 调整合法参数或向用户说明失败原因，不要把参数改传给其他脚本碰运气。
- 阅读或总结时只使用 `pages[]` 中 `usable_for_summary: true` 的文本。`needs_ocr: false` 时不得为了“常规检查”继续 OCR、渲染或调用图片识别。
- PDF 最终文件一律写入 `/usr/local/src/pdf/`，下载缓存、中间文件和渲染结果一律写入 `/usr/local/src/pdf/tmp/<任务名>/`。始终传绝对路径；固定脚本会自动创建目录并拒绝该根目录之外的输出。
- 不把 PDF 密码作为脚本参数；工具调用参数可能进入运行日志。

## 脚本清单

| 脚本 | 用途 | 底层能力 |
| --- | --- | --- |
| `scripts/download_pdf.py` | 下载并校验远程 HTTPS PDF | `urllib`、`pypdf` |
| `scripts/inspect_pdf.py` | 检查页数、加密、元数据、页面尺寸和表单数量 | `pypdf` |
| `scripts/extract_text.py` | 多引擎提取、质量检测并分段返回正文 | Poppler `pdftotext`、`pdfplumber`；`pypdf` 校验 |
| `scripts/ocr_text.py` | 对指定扫描页执行离线 OCR 并返回可靠文字 | RapidOCR、ONNX Runtime、Poppler `pdftoppm` |
| `scripts/extract_tables.py` | 按页提取表格 | `pdfplumber` |
| `scripts/render_pdf.py` | 把指定页面渲染为 PNG | Poppler `pdftoppm` |
| `scripts/create_pdf.py` | 从 UTF-8 文本或 Markdown 创建 PDF | `reportlab`、`pypdf` |
| `scripts/manage_pdf.py` | 合并、拆分或旋转 PDF | `pypdf` |
| `scripts/cleanup_pdf_temp.py` | 安全删除本次任务临时目录 | Python 文件 API |

环境已预置所有依赖。不要安装依赖，也不要提示用户安装依赖。

## 标准流程

1. 为任务选择简短目录名，把中间文件放在 `/usr/local/src/pdf/tmp/<任务名>/`。
2. 远程 HTTPS 链接先调用 `download_pdf.py`；本地文件直接进入下一步。
3. 调用 `inspect_pdf.py` 检查文件。遇到加密 PDF 时停止处理，请用户提供已解密副本；当前固定脚本不接收密码。
4. 阅读或总结时调用 `extract_text.py`。结果为 `usable_for_summary: true` 时使用可靠页文本并根据游标继续；同时为 `needs_ocr: false` 时直接回答，不调用 OCR、渲染或图片识别。
5. 只有 `extract_text.py` 返回 `needs_ocr: true` 时，才对 `text_quality.suspect_pages` 调用 `ocr_text.py`。原生可靠文本优先，OCR 只补齐可疑页，不重复识别正常页。
6. `ocr_text.py` 会在脚本内部临时渲染指定页面并交给本地 RapidOCR，完成后自动删除 PNG；普通扫描件解析不调用大模型识图，也不需要先调用 `render_pdf.py`。
7. 仅在用户明确要求检查视觉版式，或任务涉及创建/修改 PDF 时调用 `render_pdf.py`。
8. 创建或修改后的最终 PDF 写入 `/usr/local/src/pdf/`，重新执行检查、文本提取和全部页面渲染。
9. 最终产物位于临时目录之外且不再需要缓存时，调用 `cleanup_pdf_temp.py` 清理本次任务目录。

## 下载远程 PDF

只接受 HTTPS 地址。完整保留 URL 及查询参数，不在回复、日志摘要或文件名中复述敏感参数。

调用 `scripts/download_pdf.py`：

```text
--url 'https://example.com/document.pdf' --output '/usr/local/src/pdf/tmp/<任务名>/source.pdf'
```

可选参数：

- `--timeout <秒>`：默认 `60`。
- `--max-bytes <字节数>`：默认 `104857600`（100 MiB）。
- `--overwrite`：仅在目标是本次任务生成的缓存时使用。

脚本会创建父目录、流式下载、阻止 HTTPS 重定向降级到 HTTP，并验证 PDF。成功结果包含 `path`、`size_bytes`、`page_count` 和 `encrypted`。

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
- `needs_ocr: true`：一个或多个页面未得到可靠文本。把 `text_quality.suspect_pages` 中实际需要阅读的页码传给 `ocr_text.py`；不要先调用 `render_pdf.py`，也不要把临时图片交给大模型。

`complete_text_coverage: true` 表示本批次所有页面均有可靠文本。`text_quality` 按页检测空白或过少文本、页面实际可见图像覆盖过大但文字不足、`(cid:...)`、Unicode 替换字符、异常控制字符及外观像汉字的部首字符；`pages[].extractor` 表示该页最终采用的引擎。`status: mixed` 表示同一批次同时包含可靠页和可疑页：可先使用可靠页文本，同时只核验 `suspect_pages`。不要只根据“肉眼看起来能读”判定提取结果可靠。

## 本地 OCR 扫描页

仅当 `extract_text.py` 返回 `needs_ocr: true` 时调用 `scripts/ocr_text.py`。`--pages` 必须明确指定 `text_quality.suspect_pages` 中要读取的页，单次最多 4 页：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf' --pages '2,5-6'
```

默认以 260 DPI 临时渲染，并使用镜像中预置的 RapidOCR 与 ONNX Runtime 在本地识别。脚本不会联网下载模型，不会保留渲染图片，也不会调用大模型视觉能力。可选参数：

- `--dpi <150-400>`：文字过小或识别质量不足时适度提高，默认 `260`。
- `--max-chars <字符数>`：默认 `24000`，最大 `60000`。
- `--timeout <秒>`：每页 Poppler 渲染超时，默认 `180`。
- `--start-offset <字符偏移>`：续读被字符上限截断的单页；使用时 `--pages` 只能包含该页。

只使用 `pages[]` 中 `usable_for_summary: true` 的 `text`。`status: empty`、`sparse` 或 `low_confidence` 的页面文本会被置空，并通过 `needs_review: true` 提醒人工检查。

如果 `has_more: true`：

- `next_offset > 0`：用 `--pages <next_page> --start-offset <next_offset>` 续读同一页。
- `next_offset = 0`：用返回的 `remaining_pages` 继续下一批。
- 同页续读完成后，再处理先前返回的其他 `remaining_pages`。

OCR 结果中的 `mean_confidence`、`line_count`、`render_seconds` 和 `ocr_seconds` 仅用于判断质量与性能。原生提取成功的页面始终采用 `extract_text.py` 结果，不用 OCR 覆盖。

## 提取表格

调用 `scripts/extract_tables.py`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf' --start-page 1
```

默认单次最多处理 5 页、20 个表格和 2000 个单元格。可用 `--end-page`、`--start-table`、`--max-pages`、`--max-tables`、`--max-cells` 调整。若 `has_more: true`，把 `next_page` 传给 `--start-page`、`next_table` 传给 `--start-table` 后继续，并保留首次调用的 `--end-page`（如果指定）及其他提取选项。

## 渲染页面

只有满足以下任一条件时才调用 `scripts/render_pdf.py`：

- 用户明确要求审阅版式、图表、印章、公式或页面外观；
- 创建或修改 PDF 后进行最终视觉检查。

不要因为输入是 PDF、需要总结、需要 OCR 或需要检查首页就自动调用本脚本；OCR 的临时渲染由 `ocr_text.py` 内部完成。调用脚本时不要直接执行 `pdftoppm`：

```text
--input '/usr/local/src/pdf/tmp/<任务名>/source.pdf' --output-dir '/usr/local/src/pdf/tmp/<任务名>/rendered' --start-page 1
```

默认 150 DPI、单次最多 10 页。可使用 `--end-page`、`--max-pages`、`--dpi`、`--timeout` 和 `--overwrite`。若 `has_more: true`，使用 `next_page` 继续，并保留首次调用的 `--end-page`（如果指定）、输出目录及其他渲染选项。脚本返回标准化的 `page-0001.png` 文件路径。

对文字较小或图表密集的页面提高 DPI。使用可用的图像查看工具检查返回的 PNG，不要尝试把图片路径交给下载脚本。

## 创建 PDF

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
