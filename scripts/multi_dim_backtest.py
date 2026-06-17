"""
多维度回测分析 — 跨周期IC + 组合效果量化
========================================
基于 combined_3d_scores.csv (技术v3 + 基本面v2 + 资金面v2)
加入 机构面v2，计算不同维度组合的 IC 和收益区分度。
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.scoring import ScorerRegistry


def main():
    print("加载 combined_3d_scores.csv ...")
    df = pd.read_csv(PROJECT_ROOT / "data" / "combined_3d_scores.csv")
    print(f"  数据: {len(df)} 行, {df['code'].nunique()} 只股票, "
          f"{df['as_of_date'].nunique()} 个日期")

    # ── 数据清洗 ──
    # 资金面只有少量股票有数据, 缺失的填中位值
    print(f"\n清洗: 资金面缺失率 {df['fund_total'].isna().mean()*100:.0f}%")
    fund_median = df["fund_total"].median() if df["fund_total"].notna().sum() > 0 else 0
    print(f"  资金面中位值: {fund_median:.1f}")
    df["fund_total_filled"] = df["fund_total"].fillna(fund_median)
    df["fund_weighted_filled"] = df["fund_weighted"].fillna(fund_median / 21 * 20 if fund_median > 0 else 0)

    # 采样以减少计算量: 每只股票每个月取1条
    df["sample_key"] = df["code"].astype(str) + "_" + df["year_month"].astype(str)
    df_sample = df.drop_duplicates(subset=["sample_key"]).copy()
    print(f"  采样后: {len(df_sample)} 行 (每股票×月1条)")

    # 确保 ret 列是 float
    for col in ["ret_20d", "ret_40d", "ret_60d"]:
        df_sample[col] = pd.to_numeric(df_sample[col], errors="coerce")

    # ── 机构面评分 ──
    print("\n机构面评分 (采样数据)...")
    inst_scorer = ScorerRegistry.get("institutional")

    inst_scores = []
    unique_dates = sorted(df_sample["as_of_date"].unique())
    for i, dt in enumerate(unique_dates):
        date_codes = df_sample[df_sample["as_of_date"] == dt]["code"].tolist()
        if not date_codes:
            continue
        try:
            batch_df = inst_scorer.batch_score(date_codes, dt)
            inst_scores.append(batch_df)
        except Exception as e:
            print(f"  {dt} ERROR: {e}")
        if (i + 1) % 20 == 0:
            print(f"  ... {i + 1}/{len(unique_dates)}")

    if inst_scores:
        inst_all = pd.concat(inst_scores, ignore_index=True)
        inst_all["code"] = inst_all["code"].astype(str)
        df_sample["code"] = df_sample["code"].astype(str)
        df_sample = df_sample.merge(
            inst_all[["code", "as_of_date", "weighted"]].rename(
                columns={"weighted": "inst_weighted"}
            ),
            on=["code", "as_of_date"],
            how="left",
        )
        df_sample["inst_weighted"] = df_sample["inst_weighted"].fillna(6.7)  # 默认中性
        print(f"  机构面评分完成: {len(df_sample)} 行")
    else:
        df_sample["inst_weighted"] = 6.7
        print("  ⚠️ 机构面无数据")

    # ── 多维度组合 IC ──
    print("\n" + "=" * 60)
    print("交叉截面 IC 分析 (Spearman)")
    print("=" * 60)

    # 定义各维度组合
    dims_config = {
        "技术面 v3":       ["weighted"],
        "基本面 v2":       ["fundam_weighted"],
        "资金面 v2.1":     ["fund_weighted_filled"],
        "机构面 v2":       ["inst_weighted"],
        "技术+基本面":     ["weighted", "fundam_weighted"],
        "技术+资金":       ["weighted", "fund_weighted_filled"],
        "技术+机构":       ["weighted", "inst_weighted"],
        "三因子联盟": ["weighted", "fundam_weighted", "fund_weighted_filled"],
        "四因子全开":    ["weighted", "fundam_weighted", "fund_weighted_filled", "inst_weighted"],
    }

    results = []
    for name, cols in dims_config.items():
        df_sample[f"combined_{name}"] = df_sample[cols].mean(axis=1)  # 等权组合

        for ret_col in ["ret_20d", "ret_40d", "ret_60d"]:
            valid = df_sample.dropna(subset=[f"combined_{name}", ret_col])
            if len(valid) < 100:
                continue
            ic = valid[f"combined_{name}"].corr(valid[ret_col], method="spearman")
            results.append({
                "组合": name,
                "周期": ret_col,
                "IC": round(ic, 4),
                "样本数": len(valid),
            })

    results_df = pd.DataFrame(results)

    # 展示
    print("\n各维度组合 IC 对比:")
    pivot = results_df.pivot_table(
        index="组合", columns="周期", values="IC", aggfunc="first"
    )
    for col in ["ret_20d", "ret_40d", "ret_60d"]:
        if col in pivot.columns:
            pivot[col] = pivot[col].astype(float)
    print(pivot.to_string(float_format=lambda x: f"{x:+.4f}"))

    # 最优组合
    print("\n" + "=" * 60)
    print("各组合 60日 IC 排名:")
    ic60 = results_df[results_df["周期"] == "ret_60d"].sort_values("IC", ascending=False)
    for _, row in ic60.iterrows():
        ic_val = row["IC"]
        if pd.isna(ic_val):
            continue
        bar = "█" * max(1, int(abs(ic_val) * 200))
        print(f"  {row['组合']:18s}  IC={ic_val:+.4f}  {bar}")

    # ── 五分位收益 ──
    print("\n" + "=" * 60)
    print("四因子 五分位收益 (ret_60d):")
    n = len(df_sample.dropna(subset=["combined_四因子全开", "ret_60d"]))
    if n > 100:
        df_sample["q"] = pd.qcut(
            df_sample["combined_四因子全开"].rank(method="first"), 5, labels=False
        )
        for q in range(5):
            q_data = df_sample[df_sample["q"] == q]["ret_60d"].dropna()
            if len(q_data) > 0:
                print(f"  Q{q+1}: mean={q_data.mean():+.3f}%, "
                      f"median={q_data.median():+.3f}%, "
                      f"win={((q_data>0).sum()/len(q_data)*100):.1f}%, "
                      f"n={len(q_data)}")
        q0 = df_sample[df_sample["q"] == 0]["ret_60d"].mean()
        q4 = df_sample[df_sample["q"] == 4]["ret_60d"].mean()
        print(f"\n  Q5-Q1 spread: {q0 - q4:+.2f}%")
    else:
        print(f"  样本不足 ({n})")

    print("\n✅ 回测分析完成")


if __name__ == "__main__":
    main()
