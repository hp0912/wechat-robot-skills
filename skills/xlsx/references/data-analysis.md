# 数据理解、处理和汇总

## 先确认数据口径

用 `scripts/inspect_workbook.py` 分段检查相关 sheet。它返回活动表、隐藏状态、合并范围、表格图表、公式、缓存、样式和行列游标；表头不清晰时向下、向右继续读取，不猜测空表头的字段含义。对多表确定各自主键与关系，不默认逐表独立分析，也不默认第一张表代表整本工作簿。

`analyze_workbook.py` 的 `profile` 检查指定范围的类型分布、缺失、唯一值与疑似汇总行。该检查不自动排除任何候选行；“合计成本”可能是字段名称，备注里出现“合计”也未必是汇总行。排除明细中的真实汇总行后再聚合，避免重复计数。

合并区域用于分类标记时，可以对确认的分类字段前向填充；数值和普通缺失记录不能跟随整表填充。ID、邮编、前导零文本先保持文本，只有指定数值字段才转换。退款、冲销、净流出等负数照业务含义保留。

## 统一调用

通过 `execute_skill_script` 调用 `scripts/analyze_workbook.py`：

```text
--input '/usr/local/src/excel/tmp/task/source.xlsx' --spec '<JSON>' --output '/usr/local/src/excel/tmp/task/analysis.json'
```

也支持 `--spec-file <JSON路径>`，与 `--spec` 二选一。`profile` 可省略 `--output`，直接返回 JSON 概况；其他方法输出供原写入脚本使用的操作说明，而不是直接修改源文件。

```text
scripts/apply_workbook.py
--input '/usr/local/src/excel/tmp/task/source.xlsx' --output '/usr/local/src/excel/result.xlsx' --spec-file '<返回的 spec_path>'
```

新建独立结果文件时省略写入脚本的 `--input`。分析计划只追加结果 sheet；若目标已有同名 sheet，先选择新名称或明确规划替换区域，不默默清空已有内容。计划内的 `分析说明` 为保留名称，记录来源哈希、字段映射、筛选规则、算法参数和快照属性。

## 输入范围

所有分析/模型共用 `source` 对象：

```json
{
  "source": {
    "sheet": "销售明细",
    "range": "A3:F502",
    "header_row": 3,
    "columns": {"地区": "B", "销售额": "E", "日期": "A"},
    "exclude_rows": [502],
    "numeric": ["销售额"],
    "dates": {"日期": "%Y-%m-%d"}
  }
}
```

- 默认活动表、第一行为表头、读取整个使用区域。`range` 含表头；`header_row` 是源文件中的真实行号。
- `columns` 是**名称 → Excel 列字母**，用于选择字段、空表头或多层表头的人工映射；未指定时，表头必须非空且唯一。
- `exclude_rows` 指定已确认需要排除的源行号。输出 `__source_row` 始终保存原始行号，不是 DataFrame 的索引。
- Excel 公式使用缓存值；相关区域存在未计算或错误缓存时先重算，不能把缺失缓存视作空记录。外部链接数据需先确认并固化。
- `.xlsx/.xlsm/.xltx/.xltm/.csv/.tsv` 可直接分析；`.xls` 先转换。CSV 默认 `utf-8-sig`，可设 `source.encoding: "gb18030"`，所有字段初始按文本保留。
- 输入文件最大 25 MiB，单次读取最多 500000 单元格；超过时选相关区域或分组处理。不得只分析前一批却宣称覆盖全表。

## 方法

| `method` | 参数与输出 |
| --- | --- |
| `profile` | 类型分布、缺失、唯一值、样本与疑似汇总行；候选最多显示 100 项并报告总数 |
| `transform` | 执行 `steps` 后输出明细及来源行号 |
| `aggregate` | `by: [分组字段]`、`metrics: {字段: 聚合方式}` |
| `pivot` | 同 aggregate，加 `columns: [列维度]`；结果为静态交叉汇总 |
| `describe` | `columns: [数值字段]`；计数、均值、标准差、分位数、极值 |
| `correlate` | `columns`，`correlation: "pearson"` 或 `"spearman"`；相关系数和每对字段的有效样本数 |
| `classify` | `column`、`rules`，详见 [文本处理](text-analysis.md) |

非 profile 方法可用 `result_sheet` 设置首张结果表名称。聚合支持 `sum/count/size/mean/min/max/median/nunique`：`count` 排除值字段空值，`size` 统计记录数，空分类保留；全空 `sum` 保持空白，不自动写 0。均价、转化率等加权指标应从分子与分母的汇总重新计算，不能简单平均各组百分比。

交叉汇总的多层列名采用 JSON 数组形式，如 `["销售额","华东"]`，避免简单拼接导致不同维度重名。没有观测的交叉组合保留空白，不自动认为业务值为零。

## 处理步骤

`steps` 顺序执行，最多 50 项；返回处理前后行数。支持：

| `type` | 字段 |
| --- | --- |
| `trim` | `columns`，仅修剪文本前后空格 |
| `replace` | `columns`、`mapping: {原值: 新值}`，显式同义词/编码映射 |
| `numeric` | `columns`，非法文本报错，不自动去掉单位或百分号 |
| `date` | `columns`、`format`，显式日期格式 |
| `fill` | `columns`，明确 `value` 或 `method: "ffill"` |
| `drop_missing` | `columns`，排除关键字段缺失记录 |
| `deduplicate` | `columns` 为判重键；`keep: "first"/"last"/false` |
| `filter` | `column`、`operator`、`value`；比较 `eq/ne/gt/ge/lt/le`、`in/not_in`、`contains`、`is_missing/not_missing` |
| `sort` | `columns`，可选 `ascending`，稳定排序 |
| `select` | `columns`，始终保留来源行号 |
| `rename` | `mapping: {旧名: 新名}`；不能重命名来源行号 |
| `merge` | `source: {path, …输入范围参数}`、`on: [连接键]`、`how: left/inner/right/outer`；`validate` 默认 many_to_one，可选 one_to_one/one_to_many |
| `concat` | `source: {path, …输入范围参数}`，相同字段纵向拼接，保留文件与行号来源 |

连接键含空值时先处理。默认拒绝意外多对多连接，避免金额因笛卡尔积重复统计。`contains` 是字面子串匹配，不执行正则或代码；任何步骤都没有 `eval`、SQL 或 Python 执行入口。日期跨天、货币换算、百分比缩放等业务运算优先在结果工作簿写可检查的公式，不凭常识自动修改原数值。

## 销售汇总示例

```json
{
  "method": "aggregate",
  "source": {"sheet": "明细", "exclude_rows": [502], "numeric": ["收入", "成本"]},
  "by": ["地区"],
  "metrics": {"收入": "sum", "成本": "sum"},
  "result_sheet": "地区汇总",
  "chart": {"type": "column", "category": "地区", "values": ["收入", "成本"], "title": "各地区收入与成本"}
}
```

`chart` 使用首张结果表，`values` 必须按顺序选择相邻数值列；支持原 xlsx 的 bar/column/line/area/pie。多层表或复杂对照图可在后续 `apply_workbook.py` 操作中明确设置引用。

## 透视表边界与洞察

这里生成的是 pandas 聚合后写入的**静态汇总表和普通图表**，不含原生 Excel PivotTable 字段拖拽、切片器或自动刷新控件。用户需要原生控件时，先说明当前固定接口的边界；不能把静态表称为已满足原生透视表要求。用户需要实时变化的简单汇总时，优先用 SUMIFS/COUNTIFS 等公式。

交付前核对：原始明细与模板保留、分组结果与明细合计一致、记录数与排除口径一致、图表引用实际结果且不重复包含总计。不要因图表不好看删除用户要求的结果；先调整图表或说明限制。

从统计到洞察时，先给结论及数值证据，再给解释和建议；将“数据表现”与“可能原因”区分。相关系数不代表因果或显著性；常量列相关系数为空，不改成零。报告组织见 [展示与交付](reporting.md)。
