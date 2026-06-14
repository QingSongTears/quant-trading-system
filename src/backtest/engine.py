"""
回测引擎
=======

基于 Backtesting.py 封装，对接本地 SQLite 数据库。
支持:
- 单策略单股回测
- 批量回测（多策略 × 多股票）
- 参数网格搜索
"""
import json
import logging
from datetime import date
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

import pandas as pd
import numpy as np
from backtesting import Backtest

from ..config import get_config
from ..models.repository import DataRepository
from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestReport:
    """标准化回测报告"""
    strategy_name: str
    stock_code: str
    stock_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_equity: float
    total_return: float          # 总收益率(%)
    annual_return: float         # 年化收益率(%)
    sharpe_ratio: float          # 夏普比率
    max_drawdown: float          # 最大回撤(%)
    win_rate: float              # 胜率(%)
    profit_factor: float         # 盈亏比
    total_trades: int            # 总交易次数
    annual_volatility: float     # 年化波动率(%)
    calmar_ratio: float          # 卡玛比率

    # 基准对比
    benchmark_return: float = 0.0
    excess_return: float = 0.0

    # 序列化数据（供前端渲染图表）
    equity_curve: List[Dict] = None
    trades_detail: List[Dict] = None
    monthly_returns: Dict[str, float] = None

    # 成本配置快照
    cost_config: Dict = None

    def __post_init__(self):
        if self.equity_curve is None:
            self.equity_curve = []
        if self.trades_detail is None:
            self.trades_detail = []
        if self.monthly_returns is None:
            self.monthly_returns = {}
        if self.cost_config is None:
            self.cost_config = {}

    def to_dict(self) -> dict:
        """转为字典，用于 JSON 序列化和数据库存储"""
        d = asdict(self)
        d["start_date"] = str(d["start_date"])
        d["end_date"] = str(d["end_date"])
        d["equity_curve"] = json.dumps(d["equity_curve"])
        d["trades_detail"] = json.dumps(d["trades_detail"])
        d["monthly_returns"] = json.dumps(d["monthly_returns"])
        d["cost_config"] = json.dumps(d["cost_config"])
        return d


class BacktestEngine:
    """
    回测引擎
    
    使用示例:
        engine = BacktestEngine()
        report = engine.run(
            strategy_class=MACrossStrategy,
            stock_code="000001",
            start_date=date(2022, 6, 1),
            end_date=date(2025, 6, 1),
            initial_capital=100000
        )
    """

    def __init__(self):
        config = get_config()
        self.repo = DataRepository()
        self.repo.init_database()

        # 交易成本配置
        costs = config["backtest"]["costs"]
        self.commission = costs["commission_rate"]
        self.stamp_duty = costs["stamp_duty_rate"]
        self.slippage = costs["slippage"]

        # 基准指数
        self.benchmark_code = config["backtest"]["benchmark"]
        self.risk_free_rate = config["backtest"]["risk_free_rate"]

    def run(self,
            strategy_class: type,
            stock_code: str,
            start_date: date,
            end_date: date,
            initial_capital: float = 100000,
            strategy_params: Optional[Dict[str, Any]] = None,
            commission: Optional[float] = None,
            stamp_duty: Optional[float] = None,
            slippage: Optional[float] = None,
            ) -> BacktestReport:
        """
        执行单次回测
        
        Args:
            strategy_class: 策略类（继承自 BaseStrategy）
            stock_code: 股票代码
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_capital: 初始资金
            strategy_params: 策略参数覆盖
            commission: 佣金费率（覆盖默认值）
            stamp_duty: 印花税率（覆盖默认值）
            slippage: 滑点（覆盖默认值）
        
        Returns:
            BacktestReport 标准化报告
        """
        # 使用默认成本参数
        _commission = commission if commission is not None else self.commission
        _stamp_duty = stamp_duty if stamp_duty is not None else self.stamp_duty
        _slippage = slippage if slippage is not None else self.slippage

        # 获取数据
        df = self.repo.get_daily_data(stock_code, start_date, end_date)
        if df.empty:
            raise ValueError(f"股票 {stock_code} 在 [{start_date}, {end_date}] 范围内无数据")

        logger.info(f"回测: {strategy_class.name} x {stock_code} | {start_date} ~ {end_date} | 数据: {len(df)} 条")

        # 获取股票名称
        stock_name = ""
        try:
            stock_list = self.repo.get_stock_list()
            match = stock_list[stock_list["code"] == stock_code]
            if not match.empty:
                stock_name = match.iloc[0]["name"]
        except Exception:
            pass

        # 运行回测
        bt = Backtest(
            df,
            strategy_class,
            cash=initial_capital,
            commission=_commission,
            # Backtesting.py 不支持单独的印花税，将其计入 commission
            trade_on_close=True,  # 模拟 A 股 T+1
            hedging=False,
            exclusive_orders=True,
        )

        # 注入策略参数
        if strategy_params is None:
            strategy_params = {}

        stats = bt.run(**strategy_params)

        # 计算指标
        total_return = (stats["Equity Final [$]"] / initial_capital - 1) * 100
        trading_days = len(df)
        annual_return = self._calc_annual_return(total_return, trading_days)
        sharpe = self._calc_sharpe(df, initial_capital, stats["Equity Final [$]"], trading_days)
        max_dd = stats.get("Max. Drawdown [%]", 0) * -1 if stats.get("Max. Drawdown [%]", 0) < 0 else 0
        win_rate = stats.get("Win Rate [%]", 0)
        total_trades = stats.get("# Trades", 0)
        profit_factor = stats.get("Profit Factor", 0)
        annual_vol = self._calc_annual_volatility(df)
        calmar = annual_return / max_dd if max_dd > 0 else 0

        # 基准对比
        benchmark_return = self._calc_benchmark_return(start_date, end_date)
        excess_return = total_return - benchmark_return

        # 构建净值曲线
        equity_curve = self._build_equity_curve(stats, df.index)

        # 构建交易明细
        trades_detail = self._build_trades_detail(stats)

        # 月度收益率
        monthly_returns = self._calc_monthly_returns(stats, df.index)

        report = BacktestReport(
            strategy_name=strategy_class.name,
            stock_code=stock_code,
            stock_name=stock_name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_equity=stats["Equity Final [$]"],
            total_return=round(total_return, 2),
            annual_return=round(annual_return, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 2),
            win_rate=round(win_rate, 2) if win_rate else 0,
            profit_factor=round(profit_factor, 2) if profit_factor else 0,
            total_trades=total_trades or 0,
            annual_volatility=round(annual_vol, 2),
            calmar_ratio=round(calmar, 2),
            benchmark_return=round(benchmark_return, 2),
            excess_return=round(excess_return, 2),
            equity_curve=equity_curve,
            trades_detail=trades_detail,
            monthly_returns=monthly_returns,
            cost_config={
                "commission_rate": _commission,
                "stamp_duty_rate": _stamp_duty,
                "slippage": _slippage,
                "trade_on_close": True,
                "note": "A股T+1模拟: trade_on_close=True; 涨跌停限制未模拟"
            }
        )

        logger.info(f"回测完成: 总收益={total_return:.2f}%, 夏普={sharpe:.2f}, 最大回撤={max_dd:.2f}%")
        return report

    def run_batch(self,
                  strategy_classes: List[type],
                  stock_codes: List[str],
                  start_date: date,
                  end_date: date,
                  initial_capital: float = 100000,
                  ) -> List[BacktestReport]:
        """
        批量回测: 多个策略 × 多只股票
        """
        reports = []
        total = len(strategy_classes) * len(stock_codes)
        done = 0

        for strategy_class in strategy_classes:
            for code in stock_codes:
                try:
                    report = self.run(
                        strategy_class=strategy_class,
                        stock_code=code,
                        start_date=start_date,
                        end_date=end_date,
                        initial_capital=initial_capital,
                    )
                    reports.append(report)
                except Exception as e:
                    logger.error(f"回测失败 [{strategy_class.name} x {code}]: {e}")
                done += 1

        logger.info(f"批量回测完成: {len(reports)}/{total} 成功")
        return reports

    def run_grid_search(self,
                        strategy_class: type,
                        stock_code: str,
                        start_date: date,
                        end_date: date,
                        param_grid: Dict[str, list],
                        metric: str = "total_return",
                        ) -> List[Dict]:
        """
        参数网格搜索
        
        Args:
            param_grid: 如 {"fast_period": [3,5,10], "slow_period": [15,20,30]}
            metric: 优化目标指标
        
        Returns:
            按目标排序的参数组合列表
        """
        results = []

        # 生成所有参数组合
        import itertools
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        for combo in combinations:
            params = dict(zip(keys, combo))
            try:
                report = self.run(
                    strategy_class=strategy_class,
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    strategy_params=params,
                )
                result = {"params": params}
                result.update({
                    "total_return": report.total_return,
                    "sharpe_ratio": report.sharpe_ratio,
                    "max_drawdown": report.max_drawdown,
                    "win_rate": report.win_rate,
                    "total_trades": report.total_trades,
                })
                results.append(result)
            except Exception as e:
                logger.error(f"参数组合 {params} 失败: {e}")

        # 按目标排序
        results.sort(key=lambda x: x.get(metric, 0), reverse=True)
        return results

    # ===== 内部计算方法 =====

    def _calc_annual_return(self, total_return_pct: float, trading_days: int) -> float:
        """计算年化收益率"""
        if trading_days <= 0:
            return 0
        total_return_ratio = 1 + total_return_pct / 100
        years = trading_days / 250
        return ((total_return_ratio ** (1 / years)) - 1) * 100 if years > 0 else 0

    def _calc_sharpe(self, df: pd.DataFrame, initial: float, final: float, days: int) -> float:
        """计算夏普比率"""
        if days <= 1:
            return 0
        daily_returns = df["close"].pct_change().dropna()
        if len(daily_returns) == 0:
            return 0
        excess = daily_returns.mean() * 250 - self.risk_free_rate
        vol = daily_returns.std() * np.sqrt(250)
        return excess / vol if vol > 0 else 0

    def _calc_annual_volatility(self, df: pd.DataFrame) -> float:
        """计算年化波动率"""
        daily_returns = df["close"].pct_change().dropna()
        if len(daily_returns) == 0:
            return 0
        return daily_returns.std() * np.sqrt(250) * 100

    def _calc_benchmark_return(self, start: date, end: date) -> float:
        """计算基准（沪深300）同期收益率"""
        try:
            bench_df = self.repo.get_benchmark_data(self.benchmark_code, start, end)
            if bench_df.empty:
                return 0
            return (bench_df["close"].iloc[-1] / bench_df["close"].iloc[0] - 1) * 100
        except Exception:
            return 0

    def _build_equity_curve(self, stats, index) -> List[Dict]:
        """构建净值曲线数据"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return []
            curve_data = []
            # _equity_curve 是 pd.Series
            if hasattr(equity, 'values'):
                values = equity.values
                for i, val in enumerate(values):
                    curve_data.append({
                        "date": str(index[min(i, len(index) - 1)].date()),
                        "equity": float(val)
                    })
            return curve_data
        except Exception:
            return []

    def _build_trades_detail(self, stats) -> List[Dict]:
        """构建交易明细"""
        try:
            trades = stats.get("_trades", None)
            if trades is None or hasattr(trades, 'empty') and trades.empty:
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

    def _calc_monthly_returns(self, stats, index) -> Dict[str, float]:
        """计算月度收益率"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return {}
            monthly = {}
            for i, val in enumerate(equity.values):
                dt = index[min(i, len(index) - 1)]
                key = f"{dt.year}-{dt.month:02d}"
                if key not in monthly:
                    monthly[key] = float(val)
            return monthly
        except Exception:
            return {}
