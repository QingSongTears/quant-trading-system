"""
行业暴露约束 (T3.2) 测试
========================

覆盖:
- SectorConstraint 配置
- apply_sector_cap 各种边界 (排序/缺列/全同行业)
- BaseSelectionStrategy.apply_sector_constraint 集成路径
"""
from __future__ import annotations

import pandas as pd
import pytest


class TestSectorConstraint:
    """SectorConstraint 配置 dataclass"""

    def test_default_values(self):
        from src.selection.sector_constraint import SectorConstraint
        c = SectorConstraint()
        assert c.max_pct == 0.30
        assert c.max_count is None
        assert c.min_per_industry == 1

    def test_custom_values(self):
        from src.selection.sector_constraint import SectorConstraint
        c = SectorConstraint(max_pct=0.20, max_count=3, min_per_industry=1)
        assert c.max_pct == 0.20
        assert c.max_count == 3


class TestApplySectorCap:
    """apply_sector_cap 主入口测试"""

    def _make_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "code": ["001", "002", "003", "004", "005",
                     "006", "007", "008", "009", "010"],
            "industry": ["银行"] * 4 + ["地产"] * 2 + ["科技"] * 2 + ["医药"] * 2,
            "score": [0.9, 0.85, 0.8, 0.75, 0.7,
                      0.65, 0.6, 0.55, 0.5, 0.45],
        })

    def test_30pct_cap_limits_dense_industry(self):
        """4 只银行中, 30%×10=3, 应拒 1 只 (评分最低的 004)"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        result = apply_sector_cap(
            self._make_df(),
            SectorConstraint(max_pct=0.30),
            target_n=10,
        )
        assert "004" not in result.selected
        assert result.final_industry_counts["银行"] == 3
        assert "004" in result.rejected_by_industry["银行"]

    def test_max_count_overrides_pct(self):
        """max_count=2 时, 即使 pct 允许, 也只选 2 只/行业"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        result = apply_sector_cap(
            self._make_df(),
            SectorConstraint(max_pct=0.50, max_count=2),
            target_n=10,
        )
        assert all(v <= 2 for v in result.final_industry_counts.values())
        assert len(result.selected) == 8  # 4个行业 × 2 = 8

    def test_missing_industry_filled_as_unknown(self):
        """缺 industry 列 / None / '' 都视为 未知"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        df = pd.DataFrame({
            "code": ["A", "B", "C", "D"],
            "industry": ["银行", None, "", "地产"],
            "score": [4, 3, 2, 1],
        })
        result = apply_sector_cap(df, SectorConstraint(max_pct=0.50), target_n=4)
        assert result.final_industry_counts.get("未知", 0) >= 2

    def test_missing_industry_column(self):
        """整列缺失 → 全归 未知"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        df = pd.DataFrame({"code": ["A", "B", "C"], "score": [3, 2, 1]})
        result = apply_sector_cap(df, SectorConstraint(max_pct=0.30), target_n=3)
        assert list(result.final_industry_counts.keys()) == ["未知"]
        # 30% × 3 = 0.9 → max(1, 0.9)=1, 同行业只能选 1 只
        # 候选都被标为 未知 → 第 1 只通过, 第 2/3 只被拒
        assert len(result.selected) == 1
        assert result.selected == ["A"]

    def test_sort_by_score_descending(self):
        """按 score 降序选股, 同行业优先高分"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        df = pd.DataFrame({
            "code": ["A", "B", "C", "D", "E"],
            "industry": ["银行"] * 5,
            "score": [5, 4, 3, 2, 1],
        })
        result = apply_sector_cap(df, SectorConstraint(max_pct=0.40), target_n=5)
        # 40%×5=2, 应选 AB, 拒 CDE
        assert result.selected == ["A", "B"]
        assert result.rejected_by_industry["银行"] == ["C", "D", "E"]

    def test_alternative_sort_columns(self):
        """支持多种排序字段: sharpe, avg_return, return_Nd"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        df = pd.DataFrame({
            "code": ["A", "B", "C"],
            "industry": ["银行", "银行", "地产"],
            "sharpe": [2, 1, 3],  # sharpe 优先于 score (无 score 列)
        })
        result = apply_sector_cap(df, SectorConstraint(max_pct=0.67), target_n=3)
        # 排序: C(地产,3) → A(银行,2) → B(银行,1)
        assert result.selected[0] == "C"
        assert result.selected[1] == "A"

    def test_empty_candidates(self):
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        result = apply_sector_cap(pd.DataFrame(), SectorConstraint(max_pct=0.30))
        assert result.selected == []
        assert result.n_selected == 0
        assert result.n_rejected == 0

    def test_100pct_cap_no_constraint(self):
        """max_pct=1.0 视同无约束"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        df = pd.DataFrame({
            "code": [f"{i:03d}" for i in range(20)],
            "industry": ["银行"] * 20,
            "score": list(range(20, 0, -1)),
        })
        result = apply_sector_cap(df, SectorConstraint(max_pct=1.0), target_n=10)
        assert len(result.selected) == 10
        assert result.final_industry_counts["银行"] == 10

    def test_target_n_respected(self):
        """target_n < 候选数时, 只选 target_n"""
        from src.selection.sector_constraint import SectorConstraint, apply_sector_cap
        result = apply_sector_cap(
            self._make_df(),
            SectorConstraint(max_pct=1.0),
            target_n=3,
        )
        assert len(result.selected) == 3
        # 排序后前 3: 001(银行,0.9) 002(银行,0.85) 003(银行,0.8)
        assert result.selected == ["001", "002", "003"]


class TestLoadIndustryMap:
    """load_industry_map DB 集成测试"""

    def test_returns_dict_for_known_codes(self):
        from src.selection.sector_constraint import load_industry_map, get_engine
        ind = load_industry_map(get_engine(), ["000001", "600519", "300750"])
        assert isinstance(ind, dict)
        assert "000001" in ind
        # 000001=平安银行 → industry 应含 '银行'
        assert "银行" in ind["000001"]

    def test_empty_codes_returns_empty_dict(self):
        from src.selection.sector_constraint import load_industry_map, get_engine
        assert load_industry_map(get_engine(), []) == {}


class TestBaseSelectionStrategyIntegration:
    """BaseSelectionStrategy.apply_sector_constraint 集成"""

    def test_sector_constraint_with_in_memory_industry(self):
        """直接传 industry 列, 不查 DB"""
        from src.backtest.base_selection_strategy import BaseSelectionStrategy
        df = pd.DataFrame({
            "code": ["A", "B", "C", "D"],
            "industry": ["银行", "银行", "地产", "科技"],
            "score": [4, 3, 2, 1],
        })
        strategy = BaseSelectionStrategy()
        strategy.n_stocks = 4
        # 30% × 4 = 1.2 → max(1,1.2)=1, 银行最多 1 只
        selected, rejected = strategy.apply_sector_constraint(df)
        # 排序: A(银行,4) → C(地产,2) → D(科技,1) → B(银行,3) 但银行已满
        assert selected[0] == "A"
        assert "C" in selected and "D" in selected
        assert "B" not in selected  # 被行业约束拒掉
        assert rejected["银行"] == ["B"]

    def test_sector_constraint_loads_industry_from_db(self):
        """无 industry 列时, 自动从 DB 加载"""
        from src.backtest.base_selection_strategy import BaseSelectionStrategy
        # 平安银行 + 贵州茅台 + 宁德时代 (已知行业)
        df = pd.DataFrame({
            "code": ["000001", "600519", "300750"],
            "score": [3, 2, 1],
        })
        strategy = BaseSelectionStrategy()
        strategy.n_stocks = 3
        strategy.sector_cap_pct = 0.34  # 33% cap → 银行1只, 白酒1只, 电池1只
        selected, rejected = strategy.apply_sector_constraint(df)
        # 三只不同行业, 应该都能选上
        assert len(selected) >= 2  # 至少选2只 (3只股票3个行业)

    def test_disabled_constraint(self):
        """sector_cap_pct=None 时, 不应用约束"""
        from src.backtest.base_selection_strategy import BaseSelectionStrategy
        df = pd.DataFrame({
            "code": ["A", "B", "C", "D"],
            "industry": ["银行"] * 4,  # 全是银行
            "score": [4, 3, 2, 1],
        })
        strategy = BaseSelectionStrategy()
        strategy.n_stocks = 3
        strategy.sector_cap_pct = None
        strategy.sector_max_count = None
        selected, rejected = strategy.apply_sector_constraint(df)
        # 无约束 → 选评分最高的3只
        assert selected == ["A", "B", "C"]