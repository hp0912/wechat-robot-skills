# 模型方法与固定接口

先确定目标、对象与粒度、数据和变量、评价指标及需要解释的结论。缺少会改变模型含义的权重、约束或时间粒度时确认；不要假定用户不会回复，也不要编造限制或训练数据。简单业务计算优先工作簿公式，不为求和、比率或格式整理启动机器学习。

## 通用调用

通过 `execute_skill_script` 调用 `scripts/model_workbook.py`：

```text
--input '/usr/local/src/excel/tmp/task/source.xlsx' --spec '<JSON>' --output '/usr/local/src/excel/tmp/task/model.json'
```

`--spec-file` 与 `--spec` 二选一。仅 `task: "optimize"` 可省略输入文件，直接用已确认的系数建模。其他任务共享 [数据分析](data-analysis.md) 的 `source` 结构；建模数据上限 20000 行、100000 单元格。输出 JSON `spec_path` 再交给原 `apply_workbook.py`，不直接覆盖工作簿。

数值字段需有限；除监督学习的特征填补外，模型不自动填补缺失数据。保留异常值和来源行号，避免从数据缺口编造结论。图表可用 `chart` 字段（首结果表），或在原写入脚本中创建。

## 综合评价：`task: "evaluate"`

```json
{
  "task": "evaluate",
  "source": {"sheet": "供应商"},
  "entity": "供应商",
  "directions": {"质量合格率": "benefit", "交货准时率": "benefit", "单价": "cost"},
  "weighting": "user",
  "weights": {"质量合格率": 0.5, "交货准时率": 0.3, "单价": 0.2}
}
```

- `directions`：效益型 `benefit`、成本型 `cost`、中间最优型 `{"target": 7}`。先统一正向化，之后 TOPSIS 的正理想解统一取大值；**不能在已经正向化后再次反转成本方向**。
- `weighting`：默认透明的 `equal`；用户权重用 `user`（全部指标、非负、总和大于零）；差异性赋权用 `entropy`；用户给出比较矩阵时可用 `ahp`。
- 熵权高只说明样本差异性较大，不等于业务重要性。不要在没有用户偏好的情况下编造 AHP 比较矩阵。
- AHP 参数 `comparison_matrix`：按 directions 顺序，2–9 阶正数互反矩阵，对角为 1；CR 不小于 0.1 时拒绝输出排名，需修正比较判断。
- 空列、非数值、不可解释编码需先处理。常量指标没有区分度；所有对象相同则并列，得分为中性 0.5，不随行顺序强行选“第一”。

输出：综合排名（原值、得分、并列名次、来源行号）和指标权重（方向、权重、适用时的信息熵、无区分度标记）。解释优劣势时引用原始指标与权重；不把得分当作获胜概率。必要时在合理权重范围重新运行，观察排名是否稳定。

## 时序预测：`task: "forecast"`

```json
{
  "task": "forecast",
  "source": {"sheet": "月度销量", "dates": {"月份": "%Y-%m-%d"}},
  "by": ["城市"], "date": "月份", "value": "销量",
  "frequency": "MS", "horizon": 3,
  "methods": ["naive", "linear", "moving_average"], "holdout": 6
}
```

- `by` 可省略；每组独立建模。`frequency` 支持 D（日）、W（周末为周日）、MS（月初）、QS（季度初）、YS（年初）。数据需按对应周期锚点记录、连续、无重复，至少 6 期；先按口径处理重复和缺期，不能默认缺期销量为零。
- `horizon` 为 1–120 期。`holdout` 默认最后四分之一且至少 2 期，需保留至少 3 期训练数据。
- `naive` 延续最后值；`linear` 线性趋势；`moving_average` 递归最近最多三期均值；`seasonal_naive` 需明确 `seasonal_period` 且训练长度足够。
- 候选方法仅使用训练时段预测验证时段，按 MAE 选择后用全历史外推。该验证集参与选型，不能称为完全独立的最终测试集。

输出：各组×未来日期的预测值、验证 MAE/RMSE、历史数据。不要在未建区间模型时声称置信区间，不自动把负预测裁为零；检查趋势外推是否符合业务边界并披露限制。不能宣称已经运行 ARIMA、Prophet 或 XGBoost，本接口实际只运行列出的基线方法。

## 回归与分类：`task: "regression" / "classification"`

```json
{
  "task": "regression",
  "source": {"sheet": "样本"},
  "features": ["价格", "促销费用", "城市"],
  "categorical": ["城市"], "target": "销量",
  "algorithm": "ridge", "test_fraction": 0.2
}
```

- 至少 10 条记录，目标列不能缺失。`categorical` 显式列出分类特征，其余为数值特征。ID、目标列、目标派生字段不作为有效预测特征。
- 回归 `algorithm`：`linear`（默认）、`ridge`、`forest`。分类：`logistic`（默认）、`forest`。森林使用固定种子、100 棵树和最大深度 8；不接受任意估计器或任意模型代码。
- `test_fraction` 为 0.1–0.5，默认 0.2，留出集至少 2 条记录。普通样本固定随机划分，分类进行分层。时序/未来预测必须指定 `time_column` 或使用 forecast，按时间划分；相同时点跨切分边界会被拒绝。若同一实体的重复记录可能泄漏信息，应先设计实体隔离的数据集，不能把随机切分当作有效泛化证据。
- 中位数填补、分类缺失标记、One-hot 与标准化都只在训练集拟合，再应用测试集。不会先在全数据预处理后假装留出测试。
- 回归输出训练/测试 MAE、RMSE、R²；分类输出 Accuracy、macro F1，并与均值/多数类基线对比。没有实际计算的 AUC、交叉验证、p 值不填入报告。
- `predict_source` 可指定另一个已下载文件的 `{path, …source参数}`；先保留测试结果，再用全量训练数据重新拟合并预测新样本。缺少未来特征时不能凭空生成未来回归预测。

输出：留出样本实际值与预测值、模型指标、特征重要性/绝对系数、可选新样本预测。系数与重要性是关联说明，不能推断因果方向；有负 R² 或测试表现不如基线时明确说明，不以高训练分数宣传可靠性。

## 聚类和异常检测

- `task: "cluster"`：`features: [数值字段]`；`algorithm: "kmeans"` 默认，`clusters` 默认 3，需至少为 2 且小于样本数；或 `algorithm: "dbscan"`，`eps` 默认 0.5，`min_samples` 默认 5。
- `task: "anomaly"`：`features`，Isolation Forest；`contamination` 默认 0.05，范围 (0,0.5]。该比例是假设，应与业务目的相符，不表示真实异常率。
- 数值特征标准化后分析，不自动删除异常。输出原记录、来源行号和标签；-1 为异常候选或 DBSCAN 噪声，聚类数字不代表优劣。满足条件时报告排除噪声后的轮廓系数。
- 对类别本身或长文本的深层语义先参见文本指南；当前接口不假装运行主题模型、FP-Growth、SMOTE 或任意外部模型。相关性可用 analyze_workbook；需要额外算法时应新增明确的固定接口并单独评估依赖。

## 资源优化：`task: "optimize"`

```json
{
  "task": "optimize",
  "variables": ["产品A", "产品B"], "objective": [30, 20], "sense": "max",
  "bounds": [[0, 100], [0, 80]], "integer": [true, true],
  "constraints": [
    {"name": "可用工时", "coefficients": [2, 1], "relation": "<=", "rhs": 160}
  ]
}
```

- 明确变量、目标、资源限制、数量下限/上限及单位，不自动放松约束。`sense` 默认 min；`bounds` 默认非负无上界，null 表示无界；0–1 决策使用 `[0,1]` 和整数标记。
- `objective` 是按 variables 顺序的线性系数。约束 `relation` 支持 `<=`、`>=`、`==`。最多 200 个变量、1000 条约束。
- 连续/整数/混合整数线性问题使用 SciPy 内置 HiGHS，时间上限 60 秒。仅成功状态、约束与整数性复核通过才输出方案；超时、无解、无界不能当作最优解。
- 可选 `quadratic` 对称矩阵 Q，目标为 `c·x + 0.5*xᵀQx`；只支持连续变量、线性约束、凸最小化或凹最大化。`initial` 可指定初始点，使用 SLSQP。任意非线性函数不在接口范围内。

输出变量方案、目标值、约束左端值和余量。可用参数情景重新运行分析瓶颈；未求解的“增加资源收益”不能编造。所有结果是按给定系数得到的快照，不会自动随着源单元格更新。
