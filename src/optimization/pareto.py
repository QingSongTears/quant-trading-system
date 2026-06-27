"""
帕累托前沿分析
==============

多目标扫描时, 帕累托前沿 = "在所有目标上都不被任何其他点完全支配" 的解集。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .runner import TrialResult


@dataclass
class ParetoPoint:
    trial_id: int
    params: dict
    metrics: dict
    dominates: int = 0  # 被多少其他点支配

    def to_dict(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "params": self.params,
            "metrics": self.metrics,
            "dominated_by": self.dominates,
        }


class ParetoFront:
    """帕累托前沿 (最大化所有目标, 最小化回撤)

    用法:
        pf = ParetoFront(results, objectives=["sharpe", "total_return"], minimize={"max_drawdown"})
        print(pf.summarize())  # 前沿 + 关键指标
    """

    def __init__(
        self,
        results: Iterable[TrialResult],
        objectives: list[str],
        minimize: set[str] | None = None,
    ):
        self.objectives = objectives
        self.minimize = set(minimize or [])
        self.points = [
            ParetoPoint(
                trial_id=r.trial_id,
                params=r.params,
                metrics=r.metrics,
            )
            for r in results
            if r.error is None and r.metrics
        ]
        self._compute_dominance()

    def _compute_dominance(self):
        """计算每个点被多少其他点支配 (越小越前沿)"""
        n = len(self.points)
        # 使用 list 记录每次比较的 cache
        for i in range(n):
            pi = self.points[i]
            for j in range(n):
                if i == j:
                    continue
                pj = self.points[j]
                if self._dominates(pj, pi):
                    pi.dominates += 1

    def _dominates(self, a: ParetoPoint, b: ParetoPoint) -> bool:
        """a 支配 b 当且仅当: a 在所有目标上 ≥ b, 至少一个 >"""
        all_better = True
        strictly_better = False
        for obj in self.objectives:
            av = a.metrics.get(obj, 0)
            bv = b.metrics.get(obj, 0)
            if obj in self.minimize:
                av, bv = -av, -bv  # 转 maximize 等价
            if av < bv:
                all_better = False
                break
            if av > bv:
                strictly_better = True
        return all_better and strictly_better

    def front(self) -> list[ParetoPoint]:
        """返回帕累托前沿 (不被任何点支配)"""
        return [p for p in self.points if p.dominates == 0]

    def layer(self, n: int = 1) -> list[ParetoPoint]:
        """返回第 N 层前沿 (按被支配数升序)"""
        return sorted(self.points, key=lambda p: p.dominates)[:n]

    def summarize(self, top_n: int = 5) -> str:
        front = self.front()
        lines = [
            f"帕累托前沿: {len(front)} / {len(self.points)} 个点",
            f"目标: {self.objectives}  最小化: {sorted(self.minimize) or '[]'}",
            "",
            f"Top {top_n} 前沿点:",
        ]
        for p in sorted(front, key=lambda x: -x.metrics.get(self.objectives[0], 0))[:top_n]:
            m = " | ".join(f"{k}={p.metrics.get(k, 0):+.3f}" for k in self.objectives)
            lines.append(f"  #{p.trial_id}: {p.params} -> {m}")
        return "\n".join(lines)