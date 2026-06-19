"""
牛股8维评分贡献分析 v3
=========================
目标：分析8个维度，哪个对牛股收益贡献最大。

方法：
  1. 加载 all_7d_scores.json (8维评分 + ret_20d/40d/60d)
  2. 只保留牛股样本 (bull_sample_pool.json)
  3. 对每个维度按中位数分高低组，比较 ret_20d 均值差距
  4. 训练逻辑回归，输出每个维度的权重系数
"""

import sys, json, warnings, math
warnings.filterwarnings("ignore")
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import cross_val_score

PROJECT_ROOT = Path(__file__).parent.parent


def load_data():
    print("【1. 加载数据】")
    with open(PROJECT_ROOT / "data" / "all_7d_scores.json") as f:
        scores = json.load(f)
    print(f"  评分记录: {len(scores):,} 条")

    with open(PROJECT_ROOT / "data" / "bull_sample_pool.json") as f:
        bulls = json.load(f)
    bull_codes = set(str(s["code"]).zfill(6) for s in bulls)
    print(f"  牛股样本: {len(bull_codes)} 只")

    df = pd.DataFrame(scores)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].isin(bull_codes)].copy()
    print(f"  牛股匹配: {len(df):,} 条")
    return df


def analyze_dimension_contribution(df):
    """分析每个维度对 ret_20d 的贡献（IC + 分组收益差距）"""
    print(f"\n{'='*60}")
    print("【2. 8维评分贡献分析（按20日收益率）")
    print(f"{'='*60}")

    dim_map = [
        ("tech_weighted", "技术面"),
        ("fundam_weighted", "基本面"),
        ("fund_weighted", "资金面"),
        ("institutional_weighted", "机构面"),
        ("lh_institutional_weighted", "龙虎榜"),
        ("sentiment_weighted", "情绪面"),
        ("news_event_weighted", "新闻面"),
        ("chip_weighted", "筹码面"),
    ]

    results = []
    for col, name in dim_map:
        if col not in df.columns:
            continue
        valid = df[[col, "ret_20d"]].dropna()
        if len(valid) < 50:
            continue
        ic = valid[col].corr(valid["ret_20d"])

        median_val = valid[col].median()
        high = valid[valid[col] >= median_val]["ret_20d"]
        low  = valid[valid[col] <  median_val]["ret_20d"]

        results.append({
            "维度":     name,
            "IC":       round(ic, 4),
            "高分组均值": round(high.mean(), 1),
            "低分组均值": round(low.mean(), 1),
            "差距":       round(high.mean() - low.mean(), 1),
        })

    ic_df = pd.DataFrame(results).sort_values("IC", ascending=False)
    print(ic_df.to_string(index=False))
    print()
    return ic_df, dim_map


def train_logistic(df, dim_map):
    """训练逻辑回归，输出8维权重"""
    print(f"{'='*60}")
    print("【3. 逻辑回归：学习8维最优权重】")
    print(f"{'='*60}")

    dim_cols = [c for c, _ in dim_map if c in df.columns]
    df = df.dropna(subset=["ret_20d"] + dim_cols).copy()
    if len(df) < 100:
        print("  ❌ 样本太少，跳过")
        return None, None

    # 标签：ret_20d > 0 为上涨
    df["up"] = (df["ret_20d"] > 0).astype(int)
    X = df[dim_cols].fillna(df[dim_cols].median()).values
    y = df["up"].values()

    model = LogisticRegression(
        penalty="l2", C=1.0, solver="lbfgs",
        max_iter=1000, random_state=42,
    )
    scores = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
    print(f"  Cross-val AUC: {scores.mean():.3f} (±{scores.std():.3f})")

    model.fit(X, y)
    y_prob = model.predict_proba(X)[:, 1]
    auc = roc_auc_score(y, y_prob)
    acc = accuracy_score(y, model.predict(X))
    print(f"  全量 AUC:   {auc:.3f}")
    print(f"  全量准确率: {acc*100:.1f}%")

    print(f"\n  维度权重 (系数):")
    dim_names = [n for c, n in dim_map if c in df.columns]
    for name, coef in zip(dim_names, model.coef_[0]):
        print(f"    {name:8s}: {coef:+.4f}  (exp={math.exp(coef):.3f}x)")
    print(f"\n  截距 (bias): {model.intercept_[0]:.4f}")

    weights = dict(zip(dim_cols, model.coef_[0].tolist()))
    weights["intercept"] = float(model.intercept_[0])
    with open(PROJECT_ROOT / "data" / "bull_8d_weights.json", "w") as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 权重已保存: data/bull_8d_weights.json")

    return model, dim_cols


def backtest_by_dimension(df, model, dim_cols):
    """按预测概率分档，看实际收益"""
    print(f"\n{'='*60}")
    print("【4. 回测验证：预测概率 → 实际收益】")
    print(f"{'='*60}")

    if model is None:
        print("  ❌ 模型未训练，跳过")
        return

    dim_names = [n for c, n in [
        ("tech_weighted", "技术面"),
        ("fundam_weighted", "基本面"),
        ("fund_weighted", "资金面"),
        ("institutional_weighted", "机构面"),
        ("lh_institutional_weighted", "龙虎榜"),
        ("sentiment_weighted", "情绪面"),
        ("news_event_weighted", "新闻面"),
        ("chip_weighted", "筹码面"),
    ] if c in df.columns]

    X = df[dim_cols].fillna(0).values()
    df = df.copy()
    df["pred_prob"] = model.predict_proba(X)[:, 1]

    # 按 pred_prob 分5档
    df["prob_bin"] = pd.qcut(df["pred_prob"], q=5,
                                 labels=["Q1最低","Q2","Q3","Q4","Q5最高"])
    bin_res = df.groupby("prob_bin")["ret_20d"].agg(["count","mean"]).round(1)
    bin_res.columns = ["样本数","平均20日收益(%)"]
    print(bin_res)
    print()

    # 按每个维度分档
    print("  单维度分档 → 实际收益:")
    for col, name in zip(dim_cols, dim_names):
        valid = df[[col, "ret_20d"]].dropna()
        if len(valid) < 50:
            continue
        valid["bin"] = pd.qcut(valid[col], q=5,
                                  labels=["Q1最低","Q2","Q3","Q4","Q5最高"])
        grp = valid.groupby("bin")["ret_20d"].mean().round(1)
        print(f"    {name}: Q1={grp.iloc[0]:+.1f}%  Q5={grp.iloc[-1]:+.1f}%  差距={grp.iloc[-1]-grp.iloc[0]:+.1f}%")


def main():
    df = load_data()
    if len(df) < 100:
        print("❌ 样本太少，无法分析")
        return

    ic_df, dim_map = analyze_dimension_contribution(df)
    model, dim_cols = train_logistic(df, dim_map)
    backtest_by_dimension(df, model, dim_cols)

    print(f"\n✅ 完成！")
    print(f"  权重文件: data/bull_8d_weights.json")


if __name__ == "__main__":
    main()
