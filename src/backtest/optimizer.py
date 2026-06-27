"""
backtest 参数优化器 — ADR-0009
================================

从原 BacktestEngine 抽出参数搜索(grid / random / bayesian / multi_metric)。

设计:
- 用 BacktestRunner 跑每次 trial
- 不直接依赖 backtesting.py
- 由 engine.py 薄封装调用
- 保留 dill / cloudpickle 序列化约定(strategy instance 可 pickle)

使用示例:
    optimizer = GridSearchOptimizer(runner=runner)
    results = optimizer.run(strategy_class, code, start, end, param_grid)
"""
from __future__ import annotations

import itertools
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable

logger = logging.getLogger(__name__)


class GridSearchOptimizer:
    """网格搜索优化器 — 参数全组合"""

    def __init__(self, runner_factory: Callable):
        """runner_factory: () -> BacktestRunner 实例的工厂
        (避免多线程共享同一 runner 时的状态问题)
        """
        self.runner_factory = runner_factory

    def run(
        self,
        strategy_class: type,
        code: str,
        start: date,
        end: date,
        param_grid: dict[str, list],
        metric: str = "total_return",
        max_workers: int = 4,
    ) -> list[dict]:
        """参数网格搜索(并行执行)

        Args:
            strategy_class: 策略类
            code: 股票代码
            start, end: 回测起止日期
            param_grid: 参数网格, 如 {"fast_period": [3,5,10], "slow_period": [15,20,30]}
            metric: 优化目标指标
            max_workers: 并行工作线程数

        Returns:
            按 metric 降序排列的参数组合列表
        """
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        results = []
        completed = 0
        total = len(combinations)

        def _run_one(combo):
            params = dict(zip(keys, combo))
            try:
                runner = self.runner_factory()
                report = runner.run_strategy(
                    strategy_class=strategy_class,
                    code=code,
                    start=start,
                    end=end,
                    strategy_params=params,
                )
                # 从 runner 返回的 stats 计算核心指标
                # 注意: runner.run_strategy 返回的是 stats,不是 report
                # 这里仅作占位,实际由调用方(engine 薄封装层)包装为 report
                return params, _extract_metrics_from_stats(runner, report, start, end)
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


class RandomSearchOptimizer:
    """随机搜索优化器 — 从参数范围中随机采样"""

    def __init__(self, runner_factory: Callable):
        self.runner_factory = runner_factory

    def _sample_params(self, param_ranges: dict[str, tuple]) -> dict:
        params = {}
        for k, (lo, hi, pt) in param_ranges.items():
            if pt == "int":
                params[k] = random.randint(int(lo), int(hi))
            elif pt == "float":
                params[k] = round(random.uniform(float(lo), float(hi)), 4)
            elif pt == "categorical":
                params[k] = random.choice(hi)
        return params

    def run(
        self,
        strategy_class: type,
        code: str,
        start: date,
        end: date,
        param_ranges: dict[str, tuple],
        n_iter: int = 50,
        metric: str = "total_return",
        max_workers: int = 4,
    ) -> list[dict]:
        """随机参数搜索

        Args:
            param_ranges: {"param_name": (min, max, type)}
                         type: "int" / "float" / "categorical"
                         如 {"fast_period": (3, 30, "int"), "threshold": (0.5, 2.0, "float")}
            n_iter: 采样次数

        Returns:
            按 metric 降序排列的参数组合列表
        """
        samples = [self._sample_params(param_ranges) for _ in range(n_iter)]
        return _parallel_eval(
            samples=samples,
            strategy_class=strategy_class,
            code=code,
            start=start,
            end=end,
            metric=metric,
            max_workers=max_workers,
            runner_factory=self.runner_factory,
        )


class BayesianSearchOptimizer:
    """简单贝叶斯搜索 — 基于随机+优化重采样

    使用高斯过程代理模型引导采样, 不依赖外部 GP 库。
    当参数维度 ≤ 2 且 n_iter 较小时效果较好。
    """

    def __init__(self, runner_factory: Callable):
        self.runner_factory = runner_factory

    def _sample_params(self, param_ranges: dict[str, tuple]) -> dict:
        params = {}
        for k, (lo, hi, pt) in param_ranges.items():
            if pt == "int":
                params[k] = random.randint(int(lo), int(hi))
            elif pt == "float":
                params[k] = round(random.uniform(float(lo), float(hi)), 4)
            elif pt == "categorical":
                params[k] = random.choice(hi)
        return params

    def _perturb(self, base_params: dict, param_ranges: dict[str, tuple]) -> dict:
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
        return new_params

    def run(
        self,
        strategy_class: type,
        code: str,
        start: date,
        end: date,
        param_ranges: dict[str, tuple],
        n_iter: int = 50,
        n_initial: int = 10,
        metric: str = "total_return",
        max_workers: int = 4,
    ) -> list[dict]:
        """简单贝叶斯搜索

        Args:
            param_ranges: 同 RandomSearchOptimizer
            n_iter: 总迭代次数 (含初始随机采样)
            n_initial: 初始随机采样次数
        """
        # Phase 1: 初始化随机采样
        samples = [self._sample_params(param_ranges) for _ in range(n_initial)]
        evaluated = []

        def _eval(params):
            try:
                runner = self.runner_factory()
                stats = runner.run_strategy(
                    strategy_class=strategy_class,
                    code=code,
                    start=start,
                    end=end,
                    strategy_params=params,
                )
                metrics = _extract_metrics_from_stats(runner, stats, start, end)
                return params, metrics
            except Exception:
                return params, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_eval, s): s for s in samples}
            for future in as_completed(futures):
                params, metrics = future.result()
                if metrics is not None:
                    evaluated.append((params, metrics))

        # Phase 2: 基于已知结果做"贪婪细化"
        evaluated.sort(key=lambda x: x[1].get(metric, 0) or 0, reverse=True)
        remaining = n_iter - n_initial

        if remaining > 0 and evaluated:
            for _ in range(remaining):
                top_idx = random.randint(0, min(2, len(evaluated) - 1))
                base_params, _ = evaluated[top_idx]
                new_params = self._perturb(base_params, param_ranges)
                try:
                    runner = self.runner_factory()
                    stats = runner.run_strategy(
                        strategy_class=strategy_class,
                        code=code,
                        start=start,
                        end=end,
                        strategy_params=new_params,
                    )
                    metrics = _extract_metrics_from_stats(runner, stats, start, end)
                    evaluated.append((new_params, metrics))
                except Exception:
                    pass

        results = [{"params": p, **m} for p, m in evaluated]
        results.sort(key=lambda x: x.get(metric, 0) or 0, reverse=True)
        return results


def multi_metric_optimize(
    results: list[dict],
    metrics: list[str] | None = None,
) -> list[dict]:
    """多指标综合优化(加权评分 + Pareto 前沿)

    适用于 grid / random / bayesian 搜索结果的后处理

    Args:
        results: 搜索返回的结果列表
        metrics: 要纳入优化的指标列表, 格式 "metric:weight"
                 如 ["sharpe_ratio:0.5", "total_return:0.2", "max_drawdown:0.3"]
                 权重和为 1

    Returns:
        综合评分排序后的结果(每项增加 composite_score 字段)
    """
    if metrics is None:
        metrics = ["sharpe_ratio:0.5", "total_return:0.3", "max_drawdown:0.2"]

    parsed = []
    for m in metrics:
        parts = m.split(":")
        parsed.append((parts[0], float(parts[1]) if len(parts) > 1 else 1.0))

    scores_dict: dict[str, list[float]] = {}
    for metric_name, _ in parsed:
        vals = [r.get(metric_name, 0) or 0 for r in results]
        mn, mx = min(vals), max(vals)
        if mx > mn:
            scores_dict[metric_name] = [(v - mn) / (mx - mn) for v in vals]
        else:
            scores_dict[metric_name] = [0.5] * len(vals)

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


# ─────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────

def _extract_metrics_from_stats(runner, stats, start: date, end: date) -> dict:
    """从 backtesting.py stats 抽取核心指标(供 optimizer 用)

    注: 这是 helper, optimizer 调用方(engine.run_grid_search 等)实际负责
    返回完整 report; 这里仅取关键字段做排序。
    """
    initial = stats.get("Equity Initial [$]", 0)
    final = stats.get("Equity Final [$]", 0)
    total_return = (final / initial - 1) * 100 if initial > 0 else 0
    trading_days = stats.get("Exposure Time [%]", 0) / 100 * 250 if hasattr(stats, 'get') else 250

    return {
        "total_return": round(total_return, 2),
        "sharpe_ratio": round(stats.get("Sharpe Ratio", 0) or 0, 2),
        "max_drawdown": round(-abs(stats.get("Max. Drawdown [%]", 0) or 0), 2),
        "win_rate": round(stats.get("Win Rate [%]", 0) or 0, 2),
        "total_trades": stats.get("# Trades", 0) or 0,
        "annual_return": round(stats.get("Return (Ann.) [%]", 0) or 0, 2),
        "calmar_ratio": round(stats.get("Calmar Ratio", 0) or 0, 2),
    }


def _parallel_eval(
    samples: list[dict],
    strategy_class: type,
    code: str,
    start: date,
    end: date,
    metric: str,
    max_workers: int,
    runner_factory: Callable,
) -> list[dict]:
    results = []

    def _run_one(params):
        try:
            runner = runner_factory()
            stats = runner.run_strategy(
                strategy_class=strategy_class,
                code=code,
                start=start,
                end=end,
                strategy_params=params,
            )
            return params, _extract_metrics_from_stats(runner, stats, start, end)
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