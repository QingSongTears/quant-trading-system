"""
Mini 回测对比: tech-only vs tech+fund_flow
=========================================
- 用 SelectionPipeline 跑每日选股
- 每 20 个交易日调仓一次,等权
- 简单计算夏普/年化/回撤
- 对比 tech_only 和 tech+fund_flow 两组
"""
import sys
from pathlib import Path
from datetime import date, timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from src.selection import SelectionPipeline
from src.db.sql_utils import read_sql
from src.config import get_config, get_db_url
from sqlalchemy import create_engine


# ============================================================
#  数据准备
# ============================================================
def load_trade_dates(engine, start: str, end: str) -> list[str]:
    df = read_sql(
        """
        SELECT DISTINCT trade_date FROM daily_price
        WHERE trade_date >= :s AND trade_date <= :e
        ORDER BY trade_date
        """,
        engine,
        {"s": start, "e": end},
    )
    return [str(d)[:10] for d in df["trade_date"].tolist()]


def load_close_matrix(engine, codes: list[str], dates: list[str]) -> pd.DataFrame:
    """返回 close 矩阵 [date x code]"""
    placeholders = ",".join([f":c{i}" for i in range(len(codes))])
    params = {f"c{i}": c for i, c in enumerate(codes)}
    params["s"] = dates[0]
    params["e"] = dates[-1]
    sql = f"""
        SELECT trade_date, code, close FROM daily_price
        WHERE code IN ({placeholders})
          AND trade_date >= :s AND trade_date <= :e
    """
    df = read_sql(sql, engine, params)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    pivot = df.pivot(index="trade_date", columns="code", values="close").sort_index()
    return pivot


def select_at(pipe: SelectionPipeline, dt: str, top_n: int) -> list[str]:
    """在某个调仓日跑选股,返回股票代码列表"""
    try:
        df = pipe.run(dt, top_n=top_n)
        if df.empty:
            return []
        return df["code"].tolist()
    except Exception as e:
        print(f"  [WARN] {dt} 选股失败: {e}")
        return []


# ============================================================
#  净值模拟
# ============================================================
def simulate_nav(
    pipe: SelectionPipeline,
    trade_dates: list[str],
    rebalance_every: int,
    top_n: int,
    hold_days: int,
    close_matrix: pd.DataFrame,
    initial: float = 1_000_000,
) -> tuple[pd.Series, list[dict]]:
    """
    返回:
      - nav_series: 净值曲线 (pd.Series, index=date)
      - rebalance_log: 调仓日志 list[dict]
    """
    cash = initial
    holdings: dict[str, tuple[float, float]] = {}  # code -> (shares, buy_price)
    nav_history: list[tuple[str, float]] = []
    rebalance_log: list[dict] = []

    rebalance_idx = set(range(0, len(trade_dates), rebalance_every))

    for i, dt in enumerate(trade_dates):
        row = close_matrix.loc[dt] if dt in close_matrix.index else None

        # 1) 计算当前净值(现金 + 持仓市值)
        if row is not None:
            mkt_value = sum(
                sh * (row[c] if c in row.index and not pd.isna(row[c]) else bp)
                for c, (sh, bp) in holdings.items()
            )
        else:
            mkt_value = sum(sh * bp for c, (sh, bp) in holdings.items())
        nav = cash + mkt_value
        nav_history.append((dt, nav))

        # 2) 调仓日:清仓 + 重新选股
        if i in rebalance_idx:
            # 把总资产(cash + mkt_value)作为卖出后净额,再分配
            total_assets = cash + mkt_value
            sell_net = total_assets * (1 - 0.0005 - 0.0003)  # 印花税+佣金
            cash = sell_net
            holdings = {}

            # 选股
            picks = select_at(pipe, dt, top_n)
            if not picks or len(picks) < 5:
                rebalance_log.append({
                    "date": dt,
                    "picks": [],
                    "nav_before": nav,
                    "nav_after": cash,
                    "note": f"选股不足/失败 (n={len(picks)})",
                })
                continue

            # 等权买入(扣除买入成本)
            per_stock = cash / len(picks)
            buy_count = 0
            total_spent = 0.0
            for c in picks:
                if row is not None and c in row.index and not pd.isna(row[c]):
                    px = row[c]
                    # 佣金 0.03%
                    cost = px * 0.0003
                    effective_px = px + cost
                    shares = per_stock / effective_px
                    holdings[c] = (shares, px)
                    total_spent += shares * effective_px
                    buy_count += 1
            cash = cash - total_spent  # 剩余现金(零头)
            rebalance_log.append({
                "date": dt,
                "picks": picks,
                "nav_before": nav,
                "nav_after": cash + total_spent,
                "note": f"买入 {buy_count}/{len(picks)}",
            })

    nav_series = pd.Series(
        [v for _, v in nav_history],
        index=pd.to_datetime([d for d, _ in nav_history]),
    )
    return nav_series, rebalance_log


# ============================================================
#  统计指标
# ============================================================
def compute_metrics(nav: pd.Series, rf: float = 0.02) -> dict:
    rets = nav.pct_change().dropna()
    if rets.empty:
        return {"error": "无收益序列"}

    n_days = len(rets)
    annual_factor = 252

    annual_ret = (nav.iloc[-1] / nav.iloc[0]) ** (annual_factor / n_days) - 1
    daily_vol = rets.std()
    annual_vol = daily_vol * np.sqrt(annual_factor)
    sharpe = (annual_ret - rf) / annual_vol if annual_vol > 0 else 0

    cummax = nav.cummax()
    dd = (nav - cummax) / cummax
    max_dd = dd.min()

    # 胜率(日)
    win_rate = (rets > 0).mean()

    return {
        "年化收益": f"{annual_ret*100:.2f}%",
        "年化波动": f"{annual_vol*100:.2f}%",
        "夏普比率": f"{sharpe:.2f}",
        "最大回撤": f"{max_dd*100:.2f}%",
        "日胜率": f"{win_rate*100:.1f}%",
        "总交易日": n_days,
        "期末净值": f"{nav.iloc[-1]:.2f}",
    }


# ============================================================
#  主流程
# ============================================================
def main():
    config = get_config()
    engine = create_engine(get_db_url(config), echo=False)

    # 回测区间: 留 fund_flow 至少 6 个月预热期
    start = "2025-06-01"
    end = "2026-06-18"
    rebalance_every = 20  # 交易日
    top_n = 30
    hold_days = rebalance_every  # 显式 hold_days 提示用,实际由 simulate 处理

    print(f"回测区间: {start} ~ {end}")
    print(f"调仓周期: {rebalance_every} 交易日 / 持仓 {top_n} 只")

    trade_dates = load_trade_dates(engine, start, end)
    print(f"交易日数: {len(trade_dates)}")

    # 预加载 close 矩阵(全市场)
    print("\n加载全市场 close 矩阵...")
    all_codes_df = read_sql(
        "SELECT DISTINCT code FROM daily_price "
        "WHERE trade_date >= :s AND trade_date <= :e",
        engine, {"s": start, "e": end},
    )
    all_codes = all_codes_df["code"].tolist()
    print(f"全市场: {len(all_codes)} 只")
    close_matrix = load_close_matrix(engine, all_codes, trade_dates)
    print(f"close 矩阵: {close_matrix.shape}")

    # === 跑两组 ===
    scenarios = [
        ("tech_only", ["technical"]),
        ("tech+fund_flow", ["technical", "fund_flow"]),
    ]

    # DEBUG: 跑一次 tech+fund_flow 选股看输出
    print(f"\n{'='*60}")
    print("DEBUG: tech+fund_flow 第一次选股")
    print(f"{'='*60}")
    pipe_dbg = SelectionPipeline(dimensions=["technical", "fund_flow"], verbose=True)
    debug_dt = trade_dates[0]
    print(f"调仓日: {debug_dt}")
    dbg_df = pipe_dbg.run(debug_dt, top_n=top_n)
    if not dbg_df.empty:
        print(f"\n  选股 {len(dbg_df)} 只:")
        print(dbg_df[["code", "rank", "combined_score"]].head(10).to_string())
        print(f"\n  score_* 列: {[c for c in dbg_df.columns if c.startswith('score_')]}")
        # 检查这些股票在 close_matrix 里的覆盖
        picks = dbg_df["code"].tolist()
        valid = sum(1 for c in picks if c in close_matrix.columns)
        nan_count = sum(
            1 for c in picks
            if c in close_matrix.columns and close_matrix[c].iloc[:60].isna().all()
        )
        print(f"  选股中在 close_matrix 里有数据的: {valid}/{len(picks)}")
        print(f"  前 60 天全是 NaN 的: {nan_count}/{len(picks)}")
    pipe_dbg.clear_cache()

    results = {}
    for name, dims in scenarios:
        print(f"\n{'='*60}")
        print(f"场景: {name}  维度={dims}")
        print(f"{'='*60}")
        pipe = SelectionPipeline(dimensions=dims, verbose=False)
        nav, log = simulate_nav(
            pipe, trade_dates, rebalance_every, top_n, hold_days, close_matrix
        )
        metrics = compute_metrics(nav)
        results[name] = (nav, metrics, log)
        print(f"\n  调仓次数: {len(log)}")
        # 打印前几次调仓的 picks 看是否一样
        for entry in log[:3]:
            picks_str = ",".join(entry["picks"][:5]) + ("..." if len(entry["picks"]) > 5 else "")
            print(f"    {entry['date']}: {len(entry['picks'])} 只 [{picks_str}]")
        for k, v in metrics.items():
            print(f"  {k}: {v}")

    # === 对比 ===
    print(f"\n{'='*60}")
    print(f"对比结果")
    print(f"{'='*60}")
    print(f"{'指标':<12} {'tech_only':>14} {'tech+fund_flow':>18} {'提升':>10}")
    print("-" * 60)
    for key in ["年化收益", "年化波动", "夏普比率", "最大回撤", "期末净值"]:
        v1 = results["tech_only"][1].get(key, "N/A")
        v2 = results["tech+fund_flow"][1].get(key, "N/A")
        # 尝试数值计算 delta
        try:
            n1 = float(str(v1).rstrip("%"))
            n2 = float(str(v2).rstrip("%"))
            delta = f"{n2-n1:+.2f}"
        except Exception:
            delta = "—"
        print(f"{key:<12} {v1:>14} {v2:>18} {delta:>10}")

    print(f"\n{'='*60}")
    print("完成")


if __name__ == "__main__":
    main()