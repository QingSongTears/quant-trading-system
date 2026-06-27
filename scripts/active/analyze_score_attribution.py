#!/usr/bin/env python3
"""
评分归因分析 — 哪些维度支撑高回报？
======================================
从逐周采样数据中分析:
1. 各子指标对前向收益的独立预测力
2. 市场周期（牛/熊/震荡）下的差异化表现
3. 极端分位数对比（Top 10% vs Bottom 10%）
4. 子指标交叉组合的最优配置
5. 非线性关系检测
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import json
from datetime import datetime

# ============================================================
# 1. 加载数据
# ============================================================
print("=" * 70)
print("评分归因分析 — 哪些维度支撑高回报？")
print("=" * 70)

df = pd.read_csv("data/weekly_sample_scores.csv")
df = df.dropna(subset=["ret_20d", "ret_40d", "ret_60d"])
print(f"有效样本: {len(df)} 条")
print(f"日期范围: {df['as_of_date'].min()} ~ {df['as_of_date'].max()}")

sub_cols = [c for c in df.columns if c.startswith("tech_")]
sub_names = [c.replace("tech_", "") for c in sub_cols]

# ============================================================
# 2. 市场周期分类
# ============================================================
print("\n" + "=" * 70)
print("2. 市场周期拆分分析")
print("=" * 70)

# 用采样日平均收益判断市场状态
df["as_of_date"] = pd.to_datetime(df["as_of_date"])
daily_avg_ret = df.groupby("as_of_date")["ret_20d"].mean().reset_index()
daily_avg_ret = daily_avg_ret.sort_values("as_of_date")

# 滚动窗口判断牛熊：连续4周平均20日收益
daily_avg_ret["rolling_ret"] = daily_avg_ret["ret_20d"].rolling(4).mean()
daily_avg_ret["regime"] = "震荡"
daily_avg_ret.loc[daily_avg_ret["rolling_ret"] > 2.0, "regime"] = "牛市"
daily_avg_ret.loc[daily_avg_ret["rolling_ret"] < -2.0, "regime"] = "熊市"

regime_map = dict(zip(daily_avg_ret["as_of_date"], daily_avg_ret["regime"]))
df["regime"] = df["as_of_date"].map(regime_map).fillna("震荡")

regime_counts = df["regime"].value_counts()
for r in ["牛市", "熊市", "震荡"]:
    if r in regime_counts.index:
        subset = df[df["regime"] == r]
        print(f"\n{r} ({len(subset)} 样本, {regime_counts.get(r, 0)/len(df)*100:.0f}%)")
        print(f"  平均20日收益: {subset['ret_20d'].mean():.2f}%")
        print(f"  平均60日收益: {subset['ret_60d'].mean():.2f}%")
        print(f"  总分 vs ret_20d 相关系数: {subset['total'].corr(subset['ret_20d']):+.4f}")
        print(f"  总分 vs ret_60d 相关系数: {subset['total'].corr(subset['ret_60d']):+.4f}")

        # 各子指标在不同周期下的表现
        print(f"  {'子指标':<16}", end="")
        for h in [20, 40, 60]:
            print(f"  ret_{h}d", end="")
        print()
        for col in sub_cols:
            name = col.replace("tech_", "")
            print(f"  {name:<16}", end="")
            for h in [20, 40, 60]:
                corr = subset[col].corr(subset[f"ret_{h}d"])
                print(f"  {corr:>+7.4f}", end="")
            print()

# ============================================================
# 3. 极端分位数对比
# ============================================================
print("\n" + "=" * 70)
print("3. 极端分位数对比 — Top 10% vs Bottom 10% 收益")
print("=" * 70)

for horizon in [20, 40, 60]:
    ret_col = f"ret_{horizon}d"
    top_cut = df[ret_col].quantile(0.90)
    bot_cut = df[ret_col].quantile(0.10)

    top = df[df[ret_col] >= top_cut]
    bot = df[df[ret_col] <= bot_cut]

    print(f"\n{horizon}日前向收益:")
    print(f"  Top 10% (收益>{top_cut:.1f}%): {len(top)} 样本, 均值收益 {top[ret_col].mean():.1f}%")
    print(f"  Bot 10% (收益<{bot_cut:.1f}%): {len(bot)} 样本, 均值收益 {bot[ret_col].mean():.1f}%")

    print(f"  {'子指标':<16} {'Top10%':>8} {'Bot10%':>8} {'差值':>8} {'方向':>6}")
    print(f"  " + "-" * 48)
    for col in sub_cols:
        name = col.replace("tech_", "")
        tm = top[col].mean()
        bm = bot[col].mean()
        diff = tm - bm
        arrow = "★" if abs(diff) > 0.2 else ("↑" if diff > 0.1 else ("↓" if diff < -0.1 else "≈"))
        print(f"  {name:<16} {tm:>8.3f} {bm:>8.3f} {diff:>+8.3f} {arrow:>6}")

    t_total = top["total"].mean()
    b_total = bot["total"].mean()
    print(f"  {'total':<16} {t_total:>8.3f} {b_total:>8.3f} {t_total-b_total:>+8.3f}")

# ============================================================
# 4. 子指标单独预测力 — 控制其他变量
# ============================================================
print("\n" + "=" * 70)
print("4. 子指标独立预测力 (控制日期固定效应)")
print("=" * 70)

# 方法: 在每个采样日内，计算子指标与收益的 rank correlation
# 然后取所有日期的平均值。这消除了跨日期的市场beta影响。

for horizon in [20, 40, 60]:
    ret_col = f"ret_{horizon}d"
    print(f"\n{horizon}日收益 — 日均截面相关系数:")

    daily_corrs = {}
    for col in sub_cols:
        daily_corrs[col] = []

    for date, grp in df.groupby("as_of_date"):
        if len(grp) < 50:
            continue
        for col in sub_cols:
            corr = grp[col].corr(grp[ret_col])
            if not np.isnan(corr):
                daily_corrs[col].append(corr)

    print(f"  {'子指标':<16} {'均值corr':>10} {'t值':>8} {'显著':>6}")
    print(f"  " + "-" * 42)
    for col in sub_cols:
        name = col.replace("tech_", "")
        corrs = daily_corrs[col]
        if len(corrs) > 10:
            mean_corr = np.mean(corrs)
            se = np.std(corrs) / np.sqrt(len(corrs))
            t_stat = mean_corr / se if se > 0 else 0
            sig = "***" if abs(t_stat) > 2.58 else ("**" if abs(t_stat) > 1.96 else ("*" if abs(t_stat) > 1.65 else ""))
            print(f"  {name:<16} {mean_corr:>+10.5f} {t_stat:>+8.2f} {sig:>6}")

# ============================================================
# 5. 子指标最优组合
# ============================================================
print("\n" + "=" * 70)
print("5. 子指标得分组合分析")
print("=" * 70)

# 构建"高分组合"：在3个以上子指标得满分(3分)的股票
df["high_sub_count"] = (df[sub_cols] >= 3).sum(axis=1)
df["low_sub_count"] = (df[sub_cols] <= 0).sum(axis=1)

for horizon in [20, 40, 60]:
    ret_col = f"ret_{horizon}d"
    print(f"\n{horizon}日收益 — 按满分指标数量分组:")
    print(f"  {'满分指标数':<12} {'样本数':<8} {'平均收益%':>10} {'中位数收益%':>10}")
    for n in sorted(df["high_sub_count"].unique()):
        grp = df[df["high_sub_count"] == n]
        if len(grp) > 100:
            print(f"  {n:<12} {len(grp):<8} {grp[ret_col].mean():>10.2f} {grp[ret_col].median():>10.2f}")

# ============================================================
# 6. 回报率预测矩阵 — 各子指标得分 → 平均收益
# ============================================================
print("\n" + "=" * 70)
print("6. 回报率预测矩阵 (60日)")
print("=" * 70)

# 对每个子指标，展示得分0-3分别对应的平均60日收益
for col in sub_cols:
    name = col.replace("tech_", "")
    print(f"\n{name}:")
    print(f"  {'得分':<6} {'样本数':<8} {'60日收益均值%':>14} {'标准差':>10} {'胜率%':>8}")
    for score in range(4):
        grp = df[df[col] == score]
        if len(grp) > 50:
            win_rate = (grp["ret_60d"] > 0).mean() * 100
            print(f"  {score:<6} {len(grp):<8} {grp['ret_60d'].mean():>+14.2f} {grp['ret_60d'].std():>10.2f} {win_rate:>8.1f}")

# ============================================================
# 7. 非线性关系 — 子指标得分 vs 收益散点趋势
# ============================================================
print("\n" + "=" * 70)
print("7. 最佳单一维度: 胜率和收益最高的得分组合")
print("=" * 70)

best_combos = []
for col in sub_cols:
    name = col.replace("tech_", "")
    for score in range(4):
        grp = df[df[col] == score]
        if len(grp) > 100:
            best_combos.append({
                "dim": name,
                "score": score,
                "n": len(grp),
                "ret_60d": grp["ret_60d"].mean(),
                "win_rate": (grp["ret_60d"] > 0).mean(),
                "ret_20d": grp["ret_20d"].mean(),
            })

best_df = pd.DataFrame(best_combos).sort_values("ret_60d", ascending=False)
print(f"\n{'排名':<6} {'维度':<16} {'得分':<6} {'样本':<8} {'60日收益%':>10} {'胜率%':>8}")
print("-" * 56)
for i, row in best_df.head(15).iterrows():
    print(f"  {row.name+1:<6} {row['dim']:<16} {int(row['score']):<6} {int(row['n']):<8} {row['ret_60d']:>+10.2f} {row['win_rate']*100:>8.1f}")

print(f"\n{'排名':<6} {'维度':<16} {'得分':<6} {'样本':<8} {'60日收益%':>10} {'胜率%':>8} (最差)")
print("-" * 56)
for i, row in best_df.tail(10).iterrows():
    print(f"  {row.name+1:<6} {row['dim']:<16} {int(row['score']):<6} {int(row['n']):<8} {row['ret_60d']:>+10.2f} {row['win_rate']*100:>8.1f}")

# ============================================================
# 8. 总结和建议
# ============================================================
print("\n" + "=" * 70)
print("8. 综合结论")
print("=" * 70)

# 计算每个维度的综合预测力（跨3个时间周期的平均相关系数）
dim_power = {}
for col in sub_cols:
    name = col.replace("tech_", "")
    avg_corr = np.mean([df[col].corr(df[f"ret_{h}d"]) for h in [20, 40, 60]])
    dim_power[name] = avg_corr

# 排序
ranked = sorted(dim_power.items(), key=lambda x: x[1], reverse=True)
print("\n维度预测力排名 (跨20/40/60日平均相关系数):")
for i, (name, power) in enumerate(ranked):
    bar = "█" * int(abs(power) * 200)
    sign = "+" if power > 0 else ""
    print(f"  {i+1}. {name:<16} {sign}{power:.5f}  {bar}")

print("\n建议:")
print("  1. 正向维度 (保留): " + ", ".join([n for n, p in ranked if p > 0.005]))
print("  2. 中性维度 (需改进): " + ", ".join([n for n, p in ranked if -0.005 <= p <= 0.005]))
print("  3. 负向维度 (需反转或移除): " + ", ".join([n for n, p in ranked if p < -0.005]))

# 保存分析结果
df.to_csv("data/weekly_sample_analyzed.csv", index=False)
print("\n分析结果已保存到 data/weekly_sample_analyzed.csv")
