#!/usr/bin/env python3
"""
逐周采样脚本 — 长期训练数据生成
===================================
每周对所有可评分股票打分，计算前向N日收益，
用于分析哪些评分维度具有正的收益预测能力。

用法:
    # 全量采样（每周300只，全日期范围）
    python scripts/sample_weekly_scores.py

    # 快速测试（前10周）
    python scripts/sample_weekly_scores.py --test

    # 自定义参数
    python scripts/sample_weekly_scores.py --stocks-per-week 500 --start 2024-06-01 --end 2025-12-31

输出:
    data/weekly_sample_scores.csv     — 评分 + 前向收益
    data/weekly_sample_summary.json   — 汇总统计
"""
import sys
import os
import argparse
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from src.scoring.technical_scorer import TechnicalScorer


def get_weekly_dates(engine, start: str, end: str) -> list:
    """获取每周最后一个交易日"""
    query = f"""
        SELECT DISTINCT trade_date
        FROM daily_price
        WHERE trade_date >= '{start}' AND trade_date <= '{end}'
        ORDER BY trade_date
    """
    all_dates_raw = pd.read_sql(query, engine)["trade_date"].tolist()
    # Convert to proper datetime
    all_dates = pd.DatetimeIndex([pd.Timestamp(d) for d in all_dates_raw])

    # 按 ISO week 分组，取每周最大日期
    df = pd.DataFrame({"date": all_dates})
    df["week"] = df["date"].dt.isocalendar().week
    df["year"] = df["date"].dt.isocalendar().year
    weekly = df.groupby(["year", "week"])["date"].max().tolist()
    return sorted([str(d.date()) for d in weekly])


def get_eligible_codes(engine, sample_date: str, min_history: int = 60, limit: int = 300) -> list:
    """获取可评分股票（有足够历史数据），随机采样"""
    query = f"""
        SELECT code FROM daily_price
        WHERE trade_date = '{sample_date}'
        AND code IN (
            SELECT code FROM daily_price
            WHERE trade_date <= '{sample_date}'
            GROUP BY code HAVING COUNT(*) >= {min_history}
        )
        ORDER BY RANDOM()
        LIMIT {limit}
    """
    return pd.read_sql(query, engine)["code"].tolist()


def compute_forward_returns_bulk(
    engine, codes: list, sample_date: str, horizons: list = [20, 40, 60]
) -> dict:
    """批量计算前向收益

    Returns:
        {(code, horizon_days): return_pct, ...}
    """
    max_horizon = max(horizons)
    codes_str = "', '".join(codes)

    # 一次查询获取所有股票的前向价格
    query = f"""
        SELECT code, trade_date, close
        FROM daily_price
        WHERE code IN ('{codes_str}')
        AND trade_date > '{sample_date}'
        ORDER BY code, trade_date
    """
    df_fwd = pd.read_sql(query, engine)

    if df_fwd.empty:
        return {}

    # 获取入场价格
    entry_query = f"""
        SELECT code, close as entry_price
        FROM daily_price
        WHERE code IN ('{codes_str}')
        AND trade_date = '{sample_date}'
    """
    df_entry = pd.read_sql(entry_query, engine)
    entry_map = dict(zip(df_entry["code"], df_entry["entry_price"]))

    results = {}
    for code in codes:
        if code not in entry_map:
            continue
        entry_price = entry_map[code]
        if entry_price == 0:
            continue

        code_fwd = df_fwd[df_fwd["code"] == code]
        if code_fwd.empty:
            continue

        for horizon in horizons:
            fwd_prices = code_fwd.head(horizon)["close"].values
            if len(fwd_prices) >= horizon * 0.5 and entry_price > 0:
                exit_price = fwd_prices[-1]
                ret = (exit_price - entry_price) / entry_price * 100
                results[(code, horizon)] = ret

    return results


def main():
    parser = argparse.ArgumentParser(description="逐周采样评分脚本")
    parser.add_argument("--test", action="store_true", help="测试模式（仅10周）")
    parser.add_argument("--stocks-per-week", type=int, default=300)
    parser.add_argument("--start", default="2024-06-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--checkpoint", type=int, default=10, help="每N周保存中间结果")
    args = parser.parse_args()

    engine = create_engine("sqlite:///database/quant.db")
    scorer = TechnicalScorer(engine=engine)

    # 采样日期
    weekly_dates = get_weekly_dates(engine, args.start, args.end)
    if args.test:
        weekly_dates = weekly_dates[:10]

    print(f"=" * 65)
    print(f"逐周采样评分 — 潜伏反转模型 v2")
    print(f"=" * 65)
    print(f"采样周期: {weekly_dates[0]} ~ {weekly_dates[-1]} ({len(weekly_dates)} 周)")
    print(f"每周股票: ≤{args.stocks_per_week} 只")
    print(f"预计总样本: ≤{len(weekly_dates) * args.stocks_per_week} 条")
    print()

    all_results = []
    total_scored = 0
    start_time = datetime.now()

    for i, sample_date in enumerate(weekly_dates):
        week_start = datetime.now()

        # 获取可评分股票
        codes = get_eligible_codes(engine, sample_date, limit=args.stocks_per_week)
        if not codes:
            print(f"  [{i+1}/{len(weekly_dates)}] {sample_date}: 无可评分股票")
            continue

        # 批量加载价格数据
        bulk_data = scorer._load_bulk_price_data(codes, sample_date)

        if not bulk_data:
            continue

        # 批量计算前向收益
        forward_returns = compute_forward_returns_bulk(engine, list(bulk_data.keys()), sample_date)

        # 评分
        for code, df in bulk_data.items():
            sub = {
                "ma_trend": scorer._score_ma_trend(df),
                "macd": scorer._score_macd(df),
                "rsi": scorer._score_rsi(df),
                "bollinger": scorer._score_bollinger(df),
                "volume_price": scorer._score_volume_price(df),
                "breakout": scorer._score_breakout(df),
                "pullback": scorer._score_pullback(df),
            }
            total = sum(sub.values())
            weighted = round(total / 21 * 20, 1)

            record = {
                "code": code,
                "as_of_date": sample_date,
                "total": total,
                "weighted": weighted,
                **{f"tech_{k}": v for k, v in sub.items()},
                "ret_20d": forward_returns.get((code, 20)),
                "ret_40d": forward_returns.get((code, 40)),
                "ret_60d": forward_returns.get((code, 60)),
            }
            all_results.append(record)
            total_scored += 1

        elapsed = (datetime.now() - week_start).total_seconds()
        total_elapsed = (datetime.now() - start_time).total_seconds()

        # 检查点保存
        if (i + 1) % args.checkpoint == 0 or i == 0:
            df_checkpoint = pd.DataFrame(all_results)
            df_checkpoint.to_csv("data/weekly_sample_scores_checkpoint.csv", index=False)
            valid = df_checkpoint.dropna(subset=["ret_20d"])

            if len(valid) > 0:
                total_r20 = valid["total"].corr(valid["ret_20d"])
                print(f"  [{i+1}/{len(weekly_dates)}] {sample_date}: "
                      f"评分 {len(bulk_data)} 只 | "
                      f"累计 {total_scored} 条 | "
                      f"Corr(total,ret_20d)={total_r20:+.4f} | "
                      f"{elapsed:.1f}s")
            else:
                print(f"  [{i+1}/{len(weekly_dates)}] {sample_date}: "
                      f"评分 {len(bulk_data)} 只 | "
                      f"累计 {total_scored} 条 | "
                      f"{elapsed:.1f}s ({total_elapsed/60:.1f}min total)")

    # === 最终保存 ===
    df_final = pd.DataFrame(all_results)
    df_final.to_csv("data/weekly_sample_scores.csv", index=False)

    df_valid = df_final.dropna(subset=["ret_20d"])
    total_elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\n{'=' * 65}")
    print(f"采样完成!")
    print(f"{'=' * 65}")
    print(f"总样本: {len(df_final)} 条")
    print(f"有效样本: {len(df_valid)} 条 ({len(df_valid)/max(1,len(df_final))*100:.1f}%)")
    print(f"耗时: {total_elapsed/60:.1f} 分钟")

    # === 快速分析 ===
    if len(df_valid) > 100:
        print(f"\n--- 快速分析 ---")
        print(f"总分均值: {df_valid['total'].mean():.2f}")
        print(f"20日收益均值: {df_valid['ret_20d'].mean():.2f}%")
        print(f"40日收益均值: {df_valid['ret_40d'].mean():.2f}%")
        print(f"60日收益均值: {df_valid['ret_60d'].mean():.2f}%")

        # 分位数分析
        df_valid["score_q"] = pd.qcut(df_valid["total"], 4, labels=["Q1(低)", "Q2", "Q3", "Q4(高)"])
        print(f"\n总分分位 vs 前向收益:")
        print(f"{'分位':<10} {'数量':<6} {'20日%':>8} {'40日%':>8} {'60日%':>8}")
        for q in ["Q1(低)", "Q2", "Q3", "Q4(高)"]:
            g = df_valid[df_valid["score_q"] == q]
            print(f"{q:<10} {len(g):<6} {g['ret_20d'].mean():>8.2f} {g['ret_40d'].mean():>8.2f} {g['ret_60d'].mean():>8.2f}")

        print(f"\n子指标相关系数:")
        sub_cols = [c for c in df_valid.columns if c.startswith("tech_")]
        print(f"{'指标':<20} {'ret_20d':>8} {'ret_40d':>8} {'ret_60d':>8}")
        for col in sub_cols:
            name = col.replace("tech_", "")
            r20 = df_valid[col].corr(df_valid["ret_20d"])
            r40 = df_valid[col].corr(df_valid["ret_40d"])
            r60 = df_valid[col].corr(df_valid["ret_60d"])
            print(f"{name:<20} {r20:>+8.4f} {r40:>+8.4f} {r60:>+8.4f}")

    # 保存摘要
    summary = {
        "model_version": "v2-潜伏反转",
        "total_samples": len(df_final),
        "valid_samples": len(df_valid),
        "date_range": [weekly_dates[0], weekly_dates[-1]],
        "weeks_sampled": len(weekly_dates),
        "stocks_per_week": args.stocks_per_week,
        "elapsed_minutes": round(total_elapsed / 60, 1),
        "timestamp": datetime.now().isoformat(),
    }
    with open("data/weekly_sample_summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存:")
    print(f"  data/weekly_sample_scores.csv")
    print(f"  data/weekly_sample_summary.json")


if __name__ == "__main__":
    main()
