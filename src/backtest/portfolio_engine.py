"""
组合回测引擎 (精简版) — ADR-0009
==================================

原 portfolio_engine.py 732 行 → 精简 ~150 行薄封装。
内部委托给 PortfolioRunner (组合回测循环, 纯 Pandas/NumPy) + metrics。

公开 API 保留 (向后兼容):
- PortfolioBacktestEngine

使用示例:
    engine = PortfolioBacktestEngine()
    report = engine.run(
        strategy=SmallCapStrategy(),
        start_date=date(2022, 1, 1),
        end_date=date(2024, 12, 31),
        initial_capital=1_000_000,
    )
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from ..config import get_config
from .base_selection_strategy import BaseSelectionStrategy
from .data_loader import BacktestDataLoader
from .metrics import compute_metrics
from .portfolio_runner import PortfolioRunner
from .report import BacktestReport

logger = logging.getLogger(__name__)

__all__ = ["PortfolioBacktestEngine"]


class PortfolioBacktestEngine:
    """组合回测引擎薄封装 — 委托给 PortfolioRunner

    与 BacktestEngine 的区别:
    - BacktestEngine: 单股回测, 用 backtesting.py 第三方框架
    - PortfolioBacktestEngine: 组合选股回测, 纯 Pandas/NumPy 自实现

    使用示例:
        engine = PortfolioBacktestEngine()
        report = engine.run(
            strategy=SmallCapStrategy(),
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=1_000_000,
        )
    """

    def __init__(self):
        config = get_config()
        costs = config["backtest"]["costs"]
        self.commission = costs["commission_rate"]
        self.stamp_duty = costs["stamp_duty_rate"]
        self.slippage = costs.get("slippage", 0.0001)
        self.min_commission = costs.get("min_commission", 5.0)
        self.benchmark_code = config["backtest"]["benchmark"]
        self.risk_free_rate = config["backtest"]["risk_free_rate"]

        self.data_loader = BacktestDataLoader()
        self.runner = PortfolioRunner(
            data_loader=self.data_loader,
            commission=self.commission,
            stamp_duty=self.stamp_duty,
            slippage=self.slippage,
            min_commission=self.min_commission,
            benchmark_code=self.benchmark_code,
            risk_free_rate=self.risk_free_rate,
        )

    def run(self,
            strategy: BaseSelectionStrategy,
            start_date: date,
            end_date: date,
            initial_capital: float = 1_000_000,
            ) -> BacktestReport:
        """执行组合回测

        Args:
            strategy: 选股策略实例
            start_date, end_date: 回测起止日期
            initial_capital: 初始资金

        Returns:
            BacktestReport (stock_code="PORTFOLIO")
        """
        logger.info(
            f"组合回测: {strategy.name} | {start_date} ~ {end_date} | "
            f"资金={initial_capital:,.0f}"
        )

        equity_series, rebalance_details = self.runner.simulate(
            strategy=strategy,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
        )

        return self._build_report(
            strategy=strategy,
            equity_series=equity_series,
            rebalance_details=rebalance_details,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
        )

    def _build_report(
        self,
        strategy: BaseSelectionStrategy,
        equity_series: pd.Series,
        rebalance_details: list[dict],
        start_date: date,
        end_date: date,
        initial_capital: float,
    ) -> BacktestReport:
        """从净值曲线 + 调仓明细构建 BacktestReport"""
        if equity_series.empty:
            raise ValueError("净值曲线为空,无法生成报告")

        trading_days = len(equity_series)
        metrics = compute_metrics(
            equity_series=equity_series,
            initial_capital=initial_capital,
            trading_days=trading_days,
            risk_free=self.risk_free_rate,
        )

        benchmark_return = self.runner.calc_benchmark_return(start_date, end_date)
        excess_return = metrics["total_return"] - benchmark_return

        equity_curve = [
            {"date": str(dt.date()), "equity": round(eq, 2)}
            for dt, eq in equity_series.items()
        ]
        monthly_returns = self._calc_monthly_returns(equity_series)
        total_trades = len(rebalance_details)

        report = BacktestReport(
            strategy_name=strategy.name,
            stock_code="PORTFOLIO",
            stock_name=strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_equity=metrics["final_equity"],
            total_return=metrics["total_return"],
            annual_return=metrics["annual_return"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown=metrics["max_drawdown"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            total_trades=total_trades,
            annual_volatility=metrics["annual_volatility"],
            calmar_ratio=metrics["calmar_ratio"],
            benchmark_return=round(benchmark_return, 2),
            excess_return=round(excess_return, 2),
            equity_curve=equity_curve,
            trades_detail=rebalance_details,
            monthly_returns=monthly_returns,
            cost_config={
                "commission_rate": self.commission,
                "stamp_duty_rate": self.stamp_duty,
                "slippage": self.slippage,
                "min_commission": self.min_commission,
                "note": "组合回测: 等权配置,按实际换手金额计算(佣金+印花税+滑点)",
            },
        )
        logger.info(
            f"组合回测完成: 总收益={report.total_return:.2f}%, "
            f"夏普={report.sharpe_ratio:.2f}, 最大回撤={report.max_drawdown:.2f}%"
        )
        return report

    def _calc_monthly_returns(self, equity_series: pd.Series) -> dict[str, float]:
        """计算月度收益率"""
        if equity_series.empty:
            return {}
        monthly = {}
        for dt, eq in equity_series.items():
            key = f"{dt.year}-{dt.month:02d}"
            monthly[key] = round(float(eq), 2)
        return monthly