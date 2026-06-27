"""
backtest 组合回测 runner — ADR-0009
====================================

从原 PortfolioBacktestEngine 抽出组合回测循环 + 因子计算 + 调仓模拟。

设计:
- 不依赖 backtesting.py (纯 Pandas/NumPy 自实现)
- 输入: BaseSelectionStrategy + data_loader + 时间区间
- 输出: pd.Series(净值曲线) + list[Dict](调仓明细)
- 由 portfolio_engine.py (薄封装) 调用

向量化优化保留:
  1) 调仓决策循环: 仅遍历调仓日 (~24 次) 而非全市场日
  2) 日收益矩阵: pivot 一次性生成 [date x code] 收益率矩阵
  3) 日均收益: 一次 .loc 抽取 + numpy.mean, 避免 Python 内层 for 循环

ADR-0010 (2026-06-27):
  - industry fallback 走 datafeed.get_industry_map (替代 DataRepository 直连)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .base_selection_strategy import BaseSelectionStrategy
from .data_loader import BacktestDataLoader, get_rebalance_dates

logger = logging.getLogger(__name__)


class PortfolioRunner:
    """组合回测执行器 — 选股策略专用

    使用示例:
        loader = BacktestDataLoader()
        runner = PortfolioRunner(loader, commission=0.001, stamp_duty=0.0005)
        equity, rebalance = runner.simulate(
            strategy=SmallCapStrategy(),
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            initial_capital=1_000_000,
        )
    """

    def __init__(
        self,
        data_loader: BacktestDataLoader | None = None,
        commission: float = 0.0003,
        stamp_duty: float = 0.0005,
        slippage: float = 0.0001,
        min_commission: float = 5.0,
        benchmark_code: str = "sh000300",
        risk_free_rate: float = 0.02,
    ):
        self.data_loader = data_loader or BacktestDataLoader()
        self.commission = commission
        self.stamp_duty = stamp_duty
        self.slippage = slippage
        self.min_commission = min_commission
        self.benchmark_code = benchmark_code
        self.risk_free_rate = risk_free_rate

    def calc_transaction_cost(
        self,
        turnover_amount: float,
        is_sell: bool = False,
        n_trades: int = 1,
    ) -> float:
        """计算调仓的交易成本(基于实际换手金额,不是全仓)

        成本构成:
        1. 佣金: turnover * commission_rate, 每笔最低 min_commission 元
        2. 印花税(仅卖出): turnover * stamp_duty_rate
        3. 滑点: turnover * slippage (假设每次成交滑点一致)
        """
        if turnover_amount <= 0 or n_trades <= 0:
            return 0.0
        commission_raw = turnover_amount * self.commission
        commission_min_floor = self.min_commission * n_trades
        commission = max(commission_raw, commission_min_floor)
        stamp = turnover_amount * self.stamp_duty if is_sell else 0.0
        slip = turnover_amount * self.slippage
        return commission + stamp + slip

    def apply_sector_constraint(
        self,
        selected_codes: list[str],
        universe_df: pd.DataFrame,
        strategy: BaseSelectionStrategy,
    ) -> list[str]:
        """T3.2: 行业暴露约束 (在调仓时调用)

        流程:
            1. 从 universe_df 取选中股票的行业映射 (若有 industry 列)
            2. 缺失时从 stock_basic.industry 批量加载
            3. 应用 SectorConstraint (max_pct + max_count)
            4. 候选不足时, 尝试从同行业被拒股票中按原评分补全
        """
        if not selected_codes:
            return selected_codes

        n_target = max(
            int(strategy.n_stocks * (strategy.sector_cap_pct or 1.0)), 1
        )
        if strategy.sector_max_count is not None:
            n_target = min(n_target, strategy.sector_max_count)

        from ..selection.sector_constraint import load_industry_map
        code_set = set(selected_codes)
        ind_map: dict[str, str] = {}

        has_real_industry = False

        if "industry" in universe_df.columns:
            for _, row in universe_df.iterrows():
                code_str = str(row["code"])
                if code_str in code_set:
                    ind_val = str(row.get("industry", "未知") or "未知")
                    ind_map[code_str] = ind_val
                    if ind_val != "未知":
                        has_real_industry = True
        missing = code_set - set(ind_map.keys())
        if missing:
            try:
                # ADR-0010 (2026-06-27): 改走 datafeed.get_industry_map (替代 DataRepository 直连)
                from src.data import data_mgr
                db_map = load_industry_map(data_mgr.datafeed, list(missing))
                ind_map.update(db_map)
                if any(v != "未知" for v in db_map.values()):
                    has_real_industry = True
            except Exception as e:
                logger.warning(f"apply_sector_constraint: datafeed 加载 industry 失败: {e}")

        for c in selected_codes:
            ind_map.setdefault(c, "未知")

        if not has_real_industry:
            return selected_codes[:strategy.n_stocks]

        counts: dict[str, int] = {}
        kept: list[str] = []
        rejected: list[str] = []
        for c in selected_codes:
            ind = ind_map.get(c, "未知")
            if counts.get(ind, 0) >= n_target:
                rejected.append(c)
                continue
            kept.append(c)
            counts[ind] = counts.get(ind, 0) + 1

        if len(kept) < strategy.n_stocks:
            pool = universe_df.copy()
            if "industry" not in pool.columns:
                pool_codes = pool["code"].astype(str).tolist()
                try:
                    # ADR-0010 (2026-06-27): 改走 datafeed.get_industry_map
                    from src.data import data_mgr
                    db_map = load_industry_map(data_mgr.datafeed, pool_codes)
                except Exception:
                    db_map = {}
                pool["industry"] = pool["code"].astype(str).map(db_map).fillna("未知")
            else:
                pool["industry"] = pool["industry"].fillna("未知")
            pool["code"] = pool["code"].astype(str)
            pool = pool.drop_duplicates(subset=["code"], keep="first")

            pool["__in_kept"] = pool["code"].isin(set(kept)).astype(int)
            pool["__order"] = pool["code"].map(
                {c: i for i, c in enumerate(selected_codes)}
            ).fillna(999)
            pool = pool.sort_values(
                ["__in_kept", "__order"], ascending=[False, True]
            )

            for _, row in pool.iterrows():
                if len(kept) >= strategy.n_stocks:
                    break
                c = row["code"]
                if c in kept:
                    continue
                ind = row.get("industry", "未知") or "未知"
                if counts.get(ind, 0) >= n_target:
                    continue
                kept.append(c)
                counts[ind] = counts.get(ind, 0) + 1

        return kept

    def simulate(
        self,
        strategy: BaseSelectionStrategy,
        start_date: date,
        end_date: date,
        initial_capital: float = 1_000_000,
    ) -> tuple[pd.Series, list[dict]]:
        """模拟组合净值曲线(向量化版本)

        T+1 语义:
          - holdings_history[i] = 在 Day i 持有的股票列表
          - 这些股票是在 Day i-1 调仓时选出来的 (T+1)
          - 调仓成本在调仓日当天、收益结算之后扣除

        Args:
            strategy: 选股策略实例
            start_date, end_date: 回测起止日期
            initial_capital: 初始资金

        Returns:
            (equity_series, rebalance_details)
            - equity_series: pd.Series, 索引为 trade_date
            - rebalance_details: list[dict], 每次调仓的明细
        """
        lookback_buffer = timedelta(days=strategy.lookback_days * 2)
        data_start = start_date - lookback_buffer
        all_data = self.data_loader.load_all_market_data(data_start, end_date)
        if all_data.empty:
            raise ValueError(f"在 [{data_start}, {end_date}] 范围内无数据")

        backtest_dates = sorted(
            all_data[all_data["trade_date"] >= pd.Timestamp(start_date)]["trade_date"].unique()
        )
        if len(backtest_dates) == 0:
            raise ValueError(f"在 [{start_date}, {end_date}] 范围内无交易日")

        rebalance_dates = get_rebalance_dates(all_data, strategy.rebalance_days)
        rebalance_dates = [d for d in rebalance_dates if d >= pd.Timestamp(start_date)]
        logger.info(f"共 {len(rebalance_dates)} 个调仓日 (回测区间内)")

        return self._simulate_portfolio(
            strategy, all_data, rebalance_dates, initial_capital, start_date, end_date
        )

    def _simulate_portfolio(
        self,
        strategy: BaseSelectionStrategy,
        all_data: pd.DataFrame,
        rebalance_dates: list[pd.Timestamp],
        initial_capital: float,
        start_date: date,
        end_date: date,
    ) -> tuple[pd.Series, list[dict]]:
        """内部: 模拟组合净值曲线(向量化版本)

        等价于原 PortfolioBacktestEngine._simulate_portfolio 主体逻辑
        """
        all_dates = sorted([
            d for d in all_data["trade_date"].unique()
            if start_date <= d.date() <= end_date
        ])
        if not all_dates:
            return pd.Series(dtype=float), []

        # === Pass 1: 决定每日持仓 (T+1 语义) ===
        holdings_today: list[str] = []
        holdings_history: list[list[str]] = []
        rebalance_details: list[dict] = []
        rebalance_set = set(rebalance_dates)

        for dt in all_dates:
            holdings_history.append(holdings_today[:])

            if dt in rebalance_set:
                universe = self.data_loader.build_universe_factors(
                    all_data, dt, strategy.lookback_days
                )
                if not universe.empty:
                    filtered = strategy.filter_universe(universe)
                    new_selected = strategy.select(dt, filtered)
                    if new_selected and (strategy.sector_cap_pct is not None
                                          or strategy.sector_max_count is not None):
                        new_selected = self.apply_sector_constraint(
                            new_selected, filtered, strategy
                        )
                else:
                    new_selected = holdings_today

                if set(new_selected) != set(holdings_today):
                    rebalance_details.append({
                        "date": str(dt.date()),
                        "action": "rebalance",
                        "previous_holdings": holdings_today[:],
                        "new_holdings": new_selected[:],
                        "n_new": len(new_selected),
                    })
                    holdings_today = new_selected

        # === Pass 2: 构建日收益矩阵 (一次性, 无循环) ===
        returns_pivot = (
            all_data
            .pivot_table(index="trade_date", columns="code",
                         values="pct_change", fill_value=0)
            / 100.0
        )
        all_codes = set(returns_pivot.columns)

        # === Pass 3: 向量化计算每日组合收益 ===
        n_days = len(all_dates)
        portfolio_returns = np.zeros(n_days)
        for i, dt in enumerate(all_dates):
            holdings = holdings_history[i]
            if not holdings:
                continue
            valid = [c for c in holdings if c in all_codes]
            if not valid or dt not in returns_pivot.index:
                continue
            portfolio_returns[i] = float(np.mean(returns_pivot.loc[dt, valid].values))

        # === Pass 4: 组合净值曲线 + 调仓成本 ===
        equity = initial_capital
        equity_curve = [equity]

        rebalance_change_indices: set[int] = set()
        for i in range(n_days - 1):
            if set(holdings_history[i]) != set(holdings_history[i + 1]):
                rebalance_change_indices.add(i)

        def _apply_cost_at(idx: int, base_equity: float):
            prev_holdings = holdings_history[idx]
            new_holdings = holdings_history[idx + 1] if idx + 1 < n_days else prev_holdings

            prev_set = set(prev_holdings)
            new_set = set(new_holdings)
            n_sold = len(prev_set - new_set)
            n_bought = len(new_set - prev_set)
            n_prev = len(prev_holdings)
            n_new = len(new_holdings)

            sell_amount = (n_sold / n_prev) * base_equity if n_prev and n_sold else 0.0
            buy_amount = (n_bought / n_new) * base_equity if n_new and n_bought else 0.0

            sell_cost = self.calc_transaction_cost(
                sell_amount, is_sell=True, n_trades=n_sold
            ) if n_sold else 0.0
            buy_cost = self.calc_transaction_cost(
                buy_amount, is_sell=False, n_trades=n_bought
            ) if n_bought else 0.0

            new_equity = base_equity - sell_cost - buy_cost

            if rebalance_details:
                last = rebalance_details[-1]
                if last.get("date") == str(all_dates[idx].date()):
                    last["equity_before_rebalance"] = base_equity
                    last["transaction_cost"] = sell_cost + buy_cost
                    last["turnover_pct"] = round(
                        (sell_amount + buy_amount) / base_equity * 100
                        if base_equity > 0 else 0, 2
                    )
            return new_equity

        if 0 in rebalance_change_indices:
            equity = _apply_cost_at(0, equity)

        for i in range(1, n_days):
            equity *= (1 + portfolio_returns[i])
            equity_curve.append(equity)
            if i in rebalance_change_indices:
                equity = _apply_cost_at(i, equity)

        equity_series = pd.Series(equity_curve, index=pd.to_datetime(all_dates))
        return equity_series, rebalance_details

    def calc_benchmark_return(self, start: date, end: date) -> float:
        """计算基准(沪深 300)同期收益率(%)"""
        from .metrics import calc_benchmark_return
        try:
            bench_df = self.data_loader.load_benchmark(self.benchmark_code, start, end)
            if bench_df.empty:
                return 0
            return calc_benchmark_return(bench_df["close"], start, end)
        except Exception:
            return 0