"""
回测引擎 (精简版) — ADR-0009
==============================

原 engine.py 809 行 → 精简 ~150 行薄封装。
内部委托给 BacktestRunner (单股回测循环) + optimizer (参数搜索)。

公开 API 保留 (向后兼容):
- BacktestEngine
- BacktestReport (re-export from .report)

使用示例:
    engine = BacktestEngine()
    report = engine.run(
        strategy_class=MACrossStrategy,
        stock_code="000001",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
    )
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable

from ..config import get_config
from .data_loader import BacktestDataLoader
from .metrics import compute_metrics
from .optimizer import (
    GridSearchOptimizer,
    RandomSearchOptimizer,
    BayesianSearchOptimizer,
    multi_metric_optimize as _multi_metric_optimize,
)
from .report import BacktestReport  # 公开 API: 仍可通过 engine 路径导入
from .runner import BacktestRunner

logger = logging.getLogger(__name__)

__all__ = ["BacktestEngine", "BacktestReport"]


class BacktestEngine:
    """回测引擎薄封装 — 委托给 BacktestRunner

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
        costs = config["backtest"]["costs"]
        self.commission = costs["commission_rate"]
        self.stamp_duty = costs["stamp_duty_rate"]
        self.slippage = costs.get("slippage", 0.0001)
        self.min_commission = costs.get("min_commission", 5.0)
        self.benchmark_code = config["backtest"]["benchmark"]
        self.risk_free_rate = config["backtest"]["risk_free_rate"]

        self.data_loader = BacktestDataLoader()
        self.runner = BacktestRunner(
            data_loader=self.data_loader,
            commission=self.commission,
            stamp_duty=self.stamp_duty,
            slippage=self.slippage,
            benchmark_code=self.benchmark_code,
            risk_free_rate=self.risk_free_rate,
        )

    def run(self,
            strategy_class: type,
            stock_code: str,
            start_date: date,
            end_date: date,
            initial_capital: float = 100000,
            strategy_params: dict[str, Any] | None = None,
            commission: float | None = None,
            stamp_duty: float | None = None,
            slippage: float | None = None,
            ) -> BacktestReport:
        """执行单次回测

        Args:
            strategy_class: 策略类(继承自 BaseStrategy)
            stock_code: 股票代码
            start_date, end_date: 回测起止日期
            initial_capital: 初始资金
            strategy_params: 策略参数覆盖
            commission, stamp_duty, slippage: 成本覆盖(None=用默认值)

        Returns:
            BacktestReport 标准化报告
        """
        _commission = commission if commission is not None else self.commission
        _stamp_duty = stamp_duty if stamp_duty is not None else self.stamp_duty
        _slippage = slippage if slippage is not None else self.slippage

        # 临时覆盖 runner 的成本(便于单次回测时调参)
        if commission is not None or stamp_duty is not None or slippage is not None:
            saved_commission = self.runner.commission
            saved_stamp = self.runner.stamp_duty
            saved_slip = self.runner.slippage
            self.runner.commission = _commission
            self.runner.stamp_duty = _stamp_duty
            self.runner.slippage = _slippage
            try:
                stats = self.runner.run_strategy(
                    strategy_class=strategy_class,
                    code=stock_code,
                    start=start_date,
                    end=end_date,
                    initial_capital=initial_capital,
                    strategy_params=strategy_params,
                )
            finally:
                self.runner.commission = saved_commission
                self.runner.stamp_duty = saved_stamp
                self.runner.slippage = saved_slip
        else:
            stats = self.runner.run_strategy(
                strategy_class=strategy_class,
                code=stock_code,
                start=start_date,
                end=end_date,
                initial_capital=initial_capital,
                strategy_params=strategy_params,
            )

        df = self.runner.load_ohlcv(stock_code, start_date, end_date)
        return self._build_report(
            strategy_class=strategy_class,
            stats=stats,
            df=df,
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission=_commission,
            stamp_duty=_stamp_duty,
            slippage=_slippage,
        )

    def _build_report(
        self,
        strategy_class: type,
        stats,
        df,
        stock_code: str,
        start_date: date,
        end_date: date,
        initial_capital: float,
        commission: float,
        stamp_duty: float,
        slippage: float,
    ) -> BacktestReport:
        """从 backtesting.py stats 构建 BacktestReport"""
        trading_days = len(df)
        total_return = (stats["Equity Final [$]"] / initial_capital - 1) * 100
        annual_return = self.runner.calc_annual_return_pct(total_return, trading_days)
        sharpe = self.runner.calc_sharpe(stats, initial_capital, stats["Equity Final [$]"], trading_days)
        max_dd = -abs(stats.get("Max. Drawdown [%]", 0))
        win_rate = stats.get("Win Rate [%]", 0)
        total_trades = stats.get("# Trades", 0)
        profit_factor = stats.get("Profit Factor", 0)
        annual_vol = self.runner.calc_annual_volatility(stats)
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0
        benchmark_return = self.runner.calc_benchmark_return(start_date, end_date)
        excess_return = total_return - benchmark_return

        equity_curve = self.runner.build_equity_curve(stats, df.index)
        trades_detail = self.runner.build_trades_detail(stats)
        monthly_returns = self.runner.build_monthly_returns(stats, df.index)

        stock_name = self.data_loader.lookup_stock_name(stock_code)

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
                "commission_rate": commission,
                "stamp_duty_rate": stamp_duty,
                "slippage": slippage,
                "trade_on_close": True,
                "note": "A股T+1模拟: trade_on_close=True; 涨跌停限制未模拟",
            },
        )
        logger.info(
            f"回测完成: 总收益={total_return:.2f}%, 夏普={sharpe:.2f}, 最大回撤={max_dd:.2f}%"
        )
        return report

    # ─────────────────────────────────────────────
    # 批量回测
    # ─────────────────────────────────────────────

    def run_batch(self,
                  strategy_classes: list[type],
                  stock_codes: list[str],
                  start_date: date,
                  end_date: date,
                  initial_capital: float = 100000,
                  max_workers: int = 4,
                  progress_callback: Callable | None = None,
                  ) -> list[BacktestReport]:
        """批量回测: 多策略 × 多只股票,并行执行"""
        tasks = []
        for sc in strategy_classes:
            for code in stock_codes:
                tasks.append((sc, code))

        total = len(tasks)
        reports = []
        errors = []
        done = 0

        def _run_one(sc, code):
            try:
                return self.run(
                    strategy_class=sc, stock_code=code,
                    start_date=start_date, end_date=end_date,
                    initial_capital=initial_capital,
                )
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_one, sc, code): (sc, code) for sc, code in tasks}
            for future in as_completed(futures):
                sc, code = futures[future]
                result = future.result()
                if isinstance(result, Exception):
                    errors.append((sc.name, code, str(result)))
                    logger.error(f"回测失败 [{sc.name} x {code}]: {result}")
                else:
                    reports.append(result)
                done += 1
                if progress_callback:
                    progress_callback(done, total)

        logger.info(f"批量回测完成: {len(reports)}/{total} 成功, {len(errors)} 失败")
        if errors:
            logger.info(f"失败明细(前10): {errors[:10]}")
        return reports

    def rank_batch_results(self,
                           reports: list[BacktestReport],
                           sort_by: str = "sharpe_ratio",
                           top_n: int | None = None,
                           ) -> list[dict]:
        """对批量回测结果排序并返回排名"""
        rows = [
            {
                "strategy": r.strategy_name,
                "stock": r.stock_code,
                "stock_name": r.stock_name,
                "total_return": r.total_return,
                "annual_return": r.annual_return,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "total_trades": r.total_trades,
                "annual_volatility": r.annual_volatility,
                "calmar_ratio": r.calmar_ratio,
                "benchmark_return": r.benchmark_return,
                "excess_return": r.excess_return,
            }
            for r in reports
        ]
        sorted_rows = sorted(rows, key=lambda x: x.get(sort_by, 0) or 0, reverse=True)
        for i, row in enumerate(sorted_rows):
            row["rank"] = i + 1
        return sorted_rows[:top_n] if top_n else sorted_rows

    # ─────────────────────────────────────────────
    # 参数搜索(委托给 optimizer.py)
    # ─────────────────────────────────────────────

    def _runner_factory(self):
        """给 optimizer 用的工厂 — 避免共享 runner 状态"""
        return BacktestRunner(
            data_loader=self.data_loader,
            commission=self.commission,
            stamp_duty=self.stamp_duty,
            slippage=self.slippage,
            benchmark_code=self.benchmark_code,
            risk_free_rate=self.risk_free_rate,
        )

    def run_grid_search(self,
                        strategy_class: type,
                        stock_code: str,
                        start_date: date,
                        end_date: date,
                        param_grid: dict[str, list],
                        metric: str = "total_return",
                        max_workers: int = 4,
                        ) -> list[dict]:
        """参数网格搜索(并行执行)"""
        optimizer = GridSearchOptimizer(runner_factory=self._runner_factory)
        return optimizer.run(
            strategy_class=strategy_class,
            code=stock_code,
            start=start_date,
            end=end_date,
            param_grid=param_grid,
            metric=metric,
            max_workers=max_workers,
        )

    def run_random_search(self,
                          strategy_class: type,
                          stock_code: str,
                          start_date: date,
                          end_date: date,
                          param_ranges: dict[str, tuple],
                          n_iter: int = 50,
                          metric: str = "total_return",
                          max_workers: int = 4,
                          ) -> list[dict]:
        """随机参数搜索"""
        optimizer = RandomSearchOptimizer(runner_factory=self._runner_factory)
        return optimizer.run(
            strategy_class=strategy_class,
            code=stock_code,
            start=start_date,
            end=end_date,
            param_ranges=param_ranges,
            n_iter=n_iter,
            metric=metric,
            max_workers=max_workers,
        )

    def run_bayesian_search(self,
                            strategy_class: type,
                            stock_code: str,
                            start_date: date,
                            end_date: date,
                            param_ranges: dict[str, tuple],
                            n_iter: int = 50,
                            n_initial: int = 10,
                            metric: str = "total_return",
                            max_workers: int = 4,
                            ) -> list[dict]:
        """简单贝叶斯搜索"""
        optimizer = BayesianSearchOptimizer(runner_factory=self._runner_factory)
        return optimizer.run(
            strategy_class=strategy_class,
            code=stock_code,
            start=start_date,
            end=end_date,
            param_ranges=param_ranges,
            n_iter=n_iter,
            n_initial=n_initial,
            metric=metric,
            max_workers=max_workers,
        )

    def multi_metric_optimize(self,
                              results: list[dict],
                              metrics: list[str] | None = None,
                              ) -> list[dict]:
        """多指标综合优化"""
        return _multi_metric_optimize(results, metrics)