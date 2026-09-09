---
name: xlsx
description: "创建、读取、编辑、转换、重算和渲染 Excel/CSV/TSV，进行数据清洗、分组与交叉汇总、业务洞察、文本归类与翻译、综合评价、预测、回归分类、聚类异常检测和资源优化。用户提供电子表格文件或 HTTPS 地址，要求处理表格数据、分析工作簿、制作报表或模板时使用；也支持将口述数据整理为 Excel。独立 Word/PDF/HTML 制作、在线 Google Sheets 和与电子表格无关的通用编程使用对应工具。"
---

# Excel 工作簿与数据分析

## 运行约定

- 当前机器人通过 `execute_skill_script` 调用本 skill 的固定 Python 脚本。所有脚本与资源路径相对 xlsx 根目录；不执行内部模块，不把 shell、任意 Python 代码、系统命令当作脚本或参数。
- 工具链以 xlsx 为主：`openpyxl` 读写和图表，LibreOffice 重算与转换，Poppler 渲染；pandas 负责结构化处理。仅模型求解用 SciPy / scikit-learn。依赖在基础镜像中构建安装，任务运行时不安装；依赖缺失时明确说明镜像需更新，不能假装已完成计算。见 [依赖与能力边界](references/dependencies.md)。
- 源文件保留。最终工作簿写入 `/usr/local/src/excel/`，下载缓存、分析 JSON、预览和中间文件放在 `/usr/local/src/excel/tmp/<任务名>/`。传绝对路径，输出根目录由脚本校验。
- 每次检查返回的 JSON，`ok: false` 时先处理原因；`status: errors_found` 不能作为通过。`--overwrite` 只用于本次任务生成的旧产物。
- 用户格式与处理范围优先，其次源模板。只修改完成任务必需的区域；不因排版删除内容或截断原文，不擅自重命名、删除源工作表。
- 用户只要解释或统计结论时，可直接回复；需要文件时交付可编辑工作簿。额外报告、HTML 或压缩包按实际要求生成，不固定增加产物数量。发送文件仅使用当前环境实际可用且已获授权的工具。

## 按任务读取指南

| 任务 | 指南 |
| --- | --- |
| 下载、检查、创建、模板回填、格式、公式、图表、转换 | [工作簿操作接口](references/workbook-operations.md) |
| 字段理解、数据质量、清洗、连接、去重、业务洞察、交叉汇总 | [数据分析](references/data-analysis.md) |
| 提炼文本、标签、标准化、规则分类、逐格翻译 | [文本处理](references/text-analysis.md) |
| 综合评价、时序预测、回归分类、聚类、异常、资源优化 | [模型接口与方法选择](references/modeling.md) |
| 图表选择、分析结论、报告与财务格式 | [展示与交付](references/reporting.md) |

## 固定脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/download_workbook.py` | 下载并校验不超过 25 MiB 的 HTTPS Excel/CSV/TSV |
| `scripts/download_attachment.py` | 下载本任务所需且不超过 25 MiB 的 HTTPS 附件 |
| `scripts/inspect_workbook.py` | 分段检查结构、公式、缓存、样式、合并区域和外部链接 |
| `scripts/apply_workbook.py` | 受控 JSON 创建/编辑、表格、图表、条件格式、自适应列宽行高 |
| `scripts/convert_workbook.py` | 旧格式、CSV/TSV、工作簿与预览 PDF 转换 |
| `scripts/recalculate_workbook.py` | 重算公式，检查缓存错误 |
| `scripts/render_workbook.py` | 输出逐页 PNG 与可选 PDF |
| `scripts/analyze_workbook.py` | 数据概况、处理、分组、交叉汇总、相关分析和规则分类，生成写入操作 JSON |
| `scripts/model_workbook.py` | 受控建模与求解，生成写入操作 JSON |

`_xlsx_common.py`、`_xlsx_data.py` 是内部模块，不直接执行。

## 工作流程

1. HTTPS 工作簿先安全下载；完整 URL 仅传给下载脚本，不在回复或日志摘要复述敏感查询参数。`.xls` 先转换；中文 CSV 可先指定编码转换成 XLSX。
2. 编辑或分析前先检查工作簿。按游标读取相关数据范围及各相关 sheet，不能把前几行当作完整数据。确认真正表头、数据起止行、合并锚点、明细与汇总、单位、公式和缓存。
3. 明确指标对应字段、分子分母、连接键、日期粒度及输出位置。关键缺失信息会影响结论时提出精确问题；其余说明合理假设后继续。
4. 简单可维护计算优先使用最终工作簿中的 Excel 公式。分析/模型脚本输出结果快照和方法说明，再调用 `apply_workbook.py --spec-file <返回的 spec_path>` 写入新工作簿或源文件副本；脚本生成的模型参数不代表任意 Python 执行接口。
5. 结果含公式或 `requires_recalculation: true` 时，用 `recalculate_workbook.py` 重算，确认 `status: success`、`total_errors: 0`；再检查关键引用、缓存与业务口径。缓存为空需分辨空字符串与未计算，不能直接当作 0。
6. 创建/修改后用 `render_workbook.py` 逐页或分批检查全部相关页面：乱码、裁切、分页、长文本、图表与合并区域。数值检查与视觉检查通过后交付。

## 数据与分析约束

- 合并区域只写左上角。提取时仅对确认属于同一记录/分组的分类字段填充，不对整表盲目向前填充。
- 负数可能是退款或冲销，超过 100% 可能是完成率；先核对业务含义，不自动取绝对值、归零、删行或改单位。
- 缺失、零和未知分开处理；聚合的 `count` 不含空值、`size` 含空值；排除汇总行前确认其真实语义，保留来源行号和处理数量。
- 可修改的输入、假设和计算公式应留在交付表中。分类标签、清洗映射、模型预测、优化方案可保存静态结果，并附来源、参数、有效范围与重新运行方式；不要承诺模型结果会随单元格自动更新。
- 将事实、相关关系、模型估计和业务假设分开陈述。没有实际检验不填 p 值，没有区间计算不声称置信区间，不从特征重要性推导因果关系。
