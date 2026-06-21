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
# PR2.2: 委托给 metrics.performance 单一实现 (消除 risk_free=0.025 硬编码)
from src.metrics import sharpe_ratio as _sharpe_ratio, max_drawdown as _max_drawdown, annual_return as _annual_return, volatility as _volatility


def get_recent_weekly_dates(engine, n_dates=8):
    """获取最近 n 个调仓日（每月取 2 个间隔 2 周）"""
    q = """
        SELECT DISTINCT trade_date FROM daily_price
        WHERE trade_date >= '2025-12-01'
        ORDER BY trade_date DESC
        LIMIT 120
    """
    df = pd.read_sql(q, engine)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # 每 2 周取一个（双周调仓）
    biweekly = df.iloc[::10].head(n_dates)
    return sorted([d.strftime("%Y-%m-%d") for d in biweekly["trade_date"]])


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
    slippage = 0.0001

    current_holding_set = set()

    for dt in all_trade_dates:
        # 当日收盘时计算净值（基于当前 holdings）
        position_value = sum(shares * pivot.loc[dt, code] for code, shares in holdings.items()
                             if code in pivot.columns and not pd.isna(pivot.loc[dt, code]))
        total_equity = cash + position_value
        equity_curve.append({
            "date": dt.strftime("%Y-%m-%d"),
            "equity": total_equity,
            "cash": cash,
            "positions": len(holdings),
        })

        # 是否调仓日（次日才用今天的价格调仓 — 简化：用当天收盘价做信号、当天调仓）
        is_rebalance = dt.strftime("%Y-%m-%d") in rebalance_dates
        if not is_rebalance:
            continue

        target_codes = picks_by_date[dt.strftime("%Y-%m-%d")][:n_per_period]

        # Step 1: 卖出所有现有持仓（按当天收盘价 + 滑点）
        if holdings:
            sell_proceeds = 0
            for code, shares in holdings.items():
                if code in pivot.columns and not pd.isna(pivot.loc[dt, code]):
                    sell_price = pivot.loc[dt, code] * (1 - slippage)
                    sell_value = shares * sell_price
                    tax = sell_value * stamp_duty_rate
                    sell_proceeds += sell_value - tax
                # 停牌卖不掉 → 继续持有
            cash += sell_proceeds

        # Step 2: 买入目标股票（按当天收盘价 + 滑点）
        valid_targets = [c for c in target_codes if c in pivot.columns and not pd.isna(pivot.loc[dt, c])]
        if not valid_targets:
            holdings = {}
            current_holding_set = set()
            continue

        # 预留少量现金（保留 2%）
        investable_cash = cash * 0.98
        budget_per_stock = investable_cash / len(valid_targets)
        new_holdings = {}
        total_buy_cost = 0
        total_invested = 0
        for code in valid_targets:
            price = pivot.loc[dt, code] * (1 + slippage)
            shares = budget_per_stock / price
            cost = budget_per_stock * commission_rate
            new_holdings[code] = shares
            total_invested += budget_per_stock
            total_buy_cost += cost

        # 现金扣减：投入本金 + 买入佣金
        cash = cash - total_invested - total_buy_cost
        holdings = new_holdings
        current_holding_set = set(new_holdings.keys())

    return pd.DataFrame(equity_curve)


def compute_sharpe(equity_df, risk_free=0.025):
    """计算 Sharpe / 最大回撤 / 年化收益 — PR2.2: 委托 metrics.performance"""
    df = equity_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    df["return"] = df["equity"].pct_change().fillna(0)

    n_days = (df["date"].max() - df["date"].min()).days
    n_years = max(n_days / 365.25, 0.01)

    total_ret = df["equity"].iloc[-1] / df["equity"].iloc[0] - 1
    # PR2.2: 用 metrics.performance 统一算法 (ann_factor=250 替代 sqrt(252))
    ann_ret = _annual_return(total_ret, n_days, ann_factor=365) / 100  # metrics 返回百分比,转回 decimal
    vol_pct = _volatility(df["return"].values, ann_factor=365)  # 百分比
    vol = vol_pct / 100  # 转回 decimal 用于 sharpe 计算
    sharpe = _sharpe_ratio(df["return"].values, risk_free=risk_free, ann_factor=365)

    # 最大回撤 (PR2.2: 委托 metrics)
    max_dd = _max_drawdown(df["equity"].values)

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

    # 1) 取 8 个最近调仓日
    dates = get_recent_weekly_dates(engine, n_dates=8)
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
    scores_by_date = {}  # 新增：每期每只股票各评分器分数

    # 检查是否有缓存
    import json
    cache_path = PROJECT_ROOT / "data" / "picks_by_date_cache.json"
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cached_dates = cached.get("dates", [])
        if cached_dates == dates:
            print(f"\n>>> 加载缓存选股 ({len(cached['picks'])} 期)")
            picks_by_date = cached["picks"]
            scores_by_date = cached.get("scores", {})
            for dt in dates:
                print(f"  {dt}: {len(picks_by_date[dt])} 只")
        else:
            print(f"\n>>> 缓存日期不匹配，需重新评分")
            cache_path.unlink(missing_ok=True)

    if not picks_by_date:
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
                        score_df[f"{name}_score"] = np.nan
                        continue
                    # 取加权分数
                    if "weighted" in batch.columns:
                        batch_score = batch[["code", "weighted"]].rename(columns={"weighted": f"{name}_score"})
                    elif "score" in batch.columns:
                        batch_score = batch[["code", "score"]].rename(columns={"score": f"{name}_score"})
                    else:
                        continue
                    batch_score["code"] = batch_score["code"].astype(str).str.zfill(6)
                    score_df["code"] = score_df["code"].astype(str).str.zfill(6)
                    # merge 避免长度不一致报错
                    score_df = score_df.drop(columns=[f"{name}_score"], errors="ignore").merge(batch_score, on="code", how="left")
                    elapsed = time.time() - t1
                    print(f"    {name}: {len(batch)} 条 ({elapsed:.1f}s)")
                except Exception as e:
                    print(f"    {name}: ERROR {e}")
                    score_df[f"{name}_score"] = np.nan

            # 多维融合（等权平均，缺失用中位值填充）
            score_cols = [c for c in score_df.columns if c.endswith("_score")]
            for c in score_cols:
                med = score_df[c].median()
                if pd.isna(med):
                    med = 5.0  # 默认中性分
                score_df[c] = score_df[c].fillna(med)
            score_df["composite"] = score_df[score_cols].mean(axis=1)

            # 缓存每只股票各维度分数
            scores_dict = score_df.set_index("code")[score_cols + ["composite"]].to_dict("index")
            scores_by_date[dt] = scores_dict

            # 选 Top 30（实际持仓 20，留 buffer）
            top30 = score_df.nlargest(30, "composite")
            picks_by_date[dt] = top30["code"].tolist()
            print(f"  入选 Top30: {top30['composite'].mean():.2f} (avg composite)")
            print(f"  示例: {top30['code'].head(5).tolist()}")

            print(f"  调仓耗时: {time.time()-t0:.1f}s")

        # 写缓存（含各评分器分数，用于多组合对比）
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({
                "dates": dates,
                "picks": picks_by_date,
                # 每个日期每只股票各评分器分数（来自最后一次 score_df）
                "scores": scores_by_date,
            }, f, ensure_ascii=False)
        print(f"\n  ✅ 选股结果已缓存: {cache_path}")

    if not picks_by_date:
        print("\n[ERROR] 没有生成选股结果")
        return

    # 4) 模拟组合
    print("\n" + "=" * 70)
    print(">>> 模拟组合净值")
    print("=" * 70)

    start_d = dates[0]  # 最早调仓日（升序后 dates[0] 是最早的）
    end_d = dates[-1]   # 最晚调仓日
    # 模拟窗口：从最早调仓日 - 5 天开始，到最晚调仓日 + 30 天结束（用于跟踪最后持仓）
    start_d_ext = (pd.Timestamp(start_d) - timedelta(days=5)).strftime("%Y-%m-%d")
    end_d_ext = (pd.Timestamp(end_d) + timedelta(days=30)).strftime("%Y-%m-%d")

    q = "SELECT code, trade_date, close FROM daily_price WHERE trade_date BETWEEN :s AND :e"
    price_data = pd.read_sql(q, engine, params={"s": start_d_ext, "e": end_d_ext})
    print(f"  价格数据: {len(price_data)} 行 ({start_d_ext} ~ {end_d_ext})")

    equity_df = simulate_portfolio(picks_by_date, price_data, start_d_ext, end_d_ext,
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

    # 8) 多组合对比（不同评分器组合、不同持仓数量）
    print("\n" + "=" * 70)
    print(">>> 多组合对比（不同评分器组合 / 持仓数）")
    print("=" * 70)

    def run_combo(combo_dims, n_hold=20, label=""):
        """对每个调仓日按指定维度重算综合分，选 Top n"""
        combo_picks = {}
        for dt in dates:
            scores = scores_by_date.get(dt, {})
            if not scores:
                combo_picks[dt] = picks_by_date.get(dt, [])
                continue
            # 按 combo_dims 计算 composite
            ranked = []
            for code, sc_dict in scores.items():
                vals = [sc_dict.get(f"{d}_score", np.nan) for d in combo_dims]
                # 缺失用中位
                vals = [v if not pd.isna(v) else 5.0 for v in vals]
                if vals:
                    ranked.append((code, np.mean(vals)))
            ranked.sort(key=lambda x: x[1], reverse=True)
            combo_picks[dt] = [c for c, _ in ranked[:30]]
        # 跑模拟
        eq = simulate_portfolio(combo_picks, price_data, start_d_ext, end_d_ext,
                                initial=1_000_000, n_per_period=n_hold)
        m = compute_sharpe(eq)
        m["组合"] = label
        return m

    combos = [
        (["technical"], 20, "单维度-技术"),
        (["fund_flow"], 20, "单维度-资金"),
        (["institutional"], 20, "单维度-机构"),
        (["news_event"], 20, "单维度-消息"),
        (["technical", "fund_flow"], 20, "双因子-技+资"),
        (["technical", "institutional"], 20, "双因子-技+机"),
        (["technical", "fund_flow", "institutional"], 20, "三因子-技+资+机"),
        (["technical", "fund_flow", "institutional", "news_event"], 20, "四因子-全"),
        (["technical", "fund_flow", "institutional", "news_event"], 10, "四因子-持仓10"),
        (["technical", "fund_flow", "institutional", "news_event"], 30, "四因子-持仓30"),
    ]

    combo_results = []
    for dims, n, label in combos:
        try:
            m = run_combo(dims, n, label)
            combo_results.append(m)
        except Exception as e:
            print(f"  [{label}] 失败: {e}")

    print(f"\n{'组合':<25s} {'总收益':>10s} {'年化':>10s} {'波动率':>8s} {'夏普':>8s} {'最大回撤':>10s}")
    print("-" * 75)
    for m in combo_results:
        print(f"  {m['组合']:<23s} {m['总收益']:>10s} {m['年化']:>10s} {m['波动率']:>8s} "
              f"{m['夏普比率']:>8} {m['最大回撤']:>10s}")


if __name__ == "__main__":
    main()
