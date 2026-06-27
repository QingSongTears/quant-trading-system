"""
多维度回测分析 v2 — 全量资金面验证
==================================
基于重新生成的 combined_3d_scores.csv (全量3037只股票资金面覆盖)
对比资金面扩展前后的 IC 变化，验证扩展效果。

原结果 (422只资金面):
  技术+基本面 60日IC: +0.0969
  资金面单独 60日IC: +0.0084

预期 (3037只全量):
  资金面IC显著提升 (更多样本 → 更稳定信号)
  三因子/四因子组合IC提升
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.scoring import ScorerRegistry


def main():
    print("=" * 60)
    print("加载 combined_3d_scores.csv (全量资金面)...")
    print("=" * 60)
    df = pd.read_csv(PROJECT_ROOT / "data" / "combined_3d_scores.csv")
    print(f"  数据: {len(df)} 行, {df['code'].nunique()} 只股票, "
          f"{df['as_of_date'].nunique()} 个日期")

    # ── 覆盖统计 ──
    for col in ["tech_weighted", "fundam_weighted", "fund_weighted"]:
        valid = df[col].notna().sum()
        print(f"  {col}: {valid}/{len(df)} ({valid/len(df)*100:.0f}%)")

    # ── 采样: 每只股票每月取1条 ──
    df["sample_key"] = df["code"].astype(str) + "_" + df["year_month"].astype(str)
    df_sample = df.drop_duplicates(subset=["sample_key"]).copy()
    print(f"\n采样后: {len(df_sample)} 行 (每股票×月1条)")

    # 确保 ret 列是 float
    for col in ["ret_20d", "ret_40d", "ret_60d"]:
        df_sample[col] = pd.to_numeric(df_sample[col], errors="coerce")

    # ── 缺失处理 ──
    # 资金面缺失填中位值 (资金面数据从2025-10-17开始, 之前日期无数据)
    fund_na = df_sample["fund_weighted"].isna().mean()
    print(f"资金面缺失率: {fund_na*100:.1f}%")
    if fund_na > 0:
        fund_median = df_sample["fund_weighted"].median()
        print(f"  中位值: {fund_median:.1f}, 缺失→填中位值")
        df_sample["fund_weighted_filled"] = df_sample["fund_weighted"].fillna(fund_median)
    else:
        df_sample["fund_weighted_filled"] = df_sample["fund_weighted"]

    # 技术面缺失处理 (极少)
    tech_na = df_sample["tech_weighted"].isna().mean()
    if tech_na > 0:
        tech_median = df_sample["tech_weighted"].median()
        df_sample["tech_weighted"] = df_sample["tech_weighted"].fillna(tech_median)

    # ── 机构面评分 ──
    print("\n机构面评分 (采样数据, DB批量)...")
    inst_scorer = ScorerRegistry.get("institutional")

    inst_scores = []
    unique_dates = sorted(df_sample["as_of_date"].unique())
    for i, dt in enumerate(unique_dates):
        date_codes = df_sample[df_sample["as_of_date"] == dt]["code"].tolist()
        if not date_codes:
            continue
        try:
            batch_df = inst_scorer.batch_score(date_codes, dt)
            if not batch_df.empty:
                inst_scores.append(batch_df)
        except Exception as e:
            pass  # 静默
        if (i + 1) % 30 == 0:
            print(f"  ... {i + 1}/{len(unique_dates)}")

    if inst_scores:
        inst_all = pd.concat(inst_scores, ignore_index=True)
        inst_all["code"] = inst_all["code"].astype(str)
        df_sample["code"] = df_sample["code"].astype(str)
        df_sample = df_sample.merge(
            inst_all[["code", "as_of_date", "weighted"]].rename(
                columns={"weighted": "inst_weighted"}
            ),
            on=["code", "as_of_date"], how="left"
        )
        df_sample["inst_weighted"] = df_sample["inst_weighted"].fillna(6.7)
        print(f"  机构面评分完成: {len(inst_all)} 条")
    else:
        df_sample["inst_weighted"] = 6.7

    # ── 多维度组合 IC ──
    print("\n" + "=" * 60)
    print("交叉截面 IC 分析 (Spearman)")
    print("=" * 60)

    dims_config = {
        "技术面 v3":       ["tech_weighted"],
        "基本面 v2":       ["fundam_weighted"],
        "资金面 v2.1":     ["fund_weighted_filled"],
        "机构面 v2":       ["inst_weighted"],
        "技术+基本面":     ["tech_weighted", "fundam_weighted"],
        "技术+资金":       ["tech_weighted", "fund_weighted_filled"],
        "技术+机构":       ["tech_weighted", "inst_weighted"],
        "三因子联盟": ["tech_weighted", "fundam_weighted", "fund_weighted_filled"],
        "四因子全开": ["tech_weighted", "fundam_weighted", "fund_weighted_filled", "inst_weighted"],
    }

    results = []
    for name, cols in dims_config.items():
        df_sample[f"combined_{name}"] = df_sample[cols].mean(axis=1)

        for ret_col in ["ret_20d", "ret_40d", "ret_60d"]:
            valid = df_sample.dropna(subset=[f"combined_{name}", ret_col])
            if len(valid) < 50:
                continue
            ic = valid[f"combined_{name}"].corr(valid[ret_col], method="spearman")
            results.append({
                "组合": name,
                "周期": ret_col,
                "IC": round(ic, 4),
                "样本数": len(valid),
            })

    results_df = pd.DataFrame(results)

    # 展示 IC 表
    print("\n各维度组合 IC 对比:")
    pivot = results_df.pivot_table(
        index="组合", columns="周期", values="IC", aggfunc="first"
    )
    for col in ["ret_20d", "ret_40d", "ret_60d"]:
        if col in pivot.columns:
            pivot[col] = pivot[col].astype(float)
    print(pivot.to_string(float_format=lambda x: f"{x:+.4f}"))

    # IC 排名
    print("\n" + "=" * 60)
    print("各组合 60日 IC 排名:")
    ic60 = results_df[results_df["周期"] == "ret_60d"].sort_values("IC", ascending=False)
    for _, row in ic60.iterrows():
        ic_val = row["IC"]
        if pd.isna(ic_val):
            continue
        bar = "█" * max(1, int(abs(ic_val) * 200))
        print(f"  {row['组合']:18s}  IC={ic_val:+.4f}  {bar}")

    # ── 最优组合 五分位收益 ──
    print("\n" + "=" * 60)
    best_combo = ic60.iloc[0]["组合"] if len(ic60) > 0 else "三因子联盟"
    print(f"最优组合 [{best_combo}] 五分位收益:")
    col_name = f"combined_{best_combo}"
    valid = df_sample.dropna(subset=[col_name, "ret_60d"])
    if len(valid) > 100:
        valid["q"] = pd.qcut(valid[col_name].rank(method="first"), 5, labels=False)
        for q in range(5):
            q_data = valid[valid["q"] == q]["ret_60d"]
            print(f"  Q{q+1}: mean={q_data.mean():+.2f}%, "
                  f"median={q_data.median():+.2f}%, "
                  f"win={(q_data>0).sum()/len(q_data)*100:.1f}%, "
                  f"n={len(q_data)}")
        spread = valid[valid["q"] == 4]["ret_60d"].mean() - valid[valid["q"] == 0]["ret_60d"].mean()
        print(f"\n  Q5-Q1 spread: {spread:+.2f}% (正值=高分跑赢)")

    # ── 资金面时间序列分析 ──
    print("\n" + "=" * 60)
    print("资金面 IC 时间序列 (仅2025-10-17后有数据的日期):")
    df_fund_valid = df_sample[df_sample["fund_weighted"].notna()].copy()
    if len(df_fund_valid) > 100:
        # 按日期计算资金面 IC
        fund_ic_by_date = []
        for dt, grp in df_fund_valid.groupby("as_of_date"):
            valid_grp = grp.dropna(subset=["fund_weighted", "ret_60d"])
            if len(valid_grp) > 30:
                ic = valid_grp["fund_weighted"].corr(valid_grp["ret_60d"], method="spearman")
                fund_ic_by_date.append({"date": dt, "IC": ic, "n": len(valid_grp)})
        
        if fund_ic_by_date:
            ic_ts = pd.DataFrame(fund_ic_by_date)
            pos_pct = (ic_ts["IC"] > 0).mean() * 100
            print(f"  日期数: {len(ic_ts)}, IC>0占比: {pos_pct:.0f}%, "
                  f"均值: {ic_ts['IC'].mean():+.4f}, 标准差: {ic_ts['IC'].std():.4f}")
            print(f"  IR (IC均值/标准差): {ic_ts['IC'].mean()/ic_ts['IC'].std():.2f}")

    print("\n✅ 回测分析完成 (全量资金面)")


if __name__ == "__main__":
    main()
