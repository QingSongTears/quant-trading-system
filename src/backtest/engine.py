"""
回测引擎
=======

基于 Backtesting.py 封装，对接本地 SQLite 数据库。
支持:
- 单策略单股回测
- 批量回测（多策略 × 多股票，并行执行）
- 参数网格/随机/贝叶斯搜索
"""
from __future__ import annotations
import json
import logging
import random
from datetime import date
from typing import Any, Callable
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
from backtesting import Backtest

from ..config import get_config
from ..models.repository import DataRepository
from ..metrics import sharpe_ratio as _sharpe_ratio, max_drawdown as _max_drawdown, volatility as _volatility, annual_return as _annual_return
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
    equity_curve: list[Dict] = None
    trades_detail: list[Dict] = None
    monthly_returns: dict[str, float] = None

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
        """转为字典，用于 JSON 序列化"""
        d = asdict(self)
        d["start_date"] = str(d["start_date"])
        d["end_date"] = str(d["end_date"])
        d["equity_curve"] = json.dumps(d["equity_curve"])
        d["trades_detail"] = json.dumps(d["trades_detail"])
        d["monthly_returns"] = json.dumps(d["monthly_returns"])
        d["cost_config"] = json.dumps(d["cost_config"])
        return d

    def to_db_dict(self, strategy_id: int) -> dict:
        """转为数据库存储格式（排除非DB字段）"""
        d = asdict(self)
        # 只保留 BacktestResult 表中存在的字段
        db_fields = {
            "stock_code", "start_date", "end_date", "initial_capital",
            "final_equity", "total_return", "annual_return", "sharpe_ratio",
            "max_drawdown", "win_rate", "profit_factor", "total_trades",
            "annual_volatility", "calmar_ratio", "benchmark_return",
            "excess_return", "equity_curve", "trades_detail",
            "monthly_returns", "cost_config"
        }
        result = {k: v for k, v in d.items() if k in db_fields}
        result["strategy_id"] = strategy_id
        result["stock_name"] = self.stock_name
        result["start_date"] = self.start_date
        result["end_date"] = self.end_date
        result["equity_curve"] = json.dumps(self.equity_curve) if self.equity_curve else "[]"
        result["trades_detail"] = json.dumps(self.trades_detail) if self.trades_detail else "[]"
        result["monthly_returns"] = json.dumps(self.monthly_returns) if self.monthly_returns else "{}"
        result["cost_config"] = json.dumps(self.cost_config) if self.cost_config else "{}"
        return result


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
        self.slippage = costs.get("slippage", 0.0001)  # 0.0001 (万分之一)
        self.min_commission = costs.get("min_commission", 5.0)  # ¥5 最低佣金

        # 基准指数
        self.benchmark_code = config["backtest"]["benchmark"]
        self.risk_free_rate = config["backtest"]["risk_free_rate"]

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

        # Backtesting.py 要求列名大写: Open/High/Low/Close/Volume
        df = df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        })

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
        sharpe = self._calc_sharpe(stats, initial_capital, stats["Equity Final [$]"], trading_days)
        max_dd = -abs(stats.get("Max. Drawdown [%]", 0))  # PR2.2: 统一为负数,与 metrics.performance.max_drawdown 一致
        win_rate = stats.get("Win Rate [%]", 0)
        total_trades = stats.get("# Trades", 0)
        profit_factor = stats.get("Profit Factor", 0)
        annual_vol = self._calc_annual_volatility(stats)
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0

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

    # ──────────────────────────────────────────────
    # 批量回测
    # ──────────────────────────────────────────────

    def run_batch(self,
                  strategy_classes: list[type],
                  stock_codes: list[str],
                  start_date: date,
                  end_date: date,
                  initial_capital: float = 100000,
                  max_workers: int = 4,
                  progress_callback: Callable | None = None,
                  ) -> list[BacktestReport]:
        """
        批量回测: 多策略 × 多只股票，并行执行

        Args:
            strategy_classes: 策略类列表
            stock_codes: 股票代码列表
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_capital: 初始资金
            max_workers: 并行工作线程数
            progress_callback: 进度回调 fn(done, total)

        Returns:
            按 (策略, 股票) 排列的报告列表
        """
        tasks = []
        for strategy_class in strategy_classes:
            for code in stock_codes:
                tasks.append((strategy_class, code))

        total = len(tasks)
        reports = []
        errors = []
        done = 0

        def _run_one(strategy_class, code):
            try:
                r = self.run(
                    strategy_class=strategy_class,
                    stock_code=code,
                    start_date=start_date,
                    end_date=end_date,
                    initial_capital=initial_capital,
                )
                return r
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
                           ) -> list[Dict]:
        """
        对批量回测结果排序并返回排名

        Args:
            reports: run_batch 返回的报告列表
            sort_by: 排序指标 (sharpe_ratio / total_return / annual_return / max_drawdown / calmar_ratio)
            top_n: 只返回前 N 条 (None=全部)

        Returns:
            带排名+核心指标的列表, 每项格式:
            {"rank":1, "strategy":"xx", "stock":"000001", "stock_name":"xx",
             "total_return":12.3, "sharpe_ratio":1.2, ...}
        """
        rows = []
        for r in reports:
            rows.append({
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
            })

        # 排序: 指标值越大越好 (max_drawdown 是负数，绝对值越小越好 → 按最大排序)
        reverse = True
        sorted_rows = sorted(rows, key=lambda x: x.get(sort_by, 0) or 0, reverse=reverse)
        for i, row in enumerate(sorted_rows):
            row["rank"] = i + 1

        return sorted_rows[:top_n] if top_n else sorted_rows

    # ──────────────────────────────────────────────
    # 参数搜索
    # ──────────────────────────────────────────────

    def run_grid_search(self,
                        strategy_class: type,
                        stock_code: str,
                        start_date: date,
                        end_date: date,
                        param_grid: dict[str, list],
                        metric: str = "total_return",
                        max_workers: int = 4,
                        ) -> list[Dict]:
        """
        参数网格搜索（并行执行）

        Args:
            param_grid: 如 {"fast_period": [3,5,10], "slow_period": [15,20,30]}
            metric: 优化目标指标
            max_workers: 并行工作线程数

        Returns:
            按 metric 降序排列的参数组合列表
        """
        import itertools
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        results = []
        completed = 0
        total = len(combinations)

        def _run_one(combo):
            params = dict(zip(keys, combo))
            try:
                report = self.run(
                    strategy_class=strategy_class,
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    strategy_params=params,
                )
                return params, {
                    "total_return": report.total_return,
                    "sharpe_ratio": report.sharpe_ratio,
                    "max_drawdown": report.max_drawdown,
                    "win_rate": report.win_rate,
                    "total_trades": report.total_trades,
                    "annual_return": report.annual_return,
                    "calmar_ratio": report.calmar_ratio,
                }
            except Exception as e:
                return params, {"error": str(e)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_one, combo): combo for combo in combinations}
            for future in as_completed(futures):
                combo = futures[future]
                params, metrics = future.result()
                if "error" not in metrics:
                    results.append({"params": params, **metrics})
                else:
                    logger.warning(f"参数组合 {params} 失败: {metrics['error']}")
                completed += 1

        results.sort(key=lambda x: x.get(metric, 0) or 0, reverse=True)
        return results

    def run_random_search(self,
                          strategy_class: type,
                          stock_code: str,
                          start_date: date,
                          end_date: date,
                          param_ranges: dict[str, tuple],
                          n_iter: int = 50,
                          metric: str = "total_return",
                          max_workers: int = 4,
                          ) -> list[Dict]:
        """
        随机参数搜索: 从参数范围中随机采样 n_iter 组

        Args:
            param_ranges: {"param_name": (min, max, type)}
                         type: "int" / "float" / "categorical"
                         如 {"fast_period": (3, 30, "int"), "threshold": (0.5, 2.0, "float")}
            n_iter: 采样次数

        Returns:
            按 metric 降序排列的参数组合列表
        """
        param_keys = list(param_ranges.keys())
        results = []

        def _sample_params():
            params = {}
            for k, (lo, hi, pt) in param_ranges.items():
                if pt == "int":
                    params[k] = random.randint(int(lo), int(hi))
                elif pt == "float":
                    params[k] = round(random.uniform(float(lo), float(hi)), 4)
                elif pt == "categorical":
                    params[k] = random.choice(hi)  # hi is a list
            return params

        # 预生成所有样本
        samples = [_sample_params() for _ in range(n_iter)]

        def _run_one(params):
            try:
                report = self.run(
                    strategy_class=strategy_class,
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    strategy_params=params,
                )
                return params, {
                    "total_return": report.total_return,
                    "sharpe_ratio": report.sharpe_ratio,
                    "max_drawdown": report.max_drawdown,
                    "win_rate": report.win_rate,
                    "total_trades": report.total_trades,
                    "annual_return": report.annual_return,
                    "calmar_ratio": report.calmar_ratio,
                }
            except Exception as e:
                return params, {"error": str(e)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_one, s): s for s in samples}
            for future in as_completed(futures):
                params, metrics = future.result()
                if "error" not in metrics:
                    results.append({"params": params, **metrics})

        results.sort(key=lambda x: x.get(metric, 0) or 0, reverse=True)
        return results

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
                            ) -> list[Dict]:
        """
        简单贝叶斯搜索 (基于随机+优化重采样)
        使用高斯过程代理模型引导采样，不依赖外部 GP 库。
        当参数维度 ≤ 2 且 n_iter 较小时效果较好。

        Args:
            param_ranges: 同 run_random_search
            n_iter: 总迭代次数 (含初始随机采样)
            n_initial: 初始随机采样次数
        """
        param_keys = list(param_ranges.keys())

        def _sample_params():
            params = {}
            for k, (lo, hi, pt) in param_ranges.items():
                if pt == "int":
                    params[k] = random.randint(int(lo), int(hi))
                elif pt == "float":
                    params[k] = round(random.uniform(float(lo), float(hi)), 4)
                elif pt == "categorical":
                    params[k] = random.choice(hi)
            return params

        # Phase 1: 初始化随机采样
        samples = [_sample_params() for _ in range(n_initial)]
        evaluated = []

        def _eval(params):
            try:
                report = self.run(
                    strategy_class=strategy_class,
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    strategy_params=params,
                )
                return params, report
            except Exception as e:
                return params, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_eval, s): s for s in samples}
            for future in as_completed(futures):
                params, report = future.result()
                if report is not None:
                    evaluated.append((params, report))

        # Phase 2: 基于已知结果做"贪婪细化"
        # 对 top-3 参数组合做邻域随机扰动
        evaluated.sort(key=lambda x: getattr(x[1], metric, 0) or 0, reverse=True)
        remaining = n_iter - n_initial

        if remaining > 0 and evaluated:
            for _ in range(remaining):
                # 从 top-3 中随机选一个做扰动
                top_idx = random.randint(0, min(2, len(evaluated) - 1))
                base_params, _ = evaluated[top_idx]
                new_params = {}
                for k, (lo, hi, pt) in param_ranges.items():
                    if pt == "int":
                        delta = int((hi - lo) * random.gauss(0, 0.15))
                        new_params[k] = max(int(lo), min(int(hi), base_params[k] + delta))
                    elif pt == "float":
                        scale = (hi - lo) * 0.15
                        new_params[k] = round(
                            max(float(lo), min(float(hi), base_params[k] + random.gauss(0, scale))), 4
                        )
                    elif pt == "categorical":
                        new_params[k] = random.choice(hi)
                # 不去重（不影响）

                report = self.run(
                    strategy_class=strategy_class,
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    strategy_params=new_params,
                )
                evaluated.append((new_params, report))

        # 汇总结果
        results = []
        for params, report in evaluated:
            results.append({
                "params": params,
                "total_return": report.total_return,
                "sharpe_ratio": report.sharpe_ratio,
                "max_drawdown": report.max_drawdown,
                "win_rate": report.win_rate,
                "total_trades": report.total_trades,
                "annual_return": report.annual_return,
                "calmar_ratio": report.calmar_ratio,
            })

        results.sort(key=lambda x: x.get(metric, 0) or 0, reverse=True)
        return results

    def multi_metric_optimize(self,
                              results: list[Dict],
                              metrics: list[str] = None,
                              ) -> list[Dict]:
        """
        多指标综合优化 (加权评分 + Pareto 前沿)
        适用于 run_grid_search / run_random_search 的结果

        Args:
            results: 搜索返回的结果列表
            metrics: 要纳入优化的指标列表, 格式 "metric:weight"
                     如 ["sharpe_ratio:0.5", "total_return:0.2", "max_drawdown:0.3"]
                     权重和为 1

        Returns:
            综合评分排序后的结果 (每项增加 composite_score 字段)
        """
        if metrics is None:
            metrics = ["sharpe_ratio:0.5", "total_return:0.3", "max_drawdown:0.2"]

        parsed = []
        for m in metrics:
            parts = m.split(":")
            parsed.append((parts[0], float(parts[1]) if len(parts) > 1 else 1.0))

        # 归一化每项指标到 [0,1]
        scores_dict = {}
        for metric_name, _ in parsed:
            vals = [r.get(metric_name, 0) or 0 for r in results]
            mn, mx = min(vals), max(vals)
            if mx > mn:
                scores_dict[metric_name] = [(v - mn) / (mx - mn) for v in vals]
            else:
                scores_dict[metric_name] = [0.5] * len(vals)

        # 计算综合评分
        totals = []
        for i in range(len(results)):
            score = 0
            for metric_name, weight in parsed:
                score += scores_dict[metric_name][i] * weight
            totals.append(round(score, 4))

        for i, r in enumerate(results):
            r["composite_score"] = totals[i]

        results.sort(key=lambda x: x["composite_score"], reverse=True)
        return results

    # ===== 内部计算方法 =====

    def _calc_annual_return(self, total_return_pct: float, trading_days: int) -> float:
        """计算年化收益率"""
        if trading_days <= 0:
            return 0
        total_return_ratio = 1 + total_return_pct / 100
        years = trading_days / 250
        return ((total_return_ratio ** (1 / years)) - 1) * 100 if years > 0 else 0

    def _extract_equity_series(self, stats: dict):
        """从 backtesting.py stats 中提取权益序列（Series）"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return None
            if hasattr(equity, 'columns'):
                # DataFrame: 取第一列 (Equity)
                return equity.iloc[:, 0]
            elif hasattr(equity, 'values'):
                return pd.Series(equity.values)
            return None
        except Exception:
            return None

    def _calc_sharpe(self, stats: dict, initial: float, final: float, days: int) -> float:
        """计算夏普比率 — 基于策略权益曲线日收益率（非标的资产价格）"""
        if days <= 1:
            return 0
        equity = self._extract_equity_series(stats)
        if equity is None or len(equity) < 2:
            return 0
        daily_returns = equity.pct_change().dropna()
        if len(daily_returns) == 0:
            return 0
        return _sharpe_ratio(daily_returns.values, risk_free=self.risk_free_rate)

    def _calc_annual_volatility(self, stats: dict) -> float:
        """计算年化波动率 — 基于策略权益曲线日收益率（非标的资产价格）"""
        equity = self._extract_equity_series(stats)
        if equity is None or len(equity) < 2:
            return 0
        daily_returns = equity.pct_change().dropna()
        if len(daily_returns) == 0:
            return 0
        return _volatility(daily_returns.values)

    def _calc_benchmark_return(self, start: date, end: date) -> float:
        """计算基准（沪深300）同期收益率"""
        try:
            bench_df = self.repo.get_benchmark_data(self.benchmark_code, start, end)
            if bench_df.empty:
                return 0
            return (bench_df["close"].iloc[-1] / bench_df["close"].iloc[0] - 1) * 100
        except Exception:
            return 0

    def _build_equity_curve(self, stats, index) -> list[Dict]:
        """构建净值曲线数据 — backtesting 返回 DataFrame/Series 都需兼容"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return []
            curve_data = []
            # backtesting 0.x+: 返回 pd.DataFrame (列: Equity, DrawdownPct, ...)
            if hasattr(equity, 'columns'):
                # DataFrame: 取第一列 (Equity)
                first_col = equity.iloc[:, 0]
                values = first_col.values
            elif hasattr(equity, 'values'):
                # Series
                values = equity.values
            else:
                return []
            for i, val in enumerate(values):
                curve_data.append({
                    "date": str(index[min(i, len(index) - 1)].date()),
                    "equity": float(val)
                })
            return curve_data
        except Exception:
            return []

    def _build_trades_detail(self, stats) -> list[Dict]:
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

    def _calc_monthly_returns(self, stats, index) -> dict[str, float]:
        """计算月度收益率 (取每月最后一个交易日的权益值)"""
        try:
            equity = stats.get("_equity_curve", None)
            if equity is None:
                return {}
            # backtesting 返回 DataFrame 时取第一列 (Equity)
            if hasattr(equity, 'columns'):
                values = equity.iloc[:, 0].values
            elif hasattr(equity, 'values'):
                values = equity.values
            else:
                return {}
            monthly = {}
            for i, val in enumerate(values):
                dt = index[min(i, len(index) - 1)]
                key = f"{dt.year}-{dt.month:02d}"
                # 取每月最后一个值 (即月末权益)
                monthly[key] = float(val)
            return monthly
        except Exception:
            return {}
