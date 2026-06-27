"""
行业暴露约束 — PortfolioBacktestEngine 集成测试 (T3.2)
=======================================================

测试 _apply_sector_constraint 在组合回测引擎中的实际效果。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.backtest.base_selection_strategy import BaseSelectionStrategy
from src.backtest.portfolio_engine import PortfolioBacktestEngine


class _StubStrategy(BaseSelectionStrategy):
    """固定选股列表的策略 (用于测试约束)"""
    name = "stub"
    _fixed_codes: list[str] = ["000001", "600519", "300750"]  # 银行/白酒/电池

    def __init__(self):
        super().__init__()
        self.n_stocks = 3  # 与 _fixed_codes 数量一致, 防止触发补全

    def select(self, rebalance_date, universe_df):
        return self._fixed_codes.copy()


class _AllBankStrategy(BaseSelectionStrategy):
    """模拟同行业集中 (银行), 用于触发约束"""
    name = "all_bank"
    _fixed_codes: list[str] = ["000001", "002142", "600036"]  # 全银行

    def __init__(self):
        super().__init__()
        self.n_stocks = 3  # 与 _fixed_codes 数量一致

    def select(self, rebalance_date, universe_df):
        return self._fixed_codes.copy()


class TestApplySectorConstraint:
    """_apply_sector_constraint 直接测试"""

    def setup_method(self):
        self.engine = PortfolioBacktestEngine()
        # universe 包含测试用的所有股票, universe 角色是 "candidate pool"
        # selected 必须由策略 select() 返回, 但本测试直接传 selected
        self.universe = pd.DataFrame({
            "code": ["000001", "600519", "300750", "002142", "600036", "601318"],
            "name": ["平安银行", "贵州茅台", "宁德时代", "宁波银行", "招商银行", "中国平安"],
            "industry": ["银行", "白酒", "电池", "银行", "银行", "保险"],
            "close": [10.0] * 6,
            "avg_amount_wan": [10000] * 6,
        })

    def test_different_industries_pass(self):
        """3 个不同行业的股票全部通过 (无冲突)"""
        s = _StubStrategy()  # n_stocks=3, codes 长度=3, 不会触发补全
        s.sector_cap_pct = 0.30
        codes = ["000001", "600519", "300750"]
        result = self.engine._apply_sector_constraint(codes, self.universe, s)
        assert sorted(result) == ["000001", "300750", "600519"]

    def test_same_industry_limited(self):
        """3 只银行受 30% (3×0.3=0.9→max=1) 约束, 只保留 1 只 + 跨行业补全"""
        s = _AllBankStrategy()  # n_stocks=3
        s.sector_cap_pct = 0.30
        codes = ["000001", "002142", "600036"]
        result = self.engine._apply_sector_constraint(codes, self.universe, s)
        # 只 1 只银行 (000001) 通过, 其余被拒 → 触发跨行业补全
        # 从 universe 补 600519 (白酒), 300750 (电池) → kept=3
        assert "000001" in result
        assert "002142" not in result and "600036" not in result
        assert len(result) == 3  # 1 银行 + 跨行业补全

    def test_max_count_overrides(self):
        """max_count=2 时即使 30% 允许, 也最多 2 只/行业"""
        s = _StubStrategy()
        s.n_stocks = 5
        s.sector_cap_pct = 0.50
        s.sector_max_count = 2
        # 模拟 4 只银行
        codes = ["000001", "002142", "600036", "601318"]
        result = self.engine._apply_sector_constraint(codes, self.universe, s)
        # 银行 3 只, 保险 1 只, max=2 → 银行保留 2 只
        banks = [c for c in result if c in ("000001", "002142", "600036")]
        assert len(banks) == 2

    def test_disabled_constraint(self):
        """sector_cap_pct=None → 不应用约束, 原列表直接返回"""
        s = _AllBankStrategy()
        s.sector_cap_pct = None
        s.sector_max_count = None
        codes = ["000001", "002142", "600036"]
        result = self.engine._apply_sector_constraint(codes, self.universe, s)
        assert result == codes

    def test_empty_selected(self):
        """空列表直接返回"""
        s = _StubStrategy()
        assert self.engine._apply_sector_constraint([], self.universe, s) == []

    def test_backfill_from_universe(self):
        """候选不足时按行业补全 (从 universe 跨行业)"""
        s = _StubStrategy()
        s.n_stocks = 3
        s.sector_cap_pct = 1.0  # 关闭行业限制 (补全不会因行业卡掉)
        s.sector_max_count = None
        # selected 3 只股票 (含 1 只银行), universe 中还有其他股票
        codes = ["000001", "600519", "300750"]  # 银行/白酒/电池
        # universe 中再混入 2 只银行, 1 只保险 (作为补全候选)
        big_universe = pd.concat([
            self.universe,
            pd.DataFrame({
                "code": ["601318", "002142"],
                "name": ["中国平安", "宁波银行"],
                "industry": ["保险", "银行"],
                "close": [10.0, 10.0],
                "avg_amount_wan": [10000, 10000],
            })
        ], ignore_index=True)
        # n_stocks=5 → 应补全到 5 只
        s.n_stocks = 5
        result = self.engine._apply_sector_constraint(codes, big_universe, s)
        # 原 3 只都在 + 补 2 只跨行业
        assert len(result) == 5
        assert "000001" in result and "600519" in result and "300750" in result


class TestE2EIntegration:
    """E2E: 跑一次真实回测, 对比有无约束"""

    def test_run_with_constraint_does_not_break(self):
        """带约束的回测能完整跑通"""
        from src.strategies.small_cap import SmallCapStrategy

        engine = PortfolioBacktestEngine()
        s = SmallCapStrategy()
        s.n_stocks = 10  # 减少规模, 加速
        s.sector_cap_pct = 0.30
        s.sector_max_count = 3
        report = engine.run(s, date(2025, 6, 1), date(2025, 9, 30))

        assert report is not None
        assert hasattr(report, "total_return")
        assert hasattr(report, "sharpe_ratio")
        assert hasattr(report, "max_drawdown")
        assert report.total_trades > 0