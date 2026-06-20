#!/usr/bin/env python3
"""
多维融合组合回测 — 快速验证版
==============================
链路: ScorerRegistry (4个评分器) → 选股 → 模拟组合净值 → 计算 Sharpe
速度: 仅取最近 5 个调仓日，快速验证完整链路
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url
from src.scoring import ScorerRegistry


def get_recent_weekly_dates(engine, n_dates=5):
    """获取最近 n 个调仓日（每周五）"""
    q = """
        SELECT DISTINCT trade_date FROM daily_price
        WHERE trade_date >= '2026-04-01'
        ORDER BY trade_date DESC
        LIMIT 30
    """
    df = pd.read_sql(q, engine)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # 取周五的日期
    weekly = df.groupby([df["trade_date"].dt.year, df["trade_date"].dt.isocalendar().week])["trade_date"].max()
    weekly = weekly.sort_values(ascending=False).head(n_dates)
    return [d.strftime("%Y-%m-%d") for d in weekly]


def get_universe(engine, as_of_date, min_amount_wan=3000):
    """获取当天股票池 (去除 ST、停牌、低流动性)"""
    q = """
        SELECT dp.code, dp.close, dp.trade_date, sb.name
        FROM daily_price dp
        LEFT JOIN stock_basic sb ON dp.code = sb.code
        WHERE dp.trade_date = :d
    """
    df = pd.read_sql(q, engine, params={"d": as_of_date})

    # 计算近 20 日均成交额
    q2 = """
        SELECT code, AVG(amount * 1e-4) AS avg_amount_wan
        FROM daily_price
        WHERE trade_date BETWEEN :start AND :end
        GROUP BY code
        HAVING avg_amount_wan >= :min_a
    """
    start_d = (pd.to_datetime(as_of_date) - timedelta(days=30)).strftime("%Y-%m-%d")
    df_amt = pd.read_sql(q2, engine, params={"start": start_d, "end": as_of_date, "min_a": min_amount_wan})

    df = df.merge(df_amt, on="code", how="inner")
    df = df[~df["name"].fillna("").str.contains("ST", na=False)]
    df = df[df["close"] > 0]
    return df


def simulate_portfolio(picks_by_date, price_data, start_d, end_d, initial=1_000_000, n_per_period=20):
    """
    模拟组合净值

    picks_by_date: {date: [code1, code2, ...]}
    price_data: DataFrame [code, trade_date, close]
    """
    # 数据预处理
    price_data = price_data.copy()
    price_data["trade_date"] = pd.to_datetime(price_data["trade_date"])
    price_data = price_data[(price_data["trade_date"] >= pd.Timestamp(start_d))
                           & (price_data["trade_date"] <= pd.Timestamp(end_d))]

    if price_data.empty:
        raise ValueError("没有价格数据")

    # 获取所有交易日
    all_trade_dates = sorted(price_data["trade_date"].unique())

    # pivot：code × date → close
    pivot = price_data.pivot(index="trade_date", columns="code", values="close").sort_index()
    pivot = pivot.ffill()  # 前向填充停牌

    # 调仓日升序
    rebalance_dates = sorted(picks_by_date.keys())
    print(f"  调仓日: {rebalance_dates}")

    # 净值计算
    cash = float(initial)
    holdings = {}  # code -> 持有股数
    equity_curve = []

    # 交易成本
    commission_rate = 0.0003
    stamp_duty_rate = 0.0005

    current_holding_set = set()

    for dt in all_trade_dates:
        # 检查是否调仓日（用下一个交易日的开盘价交易 - 简化用当日收盘价）
        is_rebalance = dt.strftime("%Y-%m-%d") in rebalance_dates

        if is_rebalance:
            target_codes = picks_by_date[dt.strftime("%Y-%m-%d")][:n_per_period]
        else:
            target_codes = list(current_holding_set)

        # 当前持仓市值
        if holdings:
            cur_value = 0
            for code, shares in holdings.items():
                if code in pivot.columns and not pd.isna(pivot.loc[dt, code]):
                    cur_value += shares * pivot.loc[dt, code]
            cash += cur_value
        else:
            cash = initial if dt == all_trade_dates[0] else cash

        # 清仓
        new_holdings = {}
        if target_codes and len(target_codes) > 0:
            valid_targets = [c for c in target_codes if c in pivot.columns and not pd.isna(pivot.loc[dt, c])]
            if valid_targets:
                target_per_stock = cash / len(valid_targets)
                for code in valid_targets:
                    price = pivot.loc[dt, code]
                    shares = target_per_stock / price
                    # 扣除买入手续费
                    cost = target_per_stock * commission_rate
                    cash -= cost
                    new_holdings[code] = shares

        # 卖出未持仓的（收印花税）
        for old_code in current_holding_set - set(new_holdings.keys()):
            if old_code in pivot.columns and not pd.isna(pivot.loc[dt, old_code]):
                sell_value = holdings.get(old_code, 0) * pivot.loc[dt, old_code]
                tax = sell_value * stamp_duty_rate
                cash -= tax

        holdings = new_holdings
        current_holding_set = set(new_holdings.keys())

        # 当日总市值 = 持仓 + 现金
        position_value = sum(shares * pivot.loc[dt, code] for code, shares in holdings.items()
                             if code in pivot.columns and not pd.isna(pivot.loc[dt, code]))
        total_equity = cash + position_value
        equity_curve.append({
            "date": dt.strftime("%Y-%m-%d"),
            "equity": total_equity,
            "cash": cash,
            "positions": len(holdings),
        })

    return pd.DataFrame(equity_curve)


def compute_sharpe(equity_df, risk_free=0.025):
    """计算 Sharpe / 最大回撤 / 年化收益"""
    df = equity_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    df["return"] = df["equity"].pct_change().fillna(0)

    n_days = (df["date"].max() - df["date"].min()).days
    n_years = max(n_days / 365.25, 0.01)

    total_ret = df["equity"].iloc[-1] / df["equity"].iloc[0] - 1
    ann_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
    vol = df["return"].std() * np.sqrt(252)
    sharpe = (ann_ret - risk_free) / vol if vol > 0 else 0

    # 最大回撤
    cummax = df["equity"].cummax()
    drawdown = (df["equity"] - cummax) / cummax
    max_dd = drawdown.min()

    return {
        "总收益": f"{total_ret*100:+.2f}%",
        "年化": f"{ann_ret*100:+.2f}%",
        "波动率": f"{vol*100:.2f}%",
        "夏普比率": round(sharpe, 3),
        "最大回撤": f"{max_dd*100:.2f}%",
        "交易日": len(df),
        "跨度": f"{n_days}天 ({n_years:.1f}年)",
    }


def main():
    print("=" * 70)
    print("多维融合组合回测 — 快速验证")
    print("=" * 70)

    engine = create_engine(get_db_url(get_config()), echo=False)

    # 1) 取 5 个最近调仓日
    dates = get_recent_weekly_dates(engine, n_dates=5)
    print(f"\n>>> 调仓日 ({len(dates)}个): {dates[0]} ~ {dates[-1]}")

    # 2) 初始化 4 个评分器
    print("\n>>> 初始化评分器")
    scorers = ScorerRegistry.get_enabled(
        ["technical", "fund_flow", "institutional", "news_event"],
        engine=engine
    )
    for name, s in scorers.items():
        print(f"  ✓ {name}: {type(s).__name__}")

    # 3) 每期评分 + 选股
    picks_by_date = {}
    for dt in dates:
        t0 = time.time()
        print(f"\n>>> 调仓日: {dt}")

        universe = get_universe(engine, dt, min_amount_wan=3000)
        codes = universe["code"].tolist()
        print(f"  股票池: {len(codes)} 只")

        if len(codes) < 50:
            print(f"  [SKIP] 股票池过小")
            continue

        # 多维评分
        score_df = pd.DataFrame({"code": codes})
        for name, scorer in scorers.items():
            try:
                t1 = time.time()
                batch = scorer.batch_score(codes, dt)
                if batch is None or batch.empty:
                    continue
                # 取加权分数
                if "weighted" in batch.columns:
                    score_df[f"{name}_score"] = batch["weighted"].values
                elif "score" in batch.columns:
                    score_df[f"{name}_score"] = batch["score"].values
                elapsed = time.time() - t1
                print(f"    {name}: {len(batch)} 条 ({elapsed:.1f}s)")
            except Exception as e:
                print(f"    {name}: ERROR {e}")

        # 多维融合（等权平均，缺失用中位值填充）
        score_cols = [c for c in score_df.columns if c.endswith("_score")]
        for c in score_cols:
            med = score_df[c].median()
            if pd.isna(med):
                med = 5.0  # 默认中性分
            score_df[c] = score_df[c].fillna(med)
        score_df["composite"] = score_df[score_cols].mean(axis=1)

        # 选 Top 30（实际持仓 20，留 buffer）
        top30 = score_df.nlargest(30, "composite")
        picks_by_date[dt] = top30["code"].tolist()
        print(f"  入选 Top30: {top30['composite'].mean():.2f} (avg composite)")
        print(f"  示例: {top30['code'].head(5).tolist()}")

        print(f"  调仓耗时: {time.time()-t0:.1f}s")

    if not picks_by_date:
        print("\n[ERROR] 没有生成选股结果")
        return

    # 4) 模拟组合
    print("\n" + "=" * 70)
    print(">>> 模拟组合净值")
    print("=" * 70)

    start_d = dates[-1]  # 最早调仓日
    end_d = (pd.Timestamp(dates[0]) + timedelta(days=60)).strftime("%Y-%m-%d")  # 跑 60 天

    q = "SELECT code, trade_date, close FROM daily_price WHERE trade_date BETWEEN :s AND :e"
    price_data = pd.read_sql(q, engine, params={"s": start_d, "e": end_d})
    print(f"  价格数据: {len(price_data)} 行 ({start_d} ~ {end_d})")

    equity_df = simulate_portfolio(picks_by_date, price_data, start_d, end_d,
                                    initial=1_000_000, n_per_period=20)

    # 5) 计算指标
    print("\n" + "=" * 70)
    print(">>> 绩效分析")
    print("=" * 70)
    metrics = compute_sharpe(equity_df)

    for k, v in metrics.items():
        print(f"  {k:12s}: {v}")

    # 6) 保存
    csv_path = PROJECT_ROOT / "data" / "multi_dim_portfolio_result.csv"
    equity_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ 净值曲线已保存: {csv_path}")

    # 7) 基准对比 (沪深300)
    try:
        bench_q = "SELECT trade_date, close FROM benchmark_data WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
        bench = pd.read_sql(bench_q, engine, params={"s": start_d, "e": end_d})
        if len(bench) > 0:
            bench["date"] = pd.to_datetime(bench["trade_date"])
            bench = bench.sort_values("date")
            bench["ret"] = bench["close"].pct_change().fillna(0)
            bench_cum = (1 + bench["ret"]).cumprod() - 1
            print(f"\n  同期沪深300: {bench_cum.iloc[-1]*100:+.2f}%")
    except Exception as e:
        print(f"  [基准对比失败] {e}")


if __name__ == "__main__":
    main()
