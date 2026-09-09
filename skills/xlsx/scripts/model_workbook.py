#!/usr/bin/env python3
"""Controlled modeling tasks; workbook creation stays with apply_workbook.py."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from _xlsx_common import SkillArgumentParser, load_json_argument, run_cli
from _xlsx_data import SOURCE_ROW, numeric, read_dataset, require_columns, save_plan, scalar


def evaluate(frame: pd.DataFrame, spec: dict) -> tuple[list, dict]:
    directions = spec["directions"]
    columns = require_columns(frame, list(directions))
    data = numeric(frame, columns).to_numpy()
    if len(data) < 2:
        raise ValueError("综合评价至少需要两个对象")
    positive = np.zeros_like(data)
    for j, name in enumerate(columns):
        direction = directions[name]
        col = data[:, j]
        if direction == "benefit":
            transformed = col - col.min()
        elif direction == "cost":
            transformed = col.max() - col
        elif isinstance(direction, dict) and "target" in direction:
            distance = np.abs(col - float(direction["target"]))
            transformed = distance.max() - distance
        else:
            raise ValueError("指标方向需为 benefit、cost 或含 target 的对象")
        if not np.isfinite(transformed).all():
            raise ValueError("指标正向化结果无效")
        positive[:, j] = transformed / transformed.max() if transformed.max() > 0 else 0
    method = spec.get("weighting", "equal")
    entropy = np.ones(len(columns))
    consistency = None
    if method == "equal":
        weights = np.ones(len(columns))
    elif method == "user":
        if set(spec["weights"]) != set(columns):
            raise ValueError("weights 必须覆盖且仅覆盖全部指标")
        weights = np.array([spec["weights"][col] for col in columns], dtype=float)
    elif method == "entropy":
        sums = positive.sum(axis=0)
        p = np.divide(positive, sums, out=np.zeros_like(positive), where=sums > 0)
        logs = np.zeros_like(p)
        np.log(p, out=logs, where=p > 0)
        entropy = -(p * logs).sum(axis=0) / math.log(len(data))
        entropy[sums == 0] = 1
        weights = np.maximum(0, 1 - entropy)
        if not weights.any():
            weights = np.ones(len(columns))
    elif method == "ahp":
        matrix = np.asarray(spec["comparison_matrix"], dtype=float)
        n = len(columns)
        if not 2 <= n <= 9 or matrix.shape != (n, n) or not np.isfinite(matrix).all() or (matrix <= 0).any():
            raise ValueError("AHP 需 2–9 阶正数比较矩阵，顺序与 directions 相同")
        if not np.allclose(np.diag(matrix), 1) or not np.allclose(matrix * matrix.T, 1, atol=1e-6):
            raise ValueError("AHP 比较矩阵必须对角为 1 且互反")
        values, vectors = np.linalg.eig(matrix)
        index = np.argmax(values.real)
        weights = np.abs(vectors[:, index].real)
        ri = [0, 0, 0, .58, .90, 1.12, 1.24, 1.32, 1.41, 1.45][n]
        consistency = max(0, float(values[index].real - n) / (n - 1) / ri) if ri else 0
        if consistency >= .1:
            raise ValueError(f"AHP 一致性未通过：CR={consistency:.4f}，需调整用户比较矩阵")
    else:
        raise ValueError("weighting 仅支持 equal、user、entropy、ahp")
    if not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("权重必须非负、有限且总和大于 0")
    weights /= weights.sum()
    norms = np.linalg.norm(positive, axis=0)
    weighted = np.divide(positive, norms, out=np.zeros_like(positive), where=norms > 0) * weights
    # All columns are already benefit-oriented; reversing cost columns again is wrong.
    d_best = np.linalg.norm(weighted - weighted.max(axis=0), axis=1)
    d_worst = np.linalg.norm(weighted - weighted.min(axis=0), axis=1)
    scores = np.divide(d_worst, d_best + d_worst, out=np.full(len(data), .5), where=d_best + d_worst > 0)
    entity = require_columns(frame, [spec["entity"]])[0]
    result = frame[[SOURCE_ROW, entity, *columns]].copy()
    result["得分"] = scores
    result["排名"] = result["得分"].round(12).rank(method="min", ascending=False).astype(int)
    result = result.sort_values("排名", kind="stable")
    weight_table = pd.DataFrame({"指标": columns, "方向": [str(directions[c]) for c in columns], "权重": weights,
                                 "信息熵": entropy if method == "entropy" else [None] * len(columns),
                                 "无区分度": ["是" if value == 0 else "否" for value in norms]})
    return [("综合排名", result), ("指标权重", weight_table)], {"weighting": method, "ahp_cr": consistency,
             "note": "正向化后统一采用最大值作为正理想解；无区分度对象可并列，熵权反映差异性而非业务重要性。"}


def _forecast_values(history: np.ndarray, horizon: int, method: str, season: int) -> np.ndarray:
    if method == "naive":
        return np.repeat(history[-1], horizon)
    if method == "linear":
        slope, intercept = np.polyfit(np.arange(len(history)), history, 1)
        return intercept + slope * np.arange(len(history), len(history) + horizon)
    if method == "moving_average":
        values = list(history)
        for _ in range(horizon):
            values.append(float(np.mean(values[-min(3, len(values)):])))
        return np.asarray(values[-horizon:])
    if method == "seasonal_naive" and 1 <= season <= len(history):
        return np.array([history[-season + (i % season)] for i in range(horizon)])
    raise ValueError("预测方法无效，或 seasonal_period 超过训练数据长度")


def forecast(frame: pd.DataFrame, spec: dict) -> tuple[list, dict]:
    value, date_col = require_columns(frame, [spec["value"], spec["date"]])
    groups = spec.get("by", [])
    if groups:
        require_columns(frame, groups)
    horizon = int(spec.get("horizon", 3))
    if not 1 <= horizon <= 120:
        raise ValueError("horizon 必须在 1–120 之间")
    methods = spec.get("methods", ["naive", "linear"])
    if not methods or set(methods) - {"naive", "linear", "moving_average", "seasonal_naive"}:
        raise ValueError("不支持的预测方法")
    freq = spec.get("frequency", "MS")
    if freq not in {"D", "W", "MS", "QS", "YS"}:
        raise ValueError("frequency 仅支持 D、W、MS、QS、YS")
    frame = frame.copy()
    frame[value] = numeric(frame, [value])[value]
    frame[date_col] = pd.to_datetime(frame[date_col], errors="raise")
    if frame[date_col].isna().any():
        raise ValueError("预测日期列不能缺失")
    predictions, validations = [], []
    iterator = frame.groupby(groups, dropna=False, sort=False) if groups else [((), frame)]
    for key, group in iterator:
        group = group.sort_values(date_col)
        keys = list(key if isinstance(key, tuple) else (key,)) if groups else []
        dates = pd.DatetimeIndex(group[date_col])
        expected = pd.date_range(dates[0], dates[-1], freq=freq)
        if len(group) < 6 or dates.has_duplicates or not dates.equals(expected):
            raise ValueError(f"分组 {keys} 需至少 6 期连续、无重复的 {freq} 数据；缺期不能自动当作 0")
        holdout = int(spec.get("holdout", max(2, len(group) // 4)))
        if not 1 <= holdout <= len(group) - 3:
            raise ValueError("holdout 需保留至少 3 个训练时点")
        values = group[value].to_numpy(dtype=float)
        train, test = values[:-holdout], values[-holdout:]
        season = int(spec.get("seasonal_period", 1))
        candidates = []
        for method in methods:
            predicted = _forecast_values(train, holdout, method, season)
            mae = float(np.mean(np.abs(predicted - test)))
            rmse = float(np.sqrt(np.mean((predicted - test) ** 2)))
            validations.append([*keys, method, len(train), holdout, mae, rmse])
            candidates.append((mae, method))
        selected = min(candidates, key=lambda item: item[0])[1]
        future = _forecast_values(values, horizon, selected, season)
        future_dates = pd.date_range(dates[-1], periods=horizon + 1, freq=freq)[1:]
        predictions += [[*keys, date, float(prediction), selected] for date, prediction in zip(future_dates, future)]
    return [("未来预测", pd.DataFrame(predictions, columns=[*groups, date_col, "预测值", "方法"])),
            ("时序验证", pd.DataFrame(validations, columns=[*groups, "方法", "训练期数", "验证期数", "MAE", "RMSE"])),
            ("历史数据", frame)], {"selection": "按时间留出验证集，以 MAE 选择方法；这不是独立测试集。",
                                      "uncertainty": "基础趋势外推，无预测区间，未自动补期、截断负预测或假定季节性。"}


def supervised(frame: pd.DataFrame, spec: dict, *, classification: bool) -> tuple[list, dict]:
    from sklearn.compose import ColumnTransformer
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
    from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    features = require_columns(frame, spec["features"])
    target = require_columns(frame, [spec["target"]])[0]
    if target in features or SOURCE_ROW in features:
        raise ValueError("目标列和原始行号不能作为特征")
    categorical = spec.get("categorical", [])
    if set(categorical) - set(features):
        raise ValueError("categorical 必须是 features 的子集")
    numerical = [col for col in features if col not in categorical]
    X = frame[features].copy()
    if numerical:
        X[numerical] = numeric(frame, numerical, allow_missing=True)
    for col in categorical:
        X[col] = X[col].map(lambda x: str(x) if pd.notna(x) else np.nan)
    if frame[target].isna().any() or len(frame) < 10:
        raise ValueError("监督学习至少需要 10 行且目标列不能缺失")
    y = frame[target].astype(str) if classification else numeric(frame, [target])[target]
    fraction = float(spec.get("test_fraction", .2))
    if not .1 <= fraction <= .5:
        raise ValueError("test_fraction 必须在 0.1–0.5 之间")
    test_rows = max(2, math.ceil(len(frame) * fraction))
    indexes = np.arange(len(frame))
    if spec.get("time_column"):
        date_col = require_columns(frame, [spec["time_column"]])[0]
        dates = pd.to_datetime(frame[date_col], errors="raise")
        if dates.isna().any():
            raise ValueError("时间列不能缺失")
        indexes = np.argsort(dates.to_numpy(), kind="stable")
        boundary = len(frame) - test_rows
        train, test = indexes[:boundary], indexes[boundary:]
        if dates.iloc[train].max() >= dates.iloc[test].min():
            raise ValueError("时间切分边界有相同时点；请先按时点汇总或调整切分比例")
    else:
        train, test = train_test_split(indexes, test_size=test_rows, random_state=42, stratify=y if classification else None)
    if classification and (y.iloc[train].nunique() < 2 or not set(y.iloc[test]) <= set(y.iloc[train])):
        raise ValueError("训练集必须覆盖至少两个类别且包含测试集所有类别")
    transformers = []
    if numerical:
        transformers.append(("numeric", Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)), ("scale", StandardScaler())]), numerical))
    if categorical:
        transformers.append(("category", Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="缺失", keep_empty_features=True)),
                                                      ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False, max_categories=50))]), categorical))
    method = spec.get("algorithm", "logistic" if classification else "linear")
    choices = ({"logistic": LogisticRegression(max_iter=1000, class_weight="balanced"),
                "forest": RandomForestClassifier(n_estimators=100, max_depth=8, random_state=42, n_jobs=1, class_weight="balanced")}
               if classification else {"linear": LinearRegression(), "ridge": Ridge(alpha=1.),
                                        "forest": RandomForestRegressor(n_estimators=100, max_depth=8, random_state=42, n_jobs=1)})
    if method not in choices:
        raise ValueError(f"algorithm 可选：{list(choices)}")
    pipeline = Pipeline([("prepare", ColumnTransformer(transformers)), ("model", choices[method])])
    pipeline.fit(X.iloc[train], y.iloc[train])
    baseline = DummyClassifier(strategy="most_frequent") if classification else DummyRegressor(strategy="mean")
    baseline.fit(np.zeros((len(train), 1)), y.iloc[train])
    metrics = []
    for label, subset in (("训练", train), ("测试", test)):
        actual = y.iloc[subset]
        for algorithm, predicted in ((method, pipeline.predict(X.iloc[subset])), ("基线", baseline.predict(np.zeros((len(subset), 1))))):
            stats = {"Accuracy": accuracy_score(actual, predicted), "F1_macro": f1_score(actual, predicted, average="macro", zero_division=0)} if classification else {
                "MAE": mean_absolute_error(actual, predicted), "RMSE": math.sqrt(mean_squared_error(actual, predicted)), "R2": r2_score(actual, predicted)}
            metrics += [[label, algorithm, key, float(value)] for key, value in stats.items()]
    predictions = frame.iloc[test][[SOURCE_ROW, *features]].copy()
    predictions["实际值"] = y.iloc[test].to_numpy()
    predictions["预测值"] = pipeline.predict(X.iloc[test])
    model = pipeline.named_steps["model"]
    names = pipeline.named_steps["prepare"].get_feature_names_out()
    importance = model.feature_importances_ if hasattr(model, "feature_importances_") else np.mean(np.abs(np.atleast_2d(model.coef_)), axis=0)
    features_table = pd.DataFrame({"特征": names, "重要性或绝对系数": importance}).sort_values("重要性或绝对系数", ascending=False)
    tables = [("留出预测", predictions), ("模型指标", pd.DataFrame(metrics, columns=["数据集", "模型", "指标", "值"])), ("特征说明", features_table)]
    if spec.get("predict_source"):
        request = spec["predict_source"]
        future, provenance = read_dataset(request["path"], {k: v for k, v in request.items() if k != "path"})
        require_columns(future, features)
        future_X = future[features].copy()
        if numerical:
            future_X[numerical] = numeric(future, numerical, allow_missing=True)
        for col in categorical:
            future_X[col] = future_X[col].map(lambda x: str(x) if pd.notna(x) else np.nan)
        pipeline.fit(X, y)
        future = future[[SOURCE_ROW, *features]].copy()
        future["预测值"] = pipeline.predict(future_X)
        tables.append(("新样本预测", future))
    else:
        provenance = None
    return tables, {"algorithm": method, "train_rows": len(train), "test_rows": len(test), "prediction_source": provenance,
                    "note": "训练集拟合填补、编码和标准化；固定留出集对比简单基线。特征重要性不是因果影响。新样本预测使用全量训练数据重新拟合。"}


def unsupervised(frame: pd.DataFrame, spec: dict, *, anomaly: bool) -> tuple[list, dict]:
    from sklearn.cluster import DBSCAN, KMeans
    from sklearn.ensemble import IsolationForest
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    columns = require_columns(frame, spec["features"])
    data = numeric(frame, columns)
    if len(data) < 3:
        raise ValueError("至少需要 3 条完整数值记录")
    scaled = StandardScaler().fit_transform(data)
    if anomaly:
        contamination = float(spec.get("contamination", .05))
        if not 0 < contamination <= .5:
            raise ValueError("contamination 必须在 (0, 0.5] 之间")
        model = IsolationForest(contamination=contamination, random_state=42, n_jobs=1)
    elif spec.get("algorithm", "kmeans") == "kmeans":
        k = int(spec.get("clusters", 3))
        if not 2 <= k < len(data):
            raise ValueError("clusters 必须至少为 2 且小于样本数")
        model = KMeans(n_clusters=k, n_init=10, random_state=42)
    elif spec["algorithm"] == "dbscan":
        model = DBSCAN(eps=float(spec.get("eps", .5)), min_samples=int(spec.get("min_samples", 5)))
    else:
        raise ValueError("聚类 algorithm 仅支持 kmeans、dbscan")
    labels = model.fit_predict(scaled)
    result = frame.copy()
    result["异常标记" if anomaly else "簇编号"] = labels
    if anomaly:
        result["正常程度得分"] = model.decision_function(scaled)
    score = None
    valid = labels != -1
    if not anomaly and 1 < len(set(labels[valid])) < valid.sum():
        score = float(silhouette_score(scaled[valid], labels[valid], sample_size=min(2000, int(valid.sum())), random_state=42))
    return [("异常检测" if anomaly else "聚类结果", result)], {"silhouette_without_noise": score,
             "note": "数值字段先标准化；-1 表示异常候选或 DBSCAN 噪声，不自动删除。聚类编号没有优劣顺序。"}


def optimize(spec: dict) -> tuple[list, dict]:
    from scipy.optimize import Bounds, LinearConstraint, milp, minimize

    names = spec["variables"]
    if not isinstance(names, list) or not 1 <= len(names) <= 200 or len(set(names)) != len(names):
        raise ValueError("variables 需为 1–200 个唯一名称")
    n = len(names)
    c = np.asarray(spec["objective"], dtype=float)
    if c.shape != (n,) or not np.isfinite(c).all():
        raise ValueError("objective 需为每个变量的有限数值系数")
    direction = spec.get("sense", "min")
    if direction not in {"min", "max"}:
        raise ValueError("sense 仅支持 min、max")
    sign = 1 if direction == "min" else -1
    limits = spec.get("bounds", [[0, None] for _ in names])
    if len(limits) != n or any(len(bound) != 2 for bound in limits):
        raise ValueError("bounds 需给出每个变量的 [下限, 上限]，null 表示无界")
    lower = np.array([-np.inf if x[0] is None else x[0] for x in limits], dtype=float)
    upper = np.array([np.inf if x[1] is None else x[1] for x in limits], dtype=float)
    if np.isnan(lower).any() or np.isnan(upper).any() or (lower > upper).any():
        raise ValueError("变量边界无效")
    constraints = spec.get("constraints", [])
    if len(constraints) > 1000:
        raise ValueError("约束最多 1000 项")
    A, lows, highs = [], [], []
    for constraint in constraints:
        row = np.asarray(constraint["coefficients"], dtype=float)
        rhs, relation = float(constraint["rhs"]), constraint["relation"]
        if row.shape != (n,) or not np.isfinite(row).all() or not math.isfinite(rhs) or relation not in {"<=", ">=", "=="}:
            raise ValueError("约束系数、右端值或 relation 无效")
        A.append(row)
        lows.append(rhs if relation in {">=", "=="} else -np.inf)
        highs.append(rhs if relation in {"<=", "=="} else np.inf)
    A = np.asarray(A).reshape((-1, n))
    linear = LinearConstraint(A, lows, highs) if constraints else None
    integer = spec.get("integer", [False] * n)
    if len(integer) != n or any(type(v) is not bool for v in integer):
        raise ValueError("integer 需为与变量数一致的布尔列表")
    if "quadratic" in spec:
        Q = np.asarray(spec["quadratic"], dtype=float)
        if any(integer) or Q.shape != (n, n) or not np.isfinite(Q).all() or not np.allclose(Q, Q.T):
            raise ValueError("quadratic 需为对称矩阵，二次规划仅支持连续变量")
        if np.linalg.eigvalsh(sign * Q).min() < -1e-9:
            raise ValueError("仅支持凸最小化或凹最大化的二次目标")
        start = np.asarray(spec.get("initial", np.clip(np.zeros(n), lower, upper)), dtype=float)
        if start.shape != (n,) or not np.isfinite(start).all():
            raise ValueError("initial 需为有限数值向量")
        objective = lambda x: float(c @ x + .5 * x @ Q @ x)
        result = minimize(lambda x: sign * objective(x), start, jac=lambda x: sign * (c + Q @ x), method="SLSQP",
                          bounds=Bounds(lower, upper), constraints=[linear] if linear else [], options={"maxiter": 1000, "ftol": 1e-9})
        guarantee = "凸二次规划的数值解，已检查可行性"
    else:
        objective = lambda x: float(c @ x)
        result = milp(sign * c, integrality=np.asarray(integer, dtype=int), bounds=Bounds(lower, upper),
                      constraints=linear, options={"time_limit": 60., "mip_rel_gap": 0.})
        guarantee = "HiGHS 求解成功；仅在成功且可行时输出方案"
    if not result.success or result.x is None:
        raise ValueError(f"求解未成功，不能输出最优方案：status={result.status}; {result.message}")
    x = result.x
    activity = A @ x
    tolerance = 1e-6
    if (x < lower - tolerance).any() or (x > upper + tolerance).any() or (activity < np.asarray(lows) - tolerance).any() or (activity > np.asarray(highs) + tolerance).any():
        raise ValueError("求解结果未通过约束可行性复核")
    if any(abs(x[i] - round(x[i])) > tolerance for i in range(n) if integer[i]):
        raise ValueError("整数变量未通过整数性复核")
    rows = [[constraint.get("name", f"约束{i+1}"), float(activity[i]), constraint["relation"], constraint["rhs"],
             float(min(activity[i] - lows[i], highs[i] - activity[i]))] for i, constraint in enumerate(constraints)]
    return [("优化方案", pd.DataFrame({"变量": names, "取值": x})),
            ("约束复核", pd.DataFrame(rows, columns=["约束", "左端值", "关系", "右端值", "余量"]))], {
                "objective_value": objective(x), "solver_status": int(result.status), "guarantee": guarantee,
                "note": "未自动放松约束；敏感性分析需明确修改参数并重新求解。"}


def model(path: str | None, spec: dict) -> tuple[list, dict]:
    task = spec["task"]
    options = {
        "evaluate": {"entity", "directions", "weighting", "weights", "comparison_matrix"},
        "forecast": {"by", "date", "value", "frequency", "horizon", "methods", "holdout", "seasonal_period"},
        "regression": {"features", "categorical", "target", "algorithm", "test_fraction", "time_column", "predict_source"},
        "classification": {"features", "categorical", "target", "algorithm", "test_fraction", "time_column", "predict_source"},
        "cluster": {"features", "algorithm", "clusters", "eps", "min_samples"},
        "anomaly": {"features", "contamination"},
        "optimize": {"variables", "objective", "sense", "bounds", "integer", "constraints", "quadratic", "initial"},
    }
    if task not in options:
        raise ValueError(f"不支持的 task：{task}")
    unknown = set(spec) - {"task", "source", "chart"} - options[task]
    if unknown:
        raise ValueError(f"模型说明包含未知参数：{sorted(unknown)}")
    source = None
    if task == "optimize":
        tables, details = optimize(spec)
    else:
        if not path:
            raise ValueError("此任务需要 --input")
        frame, source = read_dataset(path, spec.get("source"))
        if frame.empty:
            raise ValueError("建模范围没有数据记录")
        if frame.size > 100_000 or len(frame) > 20_000:
            raise ValueError("建模上限为 20000 行、100000 单元格；请缩小明确的建模范围")
        if task == "evaluate":
            tables, details = evaluate(frame, spec)
        elif task == "forecast":
            tables, details = forecast(frame, spec)
        elif task in {"regression", "classification"}:
            tables, details = supervised(frame, spec, classification=task == "classification")
        elif task in {"cluster", "anomaly"}:
            tables, details = unsupervised(frame, spec, anomaly=task == "anomaly")
        else:
            raise ValueError(f"不支持的 task：{task}")
    return tables, {"task": task, "source": source, "parameters": spec, "results": details,
                    "result_kind": "模型结果快照，修改输入后需按相同参数重新运行；不是可自动重算的 Excel 公式。"}


def main() -> dict:
    parser = SkillArgumentParser(description="评价、预测、回归、分类、聚类、异常检测和受控优化；输出 xlsx 写入操作 JSON。")
    parser.add_argument("--input")
    parser.add_argument("--spec")
    parser.add_argument("--spec-file")
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    spec = load_json_argument(args.spec, args.spec_file, label="模型说明")
    tables, metadata = model(args.input, spec)
    return save_plan(tables, metadata, args.output, overwrite=args.overwrite, chart=spec.get("chart"))


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
