"""
龙虎榜机构评分器 v1.0
====================================
基于龙虎榜机构/游资交易行为的评分：

评分逻辑:
  - 机构净买入 → 加分 (最高 +5)
  - 游资净卖出 → 加分 (机构接盘)
  - 外资/北向净买入 → 加分
  - 多个机构同时买入 → 额外加分
  - 游资接力 → 减分

数据源: quant.db :: lhb_institutional
"""
from __future__ import annotations
from typing import Any

import pandas as pd

from ..config import get_config, get_db_url
# PR3.2: create_engine 由 base.py 通过 get_engine 单例提供
from ..db.sql_utils import read_sql
from .base import BaseScorer


class LhbInstitutionalScorer(BaseScorer):
    """
    龙虎榜机构评分器

    接口:
        scorer = LhbInstitutionalScorer()
        result = scorer.score("000001", "2026-06-15")  # 返回标准 dict
    """

    name = "lhb_institutional"
    label_zh = "龙虎榜机构"
    weight = 0.05
    max_raw = 20

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self._cache: pd.DataFrame | None = None

    def _load_data(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        self._cache = read_sql("SELECT * FROM lhb_institutional", self.engine)
        return self._cache

    def score(self, code: str, as_of_date: str | None = None,
              lookback_days: int = 60) -> dict[str, Any]:
        """
        对单只股票计算龙虎榜机构评分

        参数:
            code: 6位股票代码
            as_of_date: 评分基准日 (保留用于 BaseScorer 契约兼容)
            lookback_days: 回溯天数（默认60天）

        返回:
            标准 dict: {"code", "as_of_date", "total", "weighted",
                       "sub_scores", "error"}
        """
        try:
            df = self._load_data()
        except Exception as e:
            return {
                "code": code, "as_of_date": as_of_date,
                "total": 0, "weighted": 0.0,
                "sub_scores": {}, "error": f"load_data failed: {e}",
            }

        code = str(code).zfill(6)
        stock_rows = df[df["code"] == code]

        if stock_rows.empty:
            return {
                "code": code, "as_of_date": as_of_date,
                "total": 0, "weighted": 0.0,
                "sub_scores": {}, "error": "no_lhb_data",
            }

        recent = stock_rows.sort_values("trade_date", ascending=False).head(10)

        inst_total = 0
        north_total = 0
        tour_total = 0
        buy_count = 0

        for _, row in recent.iterrows():
            inst_net = row.get("inst_net", 0) or 0
            north = row.get("north_net", 0) or 0
            tour = row.get("tour_net", 0) or 0

            inst_total += inst_net
            north_total += north
            tour_total += tour
            if inst_net > 10000000:
                buy_count += 1

        total_score = 0
        if inst_total > 50000000:
            total_score += 5
        elif inst_total > 10000000:
            total_score += 3
        elif inst_total < -50000000:
            total_score -= 3

        if north_total > 50000000:
            total_score += 3
        if tour_total < -50000000:
            total_score += 2
        elif tour_total > 100000000:
            total_score -= 2

        if buy_count >= 3:
            total_score += 2

        normalized = max(0, min(20, total_score + 10))

        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": int(normalized),
            "weighted": round(int(normalized) / 20 * self.max_score, 2),
            "sub_scores": {
                "inst_net_yi": round(inst_total / 1e8, 3),
                "north_net_yi": round(north_total / 1e8, 3),
                "tour_net_yi": round(tour_total / 1e8, 3),
                "buy_events": buy_count,
            },
            "error": None,
        }


# ─────────────────────────────────────────────
# 测试函数
# ─────────────────────────────────────────────
if __name__ == "__main__":
    scorer = LhbInstitutionalScorer()
    test_codes = ["000001", "600519", "300750"]
    print("📥 龙虎榜机构评分器 v1.0 测试")
    for code in test_codes:
        r = scorer.score(code, "2026-06-15")
        print(f"  {code}: total={r['total']} weighted={r['weighted']} "
              f"sub={r['sub_scores']} error={r['error']}")