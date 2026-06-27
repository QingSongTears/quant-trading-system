"""
贝叶斯优化 — Optuna backend
==========================

TPE sampler, 支持多目标。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .runner import BacktestRunner, TrialResult
from .space import ParamSpace


class BayesianOpt:
    """Optuna 贝叶斯优化

    用法:
        opt = BayesianOpt(space, strategy="SmallCap", start="2025-06", end="2025-12", n_trials=30)
        best = opt.run(verbose=True)
        print(best.params, best.metrics)
    """

    def __init__(
        self,
        space: ParamSpace,
        strategy: str,
        start: str,
        end: str,
        base_kwargs: dict | None = None,
        scoring: str = "sharpe",
        n_trials: int = 30,
        timeout_sec: float | None = None,
        seed: int = 42,
    ):
        self.space = space
        self.runner = BacktestRunner(
            strategy=strategy, start=start, end=end,
            base_kwargs=base_kwargs, scoring=scoring,
        )
        self.n_trials = n_trials
        self.timeout_sec = timeout_sec
        self.seed = seed
        self.study = None

    def run(self, verbose: bool = False) -> TrialResult:
        """跑贝叶斯优化, 返回最佳 trial"""
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=self.seed),
        )
        self.study = study

        def objective(trial: optuna.Trial) -> float:
            params = {}
            for p in self.space.params:
                params[p.name] = _suggest(trial, p)
            r = self.runner.run(**params)
            # 记录到 study 便于后续分析
            r_dict = r.to_dict()
            trial.set_user_attr("result", r_dict)
            return r.metrics.get(self.runner.scoring, 0)

        study.optimize(
            objective,
            n_trials=self.n_trials,
            timeout=self.timeout_sec,
            show_progress_bar=False,
        )
        best_trial = study.best_trial
        r = best_trial.user_attrs.get("result")
        return TrialResult(
            trial_id=best_trial.number,
            params=r["params"],
            metrics=r["metrics"],
            elapsed_sec=r["elapsed_sec"],
            error=r["error"],
            timestamp=r["timestamp"],
        )

    def history(self) -> list[TrialResult]:
        """返回所有 trial (按时间顺序)"""
        if self.study is None:
            return []
        out = []
        for t in self.study.trials:
            r = t.user_attrs.get("result")
            if r:
                out.append(TrialResult(**{
                    "trial_id": t.number,
                    "params": r["params"],
                    "metrics": r["metrics"],
                    "elapsed_sec": r["elapsed_sec"],
                    "error": r["error"],
                    "timestamp": r["timestamp"],
                }))
        return out

    def export(self, path: str | Path) -> None:
        """导出全部 trial + 最优"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if self.study is None:
            self.run()
        best_attrs = dict(self.study.best_trial.user_attrs["result"])
        best_attrs.pop("trial_id", None)  # 用 study.number 作 trial_id
        best = TrialResult(
            trial_id=self.study.best_trial.number,
            **best_attrs,
        )
        out = {
            "best": best.to_dict(),
            "trials": [r.to_dict() for r in self.history()],
        }
        p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def _suggest(trial, p):
    """Optuna suggest 封装"""
    if isinstance(p, type(__import__("src.optimization.space", fromlist=["Float"]).Float)):
        F = __import__("src.optimization.space", fromlist=["Float"]).Float
        I = __import__("src.optimization.space", fromlist=["Int"]).Int
        C = __import__("src.optimization.space", fromlist=["Categorical"]).Categorical
    else:
        from .space import Float as F, Int as I, Categorical as C
    if isinstance(p, F):
        return trial.suggest_float(p.name, p.low, p.high, step=p.step, log=False)
    elif isinstance(p, I):
        return trial.suggest_int(p.name, p.low, p.high, step=p.step, log=False)
    elif isinstance(p, C):
        return trial.suggest_categorical(p.name, p.options)
    raise TypeError(f"unknown param type: {type(p)}")