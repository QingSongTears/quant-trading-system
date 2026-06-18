"""
六维全开回测 — 全部6个评分器集成验证
=====================================
技术v3 + 基本面v2 + 资金面v2.1 + 机构v2 + 情绪v1 + 新闻v1

基于 combined_3d_scores.csv (已有前3维)，追加后3维进行全维度IC分析。
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, get_db_url
from src.scoring import ScorerRegistry


def main():
    print("=" * 60)
    print("六维全开回测分析")
    print("=" * 60)

    config = get_config()
    engine = create_engine(get_db_url(config), echo=False)

    # ── 加载基础三维评分 ──
    print("\n加载 combined_3d_scores.csv ...")
    df = pd.read_csv(PROJECT_ROOT / "data" / "combined_3d_scores.csv")
    print(f"  数据: {len(df)} 行, {df['code'].nunique()} 只股票, "
          f"{df['as_of_date'].nunique()} 个日期")

    # 覆盖统计
    for col in ["tech_weighted", "fundam_weighted", "fund_weighted"]:
        valid = df[col].notna().sum()
        print(f"  {col}: {valid}/{len(df)} ({valid/len(df)*100:.0f}%)")

    # 采样: 每只股票每月1条
    df["sample_key"] = df["code"].astype(str) + "_" + df["year_month"].astype(str)
    df_sample = df.drop_duplicates(subset=["sample_key"]).copy()
    df_sample["code"] = df_sample["code"].astype(str).str.zfill(6)  # 确保6位字符串
    print(f"  采样后: {len(df_sample)} 行")
    del df  # 释放内存

    # 确保 ret 列是 float
    for col in ["ret_20d", "ret_40d", "ret_60d"]:
        df_sample[col] = pd.to_numeric(df_sample[col], errors="coerce")

    # ── 缺失填充 ──
    for dim_col, fill_val in [
        ("fund_weighted", df_sample["fund_weighted"].median()),
        ("tech_weighted", df_sample["tech_weighted"].median()),
    ]:
        na_rate = df_sample[dim_col].isna().mean()
        if na_rate > 0:
            print(f"  {dim_col} 缺失率 {na_rate*100:.0f}%, 填{fill_val:.1f}")
            df_sample[dim_col] = df_sample[dim_col].fillna(fill_val)
    # 基本面v3将在后续循环中重新计算，旧的fundam_weighted不填默认值

    # ── 后三维评分（含基本面v3重算）──
    unique_dates = sorted(df_sample["as_of_date"].unique())
    total_dates = len(unique_dates)

    for dim_name, label, default_val, col_name in [
        ("fundamental", "基本面v3", 7.6, "fundam_weighted"),  # 用v3替换旧v2评分
        ("institutional", "机构面v2", 6.7, "institutional_weighted"),
        ("sentiment", "情绪面v1", 8.6, "sentiment_weighted"),
        ("news_event", "新闻面v1", 9.0, "news_event_weighted"),
    ]:
        print(f"\n{label} 评分 ({total_dates} 日期)...")

        if col_name in df_sample.columns and dim_name != "fundamental":
            # 跳过已有的（但基本面v3强制重算）
            print(f"  已存在, 跳过")
            continue

        scorer = ScorerRegistry.get(dim_name, engine=engine)
        all_scores = []

        for i, dt in enumerate(unique_dates):
            date_codes = df_sample[df_sample["as_of_date"] == dt]["code"].tolist()
            date_codes = [str(c).zfill(6) for c in date_codes]  # 确保6位字符串
            if not date_codes:
                continue
            try:
                # 基本面评分器不需要 as_of_date（预加载全量数据）
                if dim_name == "fundamental":
                    batch_df = scorer.batch_score(date_codes)
                else:
                    batch_df = scorer.batch_score(date_codes, dt)
                if not batch_df.empty and "weighted" in batch_df.columns:
                    batch_df = batch_df.copy()
                    batch_df["code"] = batch_df["code"].astype(str)
                    batch_df["as_of_date"] = dt
                    all_scores.append(batch_df[["code", "as_of_date", "weighted"]])
            except Exception as e:
                pass  # 静默

            if (i + 1) % 30 == 0:
                print(f"  ... {i+1}/{total_dates}")

        if all_scores:
            dim_df = pd.concat(all_scores, ignore_index=True)
            dim_df = dim_df.rename(columns={"weighted": col_name})
            # 如果目标列已存在则先删除（避免 merge 后缀冲突）
            if col_name in df_sample.columns:
                df_sample = df_sample.drop(columns=[col_name])
            df_sample["code"] = df_sample["code"].astype(str)
            df_sample = df_sample.merge(
                dim_df, on=["code", "as_of_date"], how="left"
            )
            na_rate = df_sample[col_name].isna().mean()
            print(f"  完成: {len(dim_df)} 条, 缺失率 {na_rate*100:.0f}%")
            df_sample[col_name] = df_sample[col_name].fillna(default_val)
        else:
            print(f"  ⚠️  无数据, 使用默认值 {default_val}")
            df_sample[col_name] = default_val

    # ── 维度列名映射 ──
    dim_cols = {
        "技术面 v3":   "tech_weighted",
        "基本面 v2":   "fundam_weighted",
        "资金面 v2.1": "fund_weighted",
        "机构面 v2":   "institutional_weighted",
        "情绪面 v1":   "sentiment_weighted",
        "新闻面 v1":   "news_event_weighted",
    }

    # ── 多维度组合 IC ──
    print("\n" + "=" * 60)
    print("六维交叉 IC 分析 (Spearman)")
    print("=" * 60)

    dims_config = {
        # 单维度
        **{f"单维-{k}": [v] for k, v in dim_cols.items()},
        # 已验证的有效组合
        "技术+基本面":   ["tech_weighted", "fundam_weighted"],
        "技术+资金":      ["tech_weighted", "fund_weighted"],
        "技术+机构":      ["tech_weighted", "institutional_weighted"],
        "三因子联盟":     ["tech_weighted", "fundam_weighted", "fund_weighted"],
        "四因子(+机构)":  ["tech_weighted", "fundam_weighted", "fund_weighted",
                         "institutional_weighted"],
        # 五因子/六因子
        "五因子(+情绪)":  ["tech_weighted", "fundam_weighted", "fund_weighted",
                         "institutional_weighted", "sentiment_weighted"],
        "六维全开":       list(dim_cols.values()),
        # Top-2 组合
        "Tech+Fund+Inst": ["tech_weighted", "fundam_weighted", "institutional_weighted"],
        "Tech+Inst+News": ["tech_weighted", "institutional_weighted", "news_event_weighted"],
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

    # IC 对比表
    print("\n各维度组合 IC 对比:")
    pivot = results_df.pivot_table(
        index="组合", columns="周期", values="IC", aggfunc="first"
    )
    for col in ["ret_20d", "ret_40d", "ret_60d"]:
        if col in pivot.columns:
            pivot[col] = pivot[col].astype(float)

    pd.set_option("display.width", 120)
    pd.set_option("display.max_rows", 100)
    print(pivot.to_string(float_format=lambda x: f"{x:+.4f}"))

    # ── IC 排名 ──
    print("\n" + "=" * 60)
    print("各组合 60日 IC 排名 (Top 15):")
    ic60 = results_df[results_df["周期"] == "ret_60d"].sort_values("IC", ascending=False)
    for rank, (_, row) in enumerate(ic60.head(15).iterrows()):
        ic_val = row["IC"]
        if pd.isna(ic_val):
            continue
        bar = "█" * max(1, int(abs(ic_val) * 200))
        medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(rank, f"{rank+1}.")
        print(f"  {medal} {row['组合']:20s}  IC={ic_val:+.4f}  {bar}")

    # ── 五分位收益 (最优组合) ──
    best = ic60.iloc[0]
    best_name = best["组合"]
    print("\n" + "=" * 60)
    print(f"最优组合 [{best_name}] 五分位收益 (ret_60d):")
    col_name = f"combined_{best_name}"
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
        monotonic = "✅ 单调" if spread > 0 else "⚠️ 不单调"
        print(f"\n  Q5-Q1 spread: {spread:+.2f}%  {monotonic}")

    # ── 维度贡献分析 ──
    print("\n" + "=" * 60)
    print("各维度独立贡献 (单维度 60日IC):")
    for label, col in dim_cols.items():
        valid = df_sample.dropna(subset=[col, "ret_60d"])
        if len(valid) > 50:
            ic = valid[col].corr(valid["ret_60d"], method="spearman")
            if pd.isna(ic):
                print(f"  {label:15s}  IC=NaN (常数/无变化)")
                continue
            bar = "█" * max(1, int(abs(ic) * 200))
            print(f"  {label:15s}  IC={ic:+.4f}  {bar}")

    print("\n✅ 六维回测完成")

    engine.dispose()


if __name__ == "__main__":
    main()
