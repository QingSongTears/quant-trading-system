"""
网格搜索 — 全笛卡尔积遍历
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from .runner import BacktestRunner, TrialResult
from .space import ParamSpace


class GridSearcher:
    """GridSearch 执行器

    用法:
        gs = GridSearcher(space, strategy="SmallCap", start="2025-06", end="2025-12")
        results = gs.run(verbose=True)        # 全量
        top5 = gs.topk(results, k=5, by="sharpe")
        gs.export(results, "output/scans/sector_cap.json")
    """

    def __init__(
        self,
        space: ParamSpace,
        strategy: str,
        start: str,
        end: str,
        base_kwargs: dict | None = None,
        scoring: str = "sharpe",
    ):
        self.space = space
        self.runner = BacktestRunner(
            strategy=strategy, start=start, end=end,
            base_kwargs=base_kwargs, scoring=scoring,
        )

    def run(
        self,
        combinations: Iterable[dict] | None = None,
        verbose: bool = False,
    ) -> list[TrialResult]:
        """跑全部组合 (或用户传入的子集)"""
        if combinations is None:
            combinations = self.space.grid_iter()
        results: list[TrialResult] = []
        total = self._estimate_total() if combinations is None else None
        for i, params in enumerate(combinations, 1):
            r = self.runner.run(trial_id=i, **params)
            results.append(r)
            if verbose:
                if r.error:
                    print(f"  [{i:>3}] {r.params} -> ERROR: {r.error}", file=sys.stderr)
                else:
                    score = r.metrics.get(self.runner.scoring, 0)
                    print(f"  [{i:>3}] {r.params} -> {self.runner.scoring}={score:.4f}", file=sys.stderr)
        return results

    def _estimate_total(self) -> int:
        n = 1
        for p in self.space.params:
            n *= len(p.grid_points())
        return n

    def topk(self, results: list[TrialResult], k: int = 5, by: str | None = None) -> list[TrialResult]:
        """返回 Top-K (默认按 scoring 倒序)"""
        by = by or self.runner.scoring
        return sorted(results, key=lambda r: r.metrics.get(by, 0), reverse=True)[:k]

    def export(self, results: list[TrialResult], path: str | Path) -> None:
        """导出 JSON"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )