"""
backtest 单股回测 runner — ADR-0009
====================================

封装第三方 `kernc/backtesting` 框架的 `Backtest` 类, 隔离第三方边界。

职责单一:
- BacktestRunner: 接收 strategy + data, 调 Backtest.run(), 产 BacktestReport
- 提供 OHLCV 列名转换(数据库小写 → backtesting.py 大写)
- 提供指标计算(sharpe / max_drawdown / volatility 等)
- 这是 `from backtesting import Backtest` 在 src/backtest/ 的**唯一引用点**

设计动机(ADR-0009 §1.3):
- 第三方 backtesting.py 边界明确隔离在 runner.py
- engine.py 仅依赖 runner.py, 不直接 import backtesting
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from .base_strategy import BaseStrategy
from .data_loader import BacktestDataLoader
from .metrics import compute_metrics

logger = logging.getLogger(__name__)


# backtesting.py 要求列名大写: Open/High/Low/Close/Volume
_OHLCV_RENAME_MAP = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


class BacktestRunner:
    """单股回测执行器 — backtesting.py 边界隔离

    使用示例:
        loader = BacktestDataLoader()
        runner = BacktestRunner(loader, risk_free_rate=0.02)
        stats = runner.run_strategy(
            strategy_class=MACrossStrategy,
            code="000001",
            start=date(2024, 1, 1),
            end=date(2024, 6, 1),
            initial_capital=100000,
        )
    """

    def __init__(
        self,
        data_loader: BacktestDataLoader | None = None,
        commission: float = 0.0003,
        stamp_duty: float = 0.0005,
        slippage: float = 0.0001,
        benchmark_code: str = "sh000300",
        risk_free_rate: float = 0.02,
    ):
        """初始化 Runner

        Args:
            data_loader: 数据加载器 (None=新建默认)
            commission: 佣金费率
            stamp_duty: 印花税率
            slippage: 滑点
            benchmark_code: 基准指数代码
            risk_free_rate: 年化无风险利率
        """
        self.data_loader = data_loader or BacktestDataLoader()
        self.commission = commission
        self.stamp_duty = stamp_duty
        self.slippage = slippage
        self.benchmark_code = benchmark_code
        self.risk_free_rate = risk_free_rate

    def load_ohlcv(self, code: str, start: date, end: date) -> pd.DataFrame:
        """加载 K 线数据并转为 backtesting.py 要求的大写列名格式

        Args:
            code: 股票代码
            start, end: 起止日期

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume (大写)
        """
        df = self.data_loader.load_bars(code, start, end)
        if df.empty:
            return df
        return df.rename(columns=_OHLCV_RENAME_MAP)

    def run_strategy(
        self,
        strategy_class: type,
        code: str,
        start: date,
        end: date,
        initial_capital: float = 100000,
        strategy_params: dict[str, Any] | None = None,
    ) -> dict:
        """执行单次回测, 返回 backtesting.py 的 stats 对象

        这是 backtesting.py 边界的核心 — 唯一调用 Backtest() 的地方。

        Args:
            strategy_class: 策略类 (BaseStrategy 子类)
            code: 股票代码
            start, end: 回测起止日期
            initial_capital: 初始资金
            strategy_params: 策略参数覆盖

        Returns:
            backtesting.py 的 stats (含 _equity_curve / _trades 等私有字段)
        """
        from backtesting import Backtest  # 第三方边界 — 仅此处引用

        df = self.load_ohlcv(code, start, end)
        if df.empty:
            raise ValueError(f"股票 {code} 在 [{start}, {end}] 范围内无数据")

        logger.info(
            f"回测: {strategy_class.name} x {code} | "
            f"{start} ~ {end} | 数据: {len(df)} 条"
        )

        bt = Backtest(
            df,
            strategy_class,
            cash=initial_capital,
            commission=self.commission,
            # A 股 T+1: 当日收盘成交, 次日才可卖
            trade_on_close=True,
            hedging=False,
            exclusive_orders=True,
        )

        if strategy_params is None:
            strategy_params = {}
        return bt.run(**strategy_params)

    # ─────────────────────────────────────────────
    # stats → 指标提取 (从原 engine.py 抽出)
    # ─────────────────────────────────────────────

    def extract_equity_series(self, stats: dict) -> pd.Series | None:
        """从 backtesting.py stats 中提取权益序列 (pd.Series)"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return None
            if hasattr(equity, "columns"):
                return equity.iloc[:, 0]
            elif hasattr(equity, "values"):
                return pd.Series(equity.values)
            return None
        except Exception:
            return None

    def extract_trades_dataframe(self, stats: dict) -> pd.DataFrame | None:
        """从 stats 中提取交易明细 DataFrame"""
        return stats.get("_trades", None)

    def calc_annual_return_pct(self, total_return_pct: float, trading_days: int) -> float:
        """年化收益率(%)"""
        if trading_days <= 0:
            return 0
        total_return_ratio = 1 + total_return_pct / 100
        years = trading_days / 250
        if years <= 0:
            return 0
        try:
            return ((total_return_ratio ** (1 / years)) - 1) * 100
        except (OverflowError, ValueError):
            return 0

    def calc_sharpe(self, stats: dict, initial: float, final: float, days: int) -> float:
        """计算夏普比率 — 基于策略权益曲线日收益率"""
        from .metrics import calc_sharpe_from_equity
        if days <= 1:
            return 0
        equity = self.extract_equity_series(stats)
        if equity is None or len(equity) < 2:
            return 0
        return calc_sharpe_from_equity(equity, risk_free=self.risk_free_rate)

    def calc_annual_volatility(self, stats: dict) -> float:
        """计算年化波动率 — 基于策略权益曲线日收益率"""
        from .metrics import calc_volatility_from_equity
        equity = self.extract_equity_series(stats)
        if equity is None or len(equity) < 2:
            return 0
        return calc_volatility_from_equity(equity)

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

    # ─────────────────────────────────────────────
    # stats → 曲线 / 交易明细 / 月度收益构建
    # ─────────────────────────────────────────────

    def build_equity_curve(self, stats: dict, index) -> list[dict]:
        """构建净值曲线数据(供 BacktestReport.equity_curve)"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return []
            if hasattr(equity, "columns"):
                values = equity.iloc[:, 0].values
            elif hasattr(equity, "values"):
                values = equity.values
            else:
                return []
            curve_data = []
            for i, val in enumerate(values):
                curve_data.append({
                    "date": str(index[min(i, len(index) - 1)].date()),
                    "equity": float(val),
                })
            return curve_data
        except Exception:
            return []

    def build_trades_detail(self, stats: dict) -> list[dict]:
        """构建交易明细"""
        try:
            trades = stats.get("_trades", None)
            if trades is None or (hasattr(trades, "empty") and trades.empty):
                return []
            details = []
            for _, t in trades.iterrows():
                details.append({
                    "size": int(t.get("Size", 0)),
                    "entry_price": float(t.get("EntryPrice", 0)),
                    "exit_price": float(t.get("ExitPrice", 0)),
                    "pnl": float(t.get("PnL", 0)),
                    "return_pct": float(t.get("ReturnPct", 0)),
                    "entry_date": str(t.get("EntryTime", "")),
                    "exit_date": str(t.get("ExitTime", "")),
                })
            return details
        except Exception:
            return []

    def build_monthly_returns(self, stats: dict, index) -> dict[str, float]:
        """计算月度收益率(取每月最后一个交易日的权益值)"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return {}
            if hasattr(equity, "columns"):
                values = equity.iloc[:, 0].values
            elif hasattr(equity, "values"):
                values = equity.values
            else:
                return {}
            monthly = {}
            for i, val in enumerate(values):
                dt = index[min(i, len(index) - 1)]
                key = f"{dt.year}-{dt.month:02d}"
                monthly[key] = float(val)
            return monthly
        except Exception:
            return {}