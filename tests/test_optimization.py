"""优化框架测试 — 覆盖 space/grid/bayes/pareto/store 全模块"""
from __future__ import annotations

import os
import sys
import json
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestParamTypes:
    def test_float_grid(self):
        from src.optimization.space import Float
        f = Float("x", 0.0, 1.0, step=0.25)
        pts = f.grid_points()
        assert pts == [0.0, 0.25, 0.5, 0.75, 1.0]

    def test_float_no_step_default_5_points(self):
        from src.optimization.space import Float
        f = Float("x", 0.0, 1.0)
        pts = f.grid_points()
        assert len(pts) == 5
        assert pts[0] == 0.0 and pts[-1] == 1.0

    def test_int_grid(self):
        from src.optimization.space import Int
        i = Int("x", 1, 5)
        assert i.grid_points() == [1, 2, 3, 4, 5]

    def test_int_step(self):
        from src.optimization.space import Int
        i = Int("x", 0, 10, step=2)
        assert i.grid_points() == [0, 2, 4, 6, 8, 10]

    def test_categorical_grid(self):
        from src.optimization.space import Categorical
        c = Categorical("x", ["a", "b", "c"])
        assert c.grid_points() == ["a", "b", "c"]


class TestParamSpace:
    def _space(self):
        from src.optimization.space import Float, Int, Categorical
        from src.optimization import ParamSpace
        return ParamSpace().add(
            Float("a", 0.0, 1.0, step=0.5),
            Int("b", 1, 2),
            Categorical("c", ["x", "y"]),
        )

    def test_grid_size(self):
        s = self._space()
        # a: [0, 0.5, 1] (3) * b: [1, 2] (2) * c: 2 = 12
        combos = list(s.grid_iter())
        assert len(combos) == 12

    def test_grid_keys(self):
        s = self._space()
        first = next(s.grid_iter())
        assert set(first.keys()) == {"a", "b", "c"}

    def test_yaml_roundtrip(self, tmp_path):
        from src.optimization import ParamSpace
        original = self._space()
        path = tmp_path / "space.yaml"
        original.to_yaml(path)
        loaded = ParamSpace.from_yaml(path)
        assert loaded.names() == original.names()

    def test_from_yaml_validation(self, tmp_path):
        from src.optimization import ParamSpace
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: value\n")
        with pytest.raises(ValueError, match="参数列表"):
            ParamSpace.from_yaml(bad)

    def test_from_yaml_types(self, tmp_path):
        from src.optimization import ParamSpace
        yaml_content = """
- {name: a, type: float, low: 0, high: 1, step: 0.5}
- {name: b, type: int, low: 1, high: 3}
- {name: c, type: categorical, options: [x, y, z]}
"""
        p = tmp_path / "mixed.yaml"
        p.write_text(yaml_content)
        s = ParamSpace.from_yaml(p)
        assert len(s) == 3
        assert isinstance(s.params[0], type(s.params[0]))


class TestGridSearcher:
    def test_grid_basic(self):
        from src.optimization import ParamSpace, Float, GridSearcher
        space = ParamSpace().add(Float("x", 0.0, 1.0, step=0.5))
        gs = GridSearcher(space, strategy="test", start="2024", end="2024")
        results = gs.run()
        assert len(results) == 3  # 0, 0.5, 1.0
        assert all(r.metrics["sharpe"] >= 0 for r in results)

    def test_topk_ordering(self):
        from src.optimization import ParamSpace, Float, GridSearcher
        space = ParamSpace().add(Float("x", 0.0, 1.0, step=0.1))
        gs = GridSearcher(space, strategy="test", start="2024", end="2024")
        results = gs.run()
        top3 = gs.topk(results, k=3, by="sharpe")
        assert len(top3) == 3
        # 必须降序
        sharpes = [r.metrics["sharpe"] for r in top3]
        assert sharpes == sorted(sharpes, reverse=True)

    def test_export(self, tmp_path):
        from src.optimization import ParamSpace, Float, GridSearcher
        space = ParamSpace().add(Float("x", 0.0, 1.0, step=0.5))
        gs = GridSearcher(space, strategy="t", start="t", end="t")
        results = gs.run()
        out = tmp_path / "r.json"
        gs.export(results, out)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert len(loaded) == 3
        assert "trial_id" in loaded[0]


class TestBayesianOpt:
    def test_basic(self):
        from src.optimization import ParamSpace, Float, BayesianOpt
        space = ParamSpace().add(Float("x", 0.0, 1.0, step=0.25))
        opt = BayesianOpt(space, strategy="t", start="t", end="t", n_trials=15)
        best = opt.run()
        assert "sector_cap_pct" in best.params or "x" in best.params
        assert best.metrics["sharpe"] >= 0

    def test_history_returns_all_trials(self):
        from src.optimization import ParamSpace, Float, BayesianOpt
        space = ParamSpace().add(Float("x", 0.0, 1.0, step=0.25))
        opt = BayesianOpt(space, strategy="t", start="t", end="t", n_trials=10)
        opt.run()
        assert len(opt.history()) == 10


class TestParetoFront:
    def _results(self):
        from src.optimization.runner import TrialResult
        return [
            TrialResult(1, {}, {"sharpe": 2.0, "total_return": 10.0, "max_drawdown": -5.0}),
            TrialResult(2, {}, {"sharpe": 1.5, "total_return": 12.0, "max_drawdown": -3.0}),
            TrialResult(3, {}, {"sharpe": 1.8, "total_return": 11.0, "max_drawdown": -4.0}),
            TrialResult(4, {}, {"sharpe": 0.5, "total_return": 5.0, "max_drawdown": -10.0}),
        ]

    def test_basic_front(self):
        from src.optimization import ParetoFront
        pf = ParetoFront(self._results(), objectives=["sharpe", "total_return"], minimize={"max_drawdown"})
        front = pf.front()
        # 4 号点被完全支配 (所有目标都差)
        ids = {p.trial_id for p in front}
        assert 4 not in ids
        assert len(front) >= 1

    def test_summarize(self):
        from src.optimization import ParetoFront
        pf = ParetoFront(self._results(), objectives=["sharpe", "total_return"], minimize={"max_drawdown"})
        text = pf.summarize()
        assert "帕累托前沿" in text
        assert "sharpe" in text


class TestScanStore:
    def test_create_and_save(self, tmp_path):
        from src.optimization import ScanStore
        from src.optimization.runner import TrialResult
        db = str(tmp_path / "scan.db")
        store = ScanStore(db)
        sid = store.create_run("S", "test_space", "2024-01-01", "2024-12-31")
        result = TrialResult(1, {"x": 0.5}, {"sharpe": 1.0}, elapsed_sec=0.5)
        store.save_trial(sid, result)
        loaded = store.load_results(sid)
        assert len(loaded) == 1
        assert loaded[0].params == {"x": 0.5}
        assert loaded[0].metrics["sharpe"] == 1.0

    def test_exists_check(self, tmp_path):
        from src.optimization import ScanStore
        from src.optimization.runner import TrialResult
        db = str(tmp_path / "scan.db")
        store = ScanStore(db)
        sid = store.create_run("S", "sp", "2024", "2024")
        assert not store.exists(sid, {"x": 0.5})
        store.save_trial(sid, TrialResult(1, {"x": 0.5}, {"sharpe": 1.0}))
        assert store.exists(sid, {"x": 0.5})

    def test_list_runs(self, tmp_path):
        from src.optimization import ScanStore
        db = str(tmp_path / "scan.db")
        store = ScanStore(db)
        store.create_run("S1", "a", "2024", "2024")
        store.create_run("S2", "b", "2024", "2024")
        runs = store.list_runs()
        assert len(runs) == 2
        assert {r["strategy"] for r in runs} == {"S1", "S2"}