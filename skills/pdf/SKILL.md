---
name: pdf
description: "处理本地 PDF 文件或远程 HTTPS PDF 链接，包括安全下载、元数据与页面检查、分段文本和表格提取、页面 PNG 渲染、从文本创建 PDF、合并、拆分、旋转及最终质量校验。当用户提供 .pdf 文件或 HTTPS PDF 地址，或要求总结、读取、生成、编辑、转换或审阅 PDF 时使用。"
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

## 脚本清单

| 脚本 | 用途 | 底层能力 |
| --- | --- | --- |
| `scripts/download_pdf.py` | 下载并校验远程 HTTPS PDF | `urllib`、`pypdf` |
| `scripts/inspect_pdf.py` | 检查页数、加密、元数据、页面尺寸和表单数量 | `pypdf` |
| `scripts/extract_text.py` | 按页、按字符分段提取正文 | `pdfplumber` |
| `scripts/extract_tables.py` | 按页提取表格 | `pdfplumber` |
| `scripts/render_pdf.py` | 把指定页面渲染为 PNG | Poppler `pdftoppm` |
| `scripts/create_pdf.py` | 从 UTF-8 文本或 Markdown 创建 PDF | `reportlab`、`pypdf` |
| `scripts/manage_pdf.py` | 合并、拆分或旋转 PDF | `pypdf` |
| `scripts/cleanup_pdf_temp.py` | 安全删除本次任务临时目录 | Python 文件 API |

环境已预置所有依赖。不要安装依赖，也不要提示用户安装依赖。

## 标准流程

1. 为任务选择简短目录名，把中间文件放在 `tmp/pdfs/<任务名>/`。
2. 远程 HTTPS 链接先调用 `download_pdf.py`；本地文件直接进入下一步。
3. 调用 `inspect_pdf.py` 检查文件。遇到加密 PDF 且任务需要正文时，向用户索取密码，不要绕过加密。
4. 阅读或总结时，循环调用 `extract_text.py` 直至 `has_more` 为 `false`；需要表格时再调用 `extract_tables.py`。
5. 调用 `render_pdf.py` 检查首页、复杂页面、无文本页面和用户关心的页面。创建或修改 PDF 时分批渲染全部页面。
6. 使用提取文本和渲染结果完成回答；明确区分原文内容与基于版式或图表的推断。
7. 创建或修改后的最终 PDF 写入 `output/pdf/`，重新执行检查、文本提取和全部页面渲染。
8. 最终产物位于临时目录之外且不再需要缓存时，调用 `cleanup_pdf_temp.py` 清理本次任务目录。

## 下载远程 PDF

只接受 HTTPS 地址。完整保留 URL 及查询参数，不在回复、日志摘要或文件名中复述敏感参数。

调用 `scripts/download_pdf.py`：

```text
--url 'https://example.com/document.pdf' --output 'tmp/pdfs/<任务名>/source.pdf'
```

可选参数：

- `--timeout <秒>`：默认 `60`。
- `--max-bytes <字节数>`：默认 `104857600`（100 MiB）。
- `--overwrite`：仅在目标是本次任务生成的缓存时使用。

脚本会创建父目录、流式下载、阻止 HTTPS 重定向降级到 HTTP，并验证 PDF。成功结果包含 `path`、`size_bytes`、`page_count` 和 `encrypted`。

## 检查 PDF

调用 `scripts/inspect_pdf.py`：

```text
--input 'tmp/pdfs/<任务名>/source.pdf'
```

使用返回的 `page_count`、`encrypted`、`metadata`、`page_layouts` 和 `form_field_count` 判断后续处理方式。不要直接调用 `pdfinfo`。

## 提取正文

首次调用 `scripts/extract_text.py`：

```text
--input 'tmp/pdfs/<任务名>/source.pdf'
```

默认单次最多处理 8 页、返回 24000 个字符。可使用：

- `--start-page <页码>`、`--end-page <页码>`：页码从 `1` 开始。
- `--start-offset <字符偏移>`：继续读取被字符上限截断的同一页。
- `--max-pages <页数>`、`--max-chars <字符数>`：控制单次输出。
- `--layout`：仅在需要尽量保留版面空格时使用。

如果结果中 `has_more: true`，继续调用同一脚本，并把返回的 `next_page` 传给 `--start-page`、`next_offset` 传给 `--start-offset`。后续调用必须保留首次调用的 `--end-page`（如果指定）及其他提取选项。重复直到 `has_more: false`。不要只读取第一批文本就总结长文档。

提取文本为空或很少时，将页面视为可能的扫描件，改用渲染图读取；不要误判为空白 PDF。

## 提取表格

调用 `scripts/extract_tables.py`：

```text
--input 'tmp/pdfs/<任务名>/source.pdf' --start-page 1
```

默认单次最多处理 5 页、20 个表格和 2000 个单元格。可用 `--end-page`、`--start-table`、`--max-pages`、`--max-tables`、`--max-cells` 调整。若 `has_more: true`，把 `next_page` 传给 `--start-page`、`next_table` 传给 `--start-table` 后继续，并保留首次调用的 `--end-page`（如果指定）及其他提取选项。

## 渲染页面

调用 `scripts/render_pdf.py`，不要直接执行 `pdftoppm`：

```text
--input 'tmp/pdfs/<任务名>/source.pdf' --output-dir 'tmp/pdfs/<任务名>/rendered' --start-page 1
```

默认 150 DPI、单次最多 10 页。可使用 `--end-page`、`--max-pages`、`--dpi`、`--timeout` 和 `--overwrite`。若 `has_more: true`，使用 `next_page` 继续，并保留首次调用的 `--end-page`（如果指定）、输出目录及其他渲染选项。脚本返回标准化的 `page-0001.png` 文件路径。

对文字较小或图表密集的页面提高 DPI。使用可用的图像查看或识别工具检查返回的 PNG，不要尝试把图片路径交给下载脚本。

## 创建 PDF

先使用 `write_file` 把内容写为 UTF-8 `.txt` 或 `.md` 文件，再调用 `scripts/create_pdf.py`：

```text
--input 'tmp/pdfs/<任务名>/content.md' --output 'output/pdf/<文件名>.pdf' --title '文档标题'
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
merge --input 'a.pdf' --input 'b.pdf' --output 'output/pdf/merged.pdf'
```

拆分指定范围：

```text
split --input 'source.pdf' --output-dir 'output/pdf/split' --range 1-3 --range 4-6
```

不传 `--range` 时每页生成一个 PDF。

旋转指定页面：

```text
rotate --input 'source.pdf' --output 'output/pdf/rotated.pdf' --pages '1,3-5' --degrees 90
```

`--degrees` 只能是 `90`、`180` 或 `270`；不传 `--pages` 时旋转全部页面。目标已存在且确认可覆盖时添加 `--overwrite`。

## 清理临时目录

调用 `scripts/cleanup_pdf_temp.py`：

```text
--task-dir 'tmp/pdfs/<任务名>'
```

脚本只允许删除本 Skill 的 `tmp/pdfs/` 下一级任务目录，拒绝删除根目录、仓库目录或其他路径。

## 质量要求

- 不覆盖用户提供的源文件。
- 创建或修改后重新检查页数、页面尺寸、加密状态和文本可读性。
- 逐页确认没有裁切、重叠、溢出、乱码、黑方块、错误分页或异常空白页。
- 检查标题层级、段落间距、页边距、表格、图表、图片、页码及章节衔接。
- 引用和参考文献必须可读，不得残留工具令牌、占位符或临时路径。
- 只有最新渲染结果不存在可见缺陷时才交付创建或修改后的 PDF。
