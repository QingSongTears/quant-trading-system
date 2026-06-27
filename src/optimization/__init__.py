"""
参数扫描框架 — 网格搜索/贝叶斯优化
=====================================

功能:
- GridSearch: 全笛卡尔积遍历
- BayesianOpt: Optuna backend, TPESampler
- 参数空间定义: Param/Int/Float/Categorical + space.yaml
- 批量回测 + 结果聚合 + 帕累托前沿
- 断点续扫 + 缓存

用法:
    from src.optimization import GridSearcher, ParamSpace, BayesianOpt

    space = ParamSpace.from_yaml("config/scans/sector_cap.yaml")
    searcher = GridSearcher(space, scoring="sharpe")
    results = searcher.run(strategy="SmallCap", start="2025-06", end="2025-12")

    opt = BayesianOpt(space, scoring="sharpe", n_trials=50)
    best = opt.run(strategy="SmallCap", start="2025-06", end="2025-12")

    # 帕累托
    from src.optimization import ParetoFront
    pf = ParetoFront(results, objectives=["sharpe", "max_drawdown"])
    print(pf.summarize())
"""
from __future__ import annotations

from .space import (
    Param,
    Float,
    Int,
    Categorical,
    ParamSpace,
)
from .runner import BacktestRunner, TrialResult
from .grid_search import GridSearcher
from .bayesian_opt import BayesianOpt
from .pareto import ParetoFront
from .store import ScanStore

__all__ = [
    "Param", "Float", "Int", "Categorical", "ParamSpace",
    "BacktestRunner", "TrialResult",
    "GridSearcher", "BayesianOpt", "ParetoFront", "ScanStore",
]