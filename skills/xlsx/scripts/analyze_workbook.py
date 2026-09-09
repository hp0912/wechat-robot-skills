#!/usr/bin/env python3
"""Analyze table data and emit JSON operations for apply_workbook.py."""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from _xlsx_common import SkillArgumentParser, load_json_argument, run_cli
from _xlsx_data import MAX_DATA_CELLS, SOURCE_ROW, numeric, read_dataset, require_columns, save_plan, scalar


def profile(frame: pd.DataFrame, source: dict) -> dict:
    fields = []
    for name in frame.columns:
        if name == SOURCE_ROW:
            continue
        series = frame[name]
        fields.append({"name": name, "missing": int(series.isna().sum()),
                       "types": {str(k): int(v) for k, v in series.dropna().map(lambda x: type(x).__name__).value_counts().items()},
                       "unique": int(series.nunique(dropna=True)),
                       "examples": [scalar(x) for x in series.dropna().head(5)]})
    marker = re.compile(r"^(?:合计|总计|小计|汇总|累计|grand total|subtotal|total)(?:\s|[:：]|$)", re.I)
    candidates = []
    for row in frame.itertuples(index=False, name=None):
        matches = [str(x) for x in row[1:] if isinstance(x, str) and marker.search(x.strip())]
        if matches:
            candidates.append({"row": int(row[0]), "labels": matches[:3]})
    return {"source": source, "columns": fields, "summary_row_candidates": candidates[:100],
            "summary_row_candidate_count": len(candidates), "summary_candidates_truncated": len(candidates) > 100,
            "note": "候选汇总行未自动排除；先确认口径，再用 source.exclude_rows 指定原始行号。"}


def transform(frame: pd.DataFrame, operations: list[dict], audit: list) -> pd.DataFrame:
    if not isinstance(operations, list) or len(operations) > 50:
        raise ValueError("steps 必须为不超过 50 项的列表")
    for op in operations:
        kind = op["type"]
        before = len(frame)
        cols = op.get("columns", [])
        if cols:
            require_columns(frame, cols)
        if kind == "trim":
            for col in cols:
                frame[col] = frame[col].map(lambda x: x.strip() if isinstance(x, str) else x)
        elif kind == "replace":
            frame[cols] = frame[cols].replace(op["mapping"])
        elif kind == "numeric":
            frame[cols] = numeric(frame, cols, allow_missing=True)
        elif kind == "date":
            for col in cols:
                frame[col] = pd.to_datetime(frame[col], format=op["format"], errors="raise")
        elif kind == "fill":
            if op.get("method") == "ffill":
                frame[cols] = frame[cols].ffill()
            elif "value" in op:
                frame[cols] = frame[cols].fillna(op["value"])
            else:
                raise ValueError("fill 需明确 method=ffill 或 value")
        elif kind == "drop_missing":
            frame = frame.dropna(subset=require_columns(frame, cols))
        elif kind == "deduplicate":
            keep = op.get("keep", "first")
            if keep not in ("first", "last", False):
                raise ValueError("deduplicate.keep 仅支持 first、last 或 false")
            frame = frame.drop_duplicates(subset=require_columns(frame, cols), keep=keep)
        elif kind == "filter":
            col = require_columns(frame, [op["column"]])[0]
            series, value, predicate = frame[col], op.get("value"), op["operator"]
            if predicate in {"eq", "ne", "gt", "ge", "lt", "le"}:
                mask = getattr(series, predicate)(value)
            elif predicate in {"in", "not_in"}:
                mask = series.isin(value)
                if predicate == "not_in":
                    mask = ~mask
            elif predicate in {"is_missing", "not_missing"}:
                mask = series.isna() if predicate == "is_missing" else series.notna()
            elif predicate == "contains":
                mask = series.astype("string").str.contains(str(value), regex=False, na=False)
            else:
                raise ValueError(f"不支持的 filter.operator：{predicate}")
            frame = frame.loc[mask.fillna(False)].copy()
        elif kind == "sort":
            frame = frame.sort_values(require_columns(frame, cols), ascending=op.get("ascending", True), kind="stable")
        elif kind == "select":
            chosen = require_columns(frame, cols)
            frame = frame[list(dict.fromkeys([SOURCE_ROW, *chosen]))]
        elif kind == "rename":
            mapping = op["mapping"]
            require_columns(frame, list(mapping))
            if SOURCE_ROW in mapping or SOURCE_ROW in mapping.values():
                raise ValueError("不能重命名保留的原始行号列")
            frame = frame.rename(columns=mapping)
        elif kind in {"merge", "concat"}:
            other, info = read_dataset(op["source"]["path"], {k: v for k, v in op["source"].items() if k != "path"})
            if kind == "concat":
                if set(frame.columns) - {"__source_file"} != set(other.columns) - {"__source_file"}:
                    raise ValueError("concat 需要相同字段；请先对齐字段")
                # Row numbers alone are ambiguous after stacking multiple files.
                frame = frame.copy()
                if "__source_file" not in frame:
                    frame["__source_file"] = op.get("left_source_label", "primary")
                other["__source_file"] = info["path"]
                frame = pd.concat([frame, other], ignore_index=True)
            else:
                keys = require_columns(frame, op["on"])
                require_columns(other, keys)
                if frame[keys].isna().any().any() or other[keys].isna().any().any():
                    raise ValueError("merge 的连接键含空值；请先处理，避免把不同空值记录相互匹配")
                validate = op.get("validate", "many_to_one")
                if validate not in {"one_to_one", "one_to_many", "many_to_one"}:
                    raise ValueError("merge.validate 仅支持 one_to_one、one_to_many、many_to_one")
                how = op.get("how", "left")
                if how not in {"left", "inner", "right", "outer"}:
                    raise ValueError("merge.how 无效")
                frame = frame.merge(other, on=keys, how=how, validate=validate, suffixes=("", "_right"))
            audit.append({"additional_source": info})
        else:
            raise ValueError(f"不支持的处理步骤：{kind}")
        if not frame.columns.is_unique:
            raise ValueError("处理后出现重复字段名")
        if frame.size > MAX_DATA_CELLS:
            raise ValueError("处理结果超过 500000 单元格")
        audit.append({"step": kind, "rows_before": before, "rows_after": len(frame)})
    return frame


def aggregate(frame: pd.DataFrame, spec: dict, *, pivot: bool) -> pd.DataFrame:
    by = require_columns(frame, spec["by"])
    metrics = spec["metrics"]
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics 需为字段到聚合方式的非空映射")
    require_columns(frame, list(metrics))
    allowed = {"sum", "count", "size", "mean", "min", "max", "median", "nunique"}
    if set(metrics.values()) - allowed:
        raise ValueError(f"聚合方式仅支持：{sorted(allowed)}")
    numeric_cols = [col for col, method in metrics.items() if method in {"sum", "mean", "min", "max", "median"}]
    if numeric_cols:
        frame = frame.copy()
        frame[numeric_cols] = numeric(frame, numeric_cols, allow_missing=True)
    reducers = {col: (lambda x: x.sum(min_count=1)) if method == "sum" else method for col, method in metrics.items()}
    column_fields = spec.get("columns", []) if pivot else []
    if column_fields:
        require_columns(frame, column_fields)
        if set(by) & set(column_fields):
            raise ValueError("行维度与列维度不能重复")
    result = frame.groupby(by + column_fields, dropna=False, sort=False, observed=True).agg(reducers)
    if column_fields:
        result = result.unstack(column_fields)
        result.columns = [json_label(parts) for parts in result.columns.to_flat_index()]
    return result.reset_index()


def json_label(parts: Any) -> str:
    import json
    return json.dumps([scalar(x) for x in parts], ensure_ascii=False, separators=(",", ":"))


def classify(frame: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    column = require_columns(frame, [spec["column"]])[0]
    rules = spec["rules"]
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("rules 必须为不超过 100 项的规则列表")
    for rule in rules:
        if not rule.get("label") or not rule.get("keywords") or not all(isinstance(v, str) and v for v in rule["keywords"]):
            raise ValueError("每条规则必须有 label 和非空 keywords")
    records = []
    for _, row in frame.iterrows():
        raw = row[column]
        cleaned = " ".join(str(raw).split()) if pd.notna(raw) else ""
        matches = [(rule["label"], [word for word in rule["keywords"] if word.casefold() in cleaned.casefold()]) for rule in rules]
        hits = [(label, words) for label, words in matches if words]
        labels = list(dict.fromkeys(label for label, _ in hits))
        category = labels[0] if len(labels) == 1 else ("需复核" if labels else "未知")
        records.append([row[SOURCE_ROW], scalar(raw), cleaned, category,
                        "；".join(labels), "；".join(dict.fromkeys(word for _, words in hits for word in words))])
    detail = pd.DataFrame(records, columns=[SOURCE_ROW, "原文", "清洗文本", "分类", "命中标签", "证据关键词"])
    summary = detail.groupby("分类", dropna=False, sort=False).size().rename("记录数").reset_index()
    summary["占比"] = summary["记录数"] / len(detail) if len(detail) else 0
    return detail, summary


def analyze(path: str, spec: dict) -> tuple[list[tuple[str, pd.DataFrame]], dict]:
    method = spec.get("method", "profile")
    options = {
        "profile": set(), "transform": set(), "aggregate": {"by", "metrics"},
        "pivot": {"by", "columns", "metrics"}, "describe": {"columns"},
        "correlate": {"columns", "correlation"}, "classify": {"column", "rules"},
    }
    if method not in options:
        raise ValueError(f"不支持的 method：{method}")
    unknown = set(spec) - {"method", "source", "steps", "result_sheet", "chart"} - options[method]
    if unknown:
        raise ValueError(f"分析说明包含未知参数：{sorted(unknown)}")
    frame, source = read_dataset(path, spec.get("source"))
    audit: list = []
    frame = transform(frame, spec.get("steps", []), audit)
    method = spec.get("method", "profile")
    metadata = {"source": source, "method": method, "steps": audit, "parameters": spec,
                "result_kind": "数据处理结果快照；需要随输入变化时用工作簿公式，复杂分析需按相同参数重新运行"}
    if method == "profile":
        return [], profile(frame, source)
    if method == "transform":
        result = frame
    elif method in {"aggregate", "pivot"}:
        result = aggregate(frame, spec, pivot=method == "pivot")
        metadata["aggregation_note"] = "空分类保留；count 排除空值，size 包含空值；全空 sum 保持空白；交叉汇总是静态表，不含原生透视控件。"
    elif method == "describe":
        data = numeric(frame, spec["columns"], allow_missing=True)
        result = data.describe().rename_axis("统计量").reset_index()
    elif method == "correlate":
        data = numeric(frame, spec["columns"], allow_missing=True)
        correlation = spec.get("correlation", "pearson")
        if correlation not in {"pearson", "spearman"}:
            raise ValueError("correlation 仅支持 pearson、spearman")
        result = data.corr(method=correlation, min_periods=3).rename_axis("字段").reset_index()
        valid = data.notna().astype(int)
        counts = (valid.T @ valid).rename_axis("字段").reset_index()
        return [(spec.get("result_sheet", "相关系数"), result), ("配对样本数", counts)], metadata
    elif method == "classify":
        details, summary = classify(frame, spec)
        metadata["classification_note"] = "仅按显式关键词匹配；多标签冲突标为需复核，未命中标为未知，不能视为自动情感理解。"
        return [(spec.get("result_sheet", "分类明细"), details), ("分类统计", summary)], metadata
    else:
        raise ValueError(f"不支持的 method：{method}")
    return [(spec.get("result_sheet", "分析结果"), result)], metadata


def main() -> dict:
    parser = SkillArgumentParser(description="分组汇总、交叉分析、清洗、规则分类；输出 xlsx 写入操作 JSON。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--spec")
    parser.add_argument("--spec-file")
    parser.add_argument("--output", help="输出 .json 操作说明，profile 可省略")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    spec = load_json_argument(args.spec, args.spec_file, label="分析说明")
    tables, metadata = analyze(args.input, spec)
    if not tables:
        return metadata
    if not args.output:
        raise ValueError("此方法需 --output 指定 .json 文件")
    return save_plan(tables, metadata, args.output, overwrite=args.overwrite, chart=spec.get("chart"))


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
