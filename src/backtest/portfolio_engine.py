"""
组合回测引擎
============

独立于 BacktestEngine，专用于选股策略的组合回测。
基于纯 Pandas/NumPy 实现，不依赖 backtesting.py 框架。

核心流程:
1. 从 SQLite 加载全市场 daily_price 数据
2. 为每只股票计算因子 (市值估算、波动率、换手率、收益率)
3. 每隔 N 个交易日执行一次选股 → 等权分配
4. 考虑交易成本 (佣金+印花税)
5. 生成净值曲线和统计指标，复用 BacktestReport 格式
"""
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import get_config
from ..models.repository import DataRepository
from .base_selection_strategy import BaseSelectionStrategy
from .engine import BacktestReport

logger = logging.getLogger(__name__)


class PortfolioBacktestEngine:
    """
    组合回测引擎 — 选股策略专用

    使用示例:
        engine = PortfolioBacktestEngine()
        report = engine.run(
            strategy=SmallCapStrategy(),
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=1000000,
        )
    """

    def __init__(self):
        config = get_config()
        self.repo = DataRepository()
        self.repo.init_database()

        # 交易成本
        costs = config["backtest"]["costs"]
        self.commission = costs["commission_rate"]   # 0.0003 (万三)
        self.stamp_duty = costs["stamp_duty_rate"]   # 0.0005 (千五，仅卖出)
        self.benchmark_code = config["backtest"]["benchmark"]
        self.risk_free_rate = config["backtest"]["risk_free_rate"]

    def run(self,
            strategy: BaseSelectionStrategy,
            start_date: date,
            end_date: date,
            initial_capital: float = 1000000,
            ) -> BacktestReport:
        """
        执行组合回测

        Args:
            strategy: 选股策略实例
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_capital: 初始资金

        Returns:
            BacktestReport (stock_code="PORTFOLIO")
        """
        logger.info(f"组合回测: {strategy.name} | {start_date} ~ {end_date} | 资金={initial_capital:,.0f}")

        # Step 1: 加载全市场数据
        all_data = self._load_all_data(start_date, end_date)
        if all_data.empty:
            raise ValueError(f"在 [{start_date}, {end_date}] 范围内无数据")

        # Step 2: 计算每个调仓日的因子和选股
        rebalance_dates = self._get_rebalance_dates(all_data, strategy.rebalance_days)
        logger.info(f"共 {len(rebalance_dates)} 个调仓日")

        # Step 3: 模拟组合净值曲线
        equity_curve, rebalance_details = self._simulate_portfolio(
            strategy, all_data, rebalance_dates, initial_capital, start_date, end_date
        )

        # Step 4: 计算统计指标
        report = self._build_report(
            strategy, equity_curve, rebalance_details,
            start_date, end_date, initial_capital
        )

        logger.info(
            f"组合回测完成: 总收益={report.total_return:.2f}%, "
            f"夏普={report.sharpe_ratio:.2f}, 最大回撤={report.max_drawdown:.2f}%"
        )
        return report

    # ===== 内部方法 =====

    def _load_all_data(self, start: date, end: date) -> pd.DataFrame:
        """加载全市场 daily_price 数据"""
        query = f"""
            SELECT dp.code, sb.name, dp.trade_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.amount, dp.pct_change, dp.turnover
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.trade_date >= '{start}'
              AND dp.trade_date <= '{end}'
            ORDER BY dp.code, dp.trade_date
        """
        df = pd.read_sql(query, self.repo.engine)
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    def _get_rebalance_dates(self, data: pd.DataFrame, rebalance_days: int) -> List[pd.Timestamp]:
        """获取调仓日列表 (每 rebalance_days 个交易日)"""
        all_dates = sorted(data["trade_date"].unique())
        return all_dates[::rebalance_days]

    def _build_universe(self, data: pd.DataFrame, as_of_date: pd.Timestamp,
                        lookback: int, strategy: BaseSelectionStrategy) -> pd.DataFrame:
        """
        为指定日期构建股票池 (含因子)

        Returns:
            DataFrame with columns: code, name, close, avg_amount_wan, avg_turnover,
            market_cap_yi, return_Nd, volatility_Nd
        """
        # 截至调仓日的数据
        cutoff_data = data[data["trade_date"] <= as_of_date].copy()

        # 每只股票最近 lookback 天的数据
        # 按股票分组，取最近 lookback 行
        recent = cutoff_data.groupby("code").tail(lookback)

        # 只保留有足够数据的股票 (至少 lookback/2 条)
        stock_counts = recent.groupby("code").size()
        valid_codes = stock_counts[stock_counts >= lookback // 2].index
        recent = recent[recent["code"].isin(valid_codes)]

        if recent.empty:
            return pd.DataFrame()

        # 计算因子
        agg_dict = {
            "name": "last",
            "close": "last",
            "amount": "mean",      # 日均成交额
            "turnover": "mean",     # 日均换手率
            "pct_change": ["mean", "std"],  # 收益率均值和标准差
        }

        factors = recent.groupby("code").agg(agg_dict).reset_index()
        factors.columns = [
            "code", "name", "close",
            "avg_amount", "avg_turnover",
            "avg_return", "volatility"
        ]

        # 日均成交额 (元→万元)
        factors["avg_amount_wan"] = factors["avg_amount"] / 10000

        # 估算总市值 (亿元) = 日均成交额(元) / 日均换手率(%) * 100 / 100000000
        # 市值 ≈ 成交额 / 换手率 * 100 (因为换手率是百分比)
        valid_turnover = factors["avg_turnover"] > 0.01  # 换手率 > 0.01% 避免除零
        factors.loc[valid_turnover, "market_cap_yi"] = (
            factors.loc[valid_turnover, "avg_amount"]
            / factors.loc[valid_turnover, "avg_turnover"]
            * 100 / 100000000
        )
        factors.loc[~valid_turnover, "market_cap_yi"] = np.nan

        # 近N日累计收益率
        factors["return_Nd"] = (
            factors["avg_return"] * lookback
        )

        # 近N日波动率 (标准差)
        factors["volatility_Nd"] = factors["volatility"]

        # 清理
        factors = factors.dropna(subset=["close", "avg_amount_wan"])
        factors = factors[factors["close"] > 0]

        return factors

    def _simulate_portfolio(self,
                           strategy: BaseSelectionStrategy,
                           all_data: pd.DataFrame,
                           rebalance_dates: List[pd.Timestamp],
                           initial_capital: float,
                           start_date: date,
                           end_date: date,
                           ) -> tuple:
        """
        模拟组合净值曲线

        Returns:
            (equity_series, rebalance_details)
            equity_series: pd.Series indexed by date, values = portfolio equity
            rebalance_details: list of dicts with rebalance info
        """
        # 全部交易日
        all_dates = sorted(all_data["trade_date"].unique())

        # 收益率 pivot: index=trade_date, columns=code, values=pct_change
        returns_pivot = all_data.pivot_table(
            index="trade_date", columns="code", values="pct_change", fill_value=0
        )
        # 将0替换为NaN再forward fill (停牌日收益率为0是合理的，但需要区分)
        # 简化处理: 缺失=0

        equity = initial_capital
        holdings: List[str] = []  # 当前持仓
        portfolio_equity = {}
        rebalance_details = []

        for dt in all_dates:
            if dt.date() < start_date or dt.date() > end_date:
                continue

            # 检查是否调仓日
            if dt in rebalance_dates:
                # 构建股票池
                universe = self._build_universe(all_data, dt, strategy.lookback_days, strategy)
                if not universe.empty:
                    # 过滤
                    filtered = strategy.filter_universe(universe)
                    # 选股
                    selected = strategy.select(dt, filtered)

                    # 调仓: 卖出旧持仓 + 买入新选股
                    if set(selected) != set(holdings):
                        # 计算交易成本
                        sell_cost = self._calc_transaction_cost(equity, is_sell=True) if holdings else 0
                        buy_cost = self._calc_transaction_cost(equity, is_sell=False)

                        equity -= (sell_cost + buy_cost)

                        rebalance_details.append({
                            "date": str(dt.date()),
                            "action": "rebalance",
                            "previous_holdings": holdings[:],
                            "new_holdings": selected[:],
                            "n_new": len(selected),
                            "equity_before_rebalance": equity + sell_cost + buy_cost,
                            "transaction_cost": sell_cost + buy_cost,
                        })

                        holdings = selected

            # 计算当日组合收益 (等权)
            if holdings:
                day_returns = []
                pct_row = returns_pivot.loc[returns_pivot.index == dt]
                if not pct_row.empty:
                    for code in holdings:
                        if code in pct_row.columns:
                            ret = pct_row[code].values[0]
                            if pd.notna(ret):
                                day_returns.append(ret / 100)
                            else:
                                day_returns.append(0)
                        else:
                            day_returns.append(0)
                else:
                    day_returns = [0] * len(holdings)

                avg_return = np.mean(day_returns) if day_returns else 0
                equity *= (1 + avg_return)

            portfolio_equity[dt] = equity

        equity_series = pd.Series(portfolio_equity)
        equity_series.index = pd.to_datetime(equity_series.index)
        return equity_series, rebalance_details

    def _calc_transaction_cost(self, equity: float, is_sell: bool = False) -> float:
        """
        计算全仓调仓的交易成本

        简化模型: 假设全仓换手
        买入: 佣金 (双边)
        卖出: 佣金 + 印花税
        """
        cost = equity * self.commission  # 佣金
        if is_sell:
            cost += equity * self.stamp_duty  # 印花税(仅卖出)
        return cost

    def _build_report(self,
                      strategy: BaseSelectionStrategy,
                      equity_series: pd.Series,
                      rebalance_details: List[Dict],
                      start_date: date,
                      end_date: date,
                      initial_capital: float,
                      ) -> BacktestReport:
        """构建回测报告"""
        if equity_series.empty:
            raise ValueError("净值曲线为空，无法生成报告")

        final_equity = equity_series.iloc[-1]
        total_return = (final_equity / initial_capital - 1) * 100

        # 年化收益
        trading_days = len(equity_series)
        years = trading_days / 250
        if years > 0 and total_return > -100:
            annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100
        else:
            annual_return = 0

        # 日收益率
        daily_returns = equity_series.pct_change().dropna()

        # 夏普比率
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = (daily_returns.mean() * 250 - self.risk_free_rate) / (daily_returns.std() * np.sqrt(250))
        else:
            sharpe = 0

        # 最大回撤
        cummax = equity_series.cummax()
        drawdown = (equity_series - cummax) / cummax * 100
        max_drawdown = drawdown.min()
        if max_drawdown > 0:
            max_drawdown = 0  # 没有回撤

        # 年化波动率
        annual_vol = daily_returns.std() * np.sqrt(250) * 100 if len(daily_returns) > 1 else 0

        # 胜率 (调仓后正收益的比例，简化为日收益率为正的比例)
        win_rate = (daily_returns > 0).sum() / len(daily_returns) * 100 if len(daily_returns) > 0 else 0

        # 卡玛比率
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

        # 盈亏比
        gains = daily_returns[daily_returns > 0]
        losses = daily_returns[daily_returns < 0]
        profit_factor = gains.mean() / abs(losses.mean()) if len(losses) > 0 and losses.mean() != 0 else 0

        # 基准收益
        benchmark_return = self._calc_benchmark_return(start_date, end_date)
        excess_return = total_return - benchmark_return

        # 净值曲线
        equity_curve = [
            {"date": str(dt.date()), "equity": round(eq, 2)}
            for dt, eq in equity_series.items()
        ]

        # 月度收益
        monthly_returns = self._calc_monthly_returns(equity_series)

        # 总交易次数 = 调仓次数
        total_trades = len(rebalance_details)

        report = BacktestReport(
            strategy_name=strategy.name,
            stock_code="PORTFOLIO",
            stock_name=strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_equity=round(final_equity, 2),
            total_return=round(total_return, 2),
            annual_return=round(annual_return, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_drawdown, 2),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2) if profit_factor else 0,
            total_trades=total_trades,
            annual_volatility=round(annual_vol, 2),
            calmar_ratio=round(calmar, 2),
            benchmark_return=round(benchmark_return, 2),
            excess_return=round(excess_return, 2),
            equity_curve=equity_curve,
            trades_detail=rebalance_details,
            monthly_returns=monthly_returns,
            cost_config={
                "commission_rate": self.commission,
                "stamp_duty_rate": self.stamp_duty,
                "note": "组合回测: 等权配置，全仓调仓时计算双边佣金+卖出印花税",
            }
        )
        return report

    def _calc_benchmark_return(self, start: date, end: date) -> float:
        """计算基准（沪深300）同期收益率"""
        try:
            bench_df = self.repo.get_benchmark_data(self.benchmark_code, start, end)
            if bench_df.empty:
                return 0
            return (bench_df["close"].iloc[-1] / bench_df["close"].iloc[0] - 1) * 100
        except Exception:
            return 0

    def _calc_monthly_returns(self, equity_series: pd.Series) -> Dict[str, float]:
        """计算月度收益率"""
        if equity_series.empty:
            return {}
        monthly = {}
        for dt, eq in equity_series.items():
            key = f"{dt.year}-{dt.month:02d}"
            monthly[key] = round(float(eq), 2)
        return monthly
