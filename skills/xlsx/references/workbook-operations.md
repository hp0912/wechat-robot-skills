# 工作簿操作接口

以下路径相对 xlsx skill 根目录，通过 execute_skill_script 调用。

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
| `auto_fit` | `sheet`、`range`，可选 `min_width/max_width`（默认 8/40）；调整列宽、换行和行高，保留完整文本 |
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

同一单元格不能同时传 `value` 和 `formula`。`value` 中的字符串始终按文本写入，即使以 `=` 开头；公式使用明确的 `formula` 字段。`formula` 必须以 `=` 开头。`write_rows.rows[][]` 可直接传值，也可在某个位置传带 `value/formula/style/comment/hyperlink` 的对象。

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
