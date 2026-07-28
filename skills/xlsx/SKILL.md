---
name: xlsx
description: "创建、读取、编辑、修复、转换、重算、校验和渲染本地或远程 HTTPS Excel 工作簿及表格数据，并下载 Excel 任务所需且不超过 25 MiB 的图片、音视频、压缩包和其他 HTTPS 附件。用户提到 Excel、电子表格、工作簿、工作表、单元格、公式、图表、数据清洗，或提供 HTTPS Excel/CSV/TSV 地址、.xlsx、.xlsm、.xltx、.xls、.csv、.tsv 文件时使用；支持源文件安全下载、保留现有样式、批量写入、公式与缓存值检查、表格/图表/图片/数据验证/条件格式、旧格式转换和逐页视觉检查。最终交付物必须是电子表格文件；若主要交付物是 Word、PDF、HTML、数据库程序或在线 Google Sheets，则不要使用。"
---

# Excel 工作簿处理

## 强制执行规则

当前智能体不能直接执行 shell、任意 Python 代码或系统命令。只能通过 `execute_skill_script` 调用本 Skill 中真实存在的固定 Python 脚本。

- 只调用下表列出的可执行脚本，不执行 `scripts/` 目录或内部模块 `scripts/_xlsx_common.py`。
- 不把 `python3`、`soffice`、`libreoffice`、`pdftoppm`、`zip`、`unzip`、`rm` 或其他系统命令作为脚本参数。
- 外部程序只允许由固定脚本在内部以无 shell 参数数组方式调用。
- 每次检查脚本返回的 JSON；只有 `ok` 为 `true` 时才继续。`status: errors_found` 虽然表示脚本成功运行，但工作簿不合格，必须修复。
- 远程 Excel/CSV/TSV 源文件只交给 `download_workbook.py`；任务所需的远程图片、视频、音频、压缩包或其他附件只交给 `download_attachment.py`。不要在回复、日志摘要或文件名中复述可能含敏感查询参数的完整 URL。
- 不覆盖用户提供的源文件。Excel 最终文件一律写入 `/usr/local/src/excel/`，下载缓存、中间文件和渲染结果一律写入 `/usr/local/src/excel/tmp/<任务名>/`。始终传绝对路径；固定脚本会自动创建目录并拒绝该根目录之外的输出。
- 环境已预置依赖，不安装软件包，也不提示用户安装依赖。

## 脚本清单

| 脚本 | 用途 | 底层能力 |
| --- | --- | --- |
| `scripts/download_workbook.py` | 下载并校验远程 HTTPS Excel/CSV/TSV | Python `urllib`、安全 OOXML 解析、`openpyxl` |
| `scripts/download_attachment.py` | 下载图片、音视频、压缩包等通用 HTTPS 附件 | Python `urllib`、HEAD 大小探测、流式硬限制 |
| `scripts/inspect_workbook.py` | 分段读取结构、公式、缓存值和样式 | `openpyxl`、Python `csv` |
| `scripts/apply_workbook.py` | 按受控 JSON 创建或编辑工作簿 | `openpyxl`、Pillow |
| `scripts/convert_workbook.py` | 转换 `.xls/.csv/.tsv/.xlsx/.xlsm/.xltx` | `openpyxl`、LibreOffice |
| `scripts/recalculate_workbook.py` | 重算公式并检查公式错误 | LibreOffice、`openpyxl` |
| `scripts/render_workbook.py` | 把工作簿渲染为逐页 PNG/PDF | LibreOffice、Poppler |

## 标准流程

1. 输入是 HTTPS 地址时，先调用 `download_workbook.py` 下载到本次任务临时目录；本地文件直接进入下一步。
2. 检查输入格式。旧版 `.xls` 先调用 `convert_workbook.py` 转为 `.xlsx`。
3. 编辑现有文件前先调用 `inspect_workbook.py`；读取公式和缓存值，确认工作表名称、输入区域、合并区域、表格、图表、隐藏工作表和外部链接。
4. 用 `apply_workbook.py` 创建或编辑新文件。只修改用户要求的单元格或结构，保留未涉及的公式和样式。
5. 只要结果中 `formula_count > 0` 或 `requires_recalculation: true`，必须调用 `recalculate_workbook.py`，并确保 `status: success`、`total_errors: 0`。
6. 先抽查 2–3 个关键公式的引用和计算逻辑，再调用 `inspect_workbook.py` 读取重算后的公式与缓存值；“没有公式错误”不等于“公式逻辑正确”。
7. 创建或修改后调用 `render_workbook.py`，检查全部页面或按游标分批检查，确认没有裁切、异常分页、乱码、重叠、空白页或不可读图表。
8. 只有结构检查、公式检查和视觉检查都通过后才交付最终工作簿。

## 下载远程工作簿

只接受 HTTPS 地址。完整保留 URL 及查询参数传给脚本，但不要在回复、日志摘要或输出文件名中复述敏感参数。

调用 `scripts/download_workbook.py`：

```text
--url 'https://example.com/report.xlsx?signature=...' --output '/usr/local/src/excel/tmp/<任务名>/source.xlsx'
```

可选参数：

- `--timeout <1-600>`：连接和读取超时秒数，默认 `60`。
- `--max-bytes <字节数>`：默认且最高 `26214400`（25 MiB），只允许设置更小的限制。
- `--overwrite`：只在目标是本次任务生成的旧缓存时使用。

`output` 扩展名必须是 `.xlsx`、`.xlsm`、`.xltx`、`.xltm`、`.xls`、`.csv` 或 `.tsv`。脚本阻止 HTTPS 重定向降级到 HTTP，流式限制大小，先写同目录临时文件，再原子发布；OOXML 会检查 ZIP 路径、成员大小、内容类型并用 `openpyxl` 打开，CSV/TSV 会拒绝二进制或网页响应。实际 OOXML 格式与 `output` 扩展名不一致时，根据错误中的实际格式更正缓存扩展名，再调用同一脚本。

成功结果包含 `path`、`size_bytes`、`format` 和 `validation`；OOXML 还包含 `sheet_count`。后续脚本只使用返回的本地 `path`，不再访问原 URL。

## 下载通用附件

需要下载作为 Excel 任务素材的图片、视频、音频、压缩包或其他文件时，调用 `scripts/download_attachment.py`：

```text
--url 'https://example.com/asset.bin?signature=...' --output '/usr/local/src/excel/tmp/<任务名>/asset.bin'
```

只接受 HTTPS 地址，`output` 可使用任意附件扩展名。可选参数只有 `--timeout <1-600>`（默认 `60`）和 `--overwrite`。附件上限固定为 25 MiB（26214400 字节），不可调高：脚本先用 HEAD 探测远端声明大小，再检查 GET 响应声明，并在流式接收时持续兜底计数；任一阶段发现超限都会返回 `ok: false` 和明确的“已拒绝下载”错误，且不会发布部分文件。

成功结果包含 `path`、实际 `size_bytes`、`declared_size_bytes`、`size_limit_bytes`、`size_probe` 和 `content_type`。本脚本不校验文件业务格式；远程 Excel/CSV/TSV 源文件仍使用 `download_workbook.py`。

## 检查工作簿

调用 `scripts/inspect_workbook.py`：

```text
--input 'source.xlsx'
```

可选参数：

- `--sheet <名称>`：选择工作表；默认活动工作表。
- `--start-row <行>`、`--start-column <列>`：读取起点，均从 `1` 开始。
- `--max-rows <1-200>`、`--max-columns <1-100>`：限制单次输出，默认 `40 × 20`。

结果同时给出公式字符串和缓存值：

- `formula`：原始公式。
- `cached_value`：Excel/LibreOffice 上次计算后保存的结果。
- `has_external_links`：为 `true` 时，编辑或重算可能破坏外部链接缓存；默认停止并向用户说明。
- `selection.has_more`、`next_row`、`next_column`：用于继续读取大表，不要一次返回整本工作簿。

CSV/TSV 只返回行数据，不存在工作表。`.xls` 必须先转换。

## 创建或编辑

调用 `scripts/apply_workbook.py`，新建时省略 `--input`，编辑时提供源文件：

```text
--output '/usr/local/src/excel/result.xlsx' --spec '<JSON对象>'
```

或：

```text
--input 'source.xlsx' --output '/usr/local/src/excel/result.xlsx' --spec-file '/usr/local/src/excel/tmp/task/operations.json'
```

目标已存在且确认是本次任务的旧产物时才传 `--overwrite`。输入含外部链接时脚本默认拒绝保存；只有用户明确接受缓存值可能丢失的风险时才传 `--allow-external-links`。`.xlsm` 必须继续输出 `.xlsm` 才能保留宏；只有用户明确同意丢弃宏时才输出 `.xlsx` 并传 `--drop-macros`。

操作说明顶层字段：

```json
{
  "properties": {
    "title": "销售分析",
    "creator": "示例公司"
  },
  "calculation_mode": "auto",
  "active_sheet": "汇总",
  "operations": []
}
```

支持的 `operations[].type`：

| 类型 | 关键字段 |
| --- | --- |
| `add_sheet` | `name`，可选 `index` |
| `remove_sheet` | `sheet` |
| `rename_sheet` | `sheet`、`name` |
| `set_cells` | `sheet`、`cells[]` |
| `write_rows` | `sheet`、`start_cell`、`rows[][]`，可选统一 `style` |
| `append_rows` | `sheet`、`rows[][]` |
| `style_range` | `sheet`、`range`、`style` |
| `clear_range` | `sheet`、`range`，可选 `values/styles/comments/hyperlinks` |
| `insert_rows` / `delete_rows` | `sheet`、`index`、`amount` |
| `insert_columns` / `delete_columns` | `sheet`、`index`、`amount` |
| `merge_cells` / `unmerge_cells` | `sheet`、`range` |
| `set_column_widths` | `sheet`、`widths`，如 `{"A": 18, "B:D": 12}` |
| `set_row_heights` | `sheet`、`heights`，如 `{"1": 28, "2:5": 20}` |
| `freeze_panes` | `sheet`、`cell`；传空值取消冻结 |
| `set_auto_filter` | `sheet`、`range`；传空值取消筛选 |
| `add_table` | `sheet`、`range`、`name`，可选 `style` |
| `add_chart` | `sheet`、`chart_type`、`data_range`、`anchor`；可选 `categories_range/title` |
| `add_image` | `sheet`、`path`、`anchor`；可选像素 `width/height` |
| `add_data_validation` | `sheet`、`range`、`validation_type`、`formula1` |
| `add_conditional_format` | `sheet`、`range`、`rule_type` 及对应规则参数 |
| `set_print` | `sheet`，可选 `print_area/orientation/paper_size/fit_to_width/margins` |
| `set_named_range` | `sheet`、`name`、`range` |

`set_cells.cells[]` 中每项使用：

```json
{
  "cell": "B2",
  "formula": "=SUM(B3:B10)",
  "style": {
    "font": {"name": "Arial", "size": 11, "bold": true, "color": "FFFFFF"},
    "fill": {"color": "1F4E78"},
    "alignment": {"horizontal": "center", "vertical": "center", "wrap_text": true},
    "number_format": "#,##0.00",
    "border": {
      "bottom": {"style": "thin", "color": "808080"}
    },
    "protection": {"locked": true}
  },
  "comment": {"author": "AI", "text": "来源：用户提供的 2026 年预算"},
  "hyperlink": "https://example.com/source"
}
```

同一单元格不能同时传 `value` 和 `formula`。`formula` 必须以 `=` 开头。`write_rows.rows[][]` 可直接传值，也可在某个位置传带 `value/formula/style/comment/hyperlink` 的对象。

## 转换文件

调用 `scripts/convert_workbook.py`：

```text
--input 'legacy.xls' --output '/usr/local/src/excel/tmp/task/source.xlsx'
```

常见用法：

- CSV/TSV → XLSX：可传 `--sheet-name <名称>`；默认所有字段按文本保留，确认可以推断数字/布尔值时才传 `--infer-types`。
- XLSX/XLSM → CSV/TSV：可传 `--sheet <名称>`；默认导出缓存结果，明确需要公式字符串时传 `--formulas`。
- Excel → PDF：输出路径使用 `.pdf`；该 PDF 仅用于预览或用户明确要求的转换，不替代工作簿交付。
- 中文旧系统文本可传 `--encoding gb18030`；默认 `utf-8-sig`。

## 公式重算

含公式的工作簿必须调用 `scripts/recalculate_workbook.py`：

```text
--input '/usr/local/src/excel/result.xlsx' --output '/usr/local/src/excel/result-recalculated.xlsx'
```

检查返回值：

- `status: success` 且 `total_errors: 0`：公式可被 LibreOffice 计算。
- `status: errors_found`：根据 `error_summary` 中的工作表、单元格和公式修复，再重算。
- `missing_cached_value_count > 0`：可能是公式结果为空字符串，也可能未正确计算；逐个抽查。

优先使用 Excel 2007 时代即可稳定重算的函数，如 `SUMIFS`、`INDEX`、`MATCH`、`IFERROR`、`SUMPRODUCT`。避免 `XLOOKUP`、`XMATCH`、`SORT`、`FILTER`、`UNIQUE`、`SEQUENCE` 等动态数组或新函数；脚本会提示但不能证明其结果完整。

## 渲染与视觉检查

调用 `scripts/render_workbook.py`：

```text
--input '/usr/local/src/excel/result-recalculated.xlsx' --output-dir '/usr/local/src/excel/tmp/task/rendered'
```

默认 150 DPI、单次最多 20 页。可传：

- `--start-page`、`--end-page`、`--max-pages`：分批渲染。
- `--dpi <72-300>`：小字或复杂图表可提高到 180–220。
- `--include-pdf`：同时保留 `workbook.pdf`。
- `--overwrite`：只覆盖本次任务旧渲染。

若 `has_more: true`，用 `next_page` 继续。通过可用的图片查看工具逐页检查返回的 PNG。

## 质量要求

- 默认使用专业字体：中文使用 `Noto Sans CJK SC` 或与原文件一致的字体，拉丁文字使用 Arial；编辑现有文件时原有规范优先。
- 表头、单位、日期、货币、百分比和负数格式必须明确；百分比按小数存储，例如 `0.15` 显示为 `15.0%`。
- 可计算结果使用公式，不把当前结果硬编码进单元格；假设值单独放在有标签的输入单元格中。
- 每个外部数据、假设和硬编码数字都用批注或邻近单元格说明来源。
- 新建供他人填写的模板要包含填写说明和一行格式示例；编辑现有文件时不要擅自插入示例行。
- 精确遵循用户指定的工作表名、表头、公式和输出格式，不擅自重构业务逻辑。
- 合并单元格只写左上角锚点；编辑 `.xlsm` 时保留宏；不要用 `data_only=True` 读取后再保存。
- 公式重算、关键值抽查和全部页面视觉检查全部通过后再交付。
