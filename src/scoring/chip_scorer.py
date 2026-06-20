"""
筹码面评分器 — 主力控盘模型 (权重 10%)
========================================

基于 chip_distribution 表的筹码分布数据。

数据: 5,196只股票, 单日快照 (2026-06-17)
  - chipProfitRate: 获利比例 0-100%
  - chipAvgCost: 平均持仓成本
  - chipConcentration90: 90%筹码集中度 (数值越小越集中)
  - chipConcentration70: 70%筹码集中度

设计理念:
  寻找"主力控盘"标的 — 筹码集中 + 获利盘少 + 价格低于成本。

5个子指标 (每项 0-3 分，满分 15，归一化到 0-20):
  1. 获利出清   (0-3): 低获利比例=套牢盘锁定, 抛压小
  2. 筹码集中   (0-3): 低集中度值=筹码集中在少数账户
  3. 成本区间   (0-3): 价格低于/接近平均成本=超跌反弹机会
  4. 主力控盘   (0-3): 集中+低获利=主力高度控盘
  5. 筹码综合   (0-3): 综合上述信号

用法:
    scorer = ChipScorer(engine=engine)
    result = scorer.score("000001")
"""

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from typing import Dict, Any, List, Optional

from ..config import get_config, get_db_url
from ..db.sql_utils import read_sql
from .base import BaseScorer


class ChipScorer(BaseScorer):
    """筹码面评分器 — 主力控盘模型"""

    name = "chip"
    label_zh = "筹码面"
    weight = 0.10
    max_raw = 15

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self._load_data()

    def _load_data(self):
        """加载全量筹码数据并预计算分位数"""
        self.df = read_sql(
            "SELECT code, closePrice, chipProfitRate, chipAvgCost, "
            "chipConcentration90, chipConcentration70 "
            "FROM chip_distribution",
            self.engine,
        )
        self.df = self.df.set_index("code")

        # 衍生指标
        # 成本偏离率: (closePrice - chipAvgCost) / chipAvgCost
        self.df["cost_deviation"] = np.where(
            self.df["chipAvgCost"] > 0,
            (self.df["closePrice"] - self.df["chipAvgCost"]) / self.df["chipAvgCost"],
            0
        )
        # 筹码集中度取均值
        self.df["concentration"] = (self.df["chipConcentration90"] + self.df["chipConcentration70"]) / 2

        # 预计算分位数
        self._pct = {}
        for col in ["chipProfitRate", "concentration", "cost_deviation"]:
            vals = self.df[col].dropna()
            if len(vals) > 0:
                self._pct[col] = {
                    p: np.percentile(vals, p) for p in [10, 20, 30, 40, 50, 60, 70, 80, 90]
                }

    # ============================================================
    #  1. 获利出清 (0-3) — 反向: 获利比例越低越好
    # ============================================================
    def _score_profit_clearance(self, profit_rate: float) -> int:
        """
        获利盘少 = 套牢盘多 = 锁定筹码 → 上升阻力小

        3分: 获利 < P30 (市场上最惨的30%)
        2分: 获利 < P50
        1分: 获利 < P70
        0分: 获利 > P70 (大部分人都赚钱了, 有获利了结压力)
        """
        if pd.isna(profit_rate):
            return 1
        pct = self._pct.get("chipProfitRate", {})
        if profit_rate <= pct.get(30, 1):
            return 3
        elif profit_rate <= pct.get(50, 10):
            return 2
        elif profit_rate <= pct.get(70, 50):
            return 1
        return 0

    # ============================================================
    #  2. 筹码集中 (0-3)
    # ============================================================
    def _score_concentration(self, concentration: float) -> int:
        """
        筹码集中度值越低 = 筹码越集中在少数账户 → 主力控盘

        3分: 集中度 < P30 (非常集中)
        2分: 集中度 < P50
        1分: 集中度 < P70
        0分: 集中度 > P70 (筹码分散)
        """
        if pd.isna(concentration):
            return 1
        pct = self._pct.get("concentration", {})
        if concentration <= pct.get(30, 5):
            return 3
        elif concentration <= pct.get(50, 10):
            return 2
        elif concentration <= pct.get(70, 15):
            return 1
        return 0

    # ============================================================
    #  3. 成本区间 (0-3)
    # ============================================================
    def _score_cost_position(self, deviation: float) -> int:
        """
        价格低于平均成本 = 大多数人亏损 = 超跌反弹机会

        3分: 偏离 < P30 (深度亏损, 超跌)
        2分: 偏离 < P50
        1分: 偏离 < P70
        0分: 偏离 > P70 (大家都在赚钱)
        """
        if pd.isna(deviation):
            return 1
        pct = self._pct.get("cost_deviation", {})
        if deviation <= pct.get(30, -0.15):
            return 3
        elif deviation <= pct.get(50, 0):
            return 2
        elif deviation <= pct.get(70, 0.1):
            return 1
        return 0

    # ============================================================
    #  4. 主力控盘 (0-3) — 综合集中+获利
    # ============================================================
    def _score_control(self, profit_rate: float, concentration: float) -> int:
        """
        筹码集中 + 获利少 = 主力高度控盘待拉升
        """
        if pd.isna(profit_rate) or pd.isna(concentration):
            return 1

        pct_p = self._pct.get("chipProfitRate", {})
        pct_c = self._pct.get("concentration", {})

        low_profit = profit_rate <= pct_p.get(50, 10)
        high_conc = concentration <= pct_c.get(50, 10)

        if low_profit and high_conc:
            return 3
        elif low_profit or high_conc:
            return 2
        return 1

    # ============================================================
    #  5. 筹码综合 (0-3)
    # ============================================================
    def _score_composite(self, sub_scores: dict) -> int:
        parts = [
            sub_scores.get("profit_clearance", 1),
            sub_scores.get("concentration", 1),
            sub_scores.get("cost_position", 1),
            sub_scores.get("control", 1),
        ]
        avg = np.mean(parts)
        if avg >= 2.5:
            return 3
        elif avg >= 2.0:
            return 2
        elif avg >= 1.5:
            return 1
        return 0

    # ============================================================
    #  主入口
    # ============================================================
    def score(self, code: str) -> Dict[str, Any]:
        if code not in self.df.index:
            return {
                "code": code, "total": 0, "weighted": 0.0,
                "sub_scores": {}, "error": "无筹码数据",
            }

        row = self.df.loc[code]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        profit = row.get("chipProfitRate", np.nan)
        conc = row.get("concentration", np.nan)
        dev = row.get("cost_deviation", np.nan)

        sub_scores = {
            "profit_clearance": self._score_profit_clearance(profit),
            "concentration": self._score_concentration(conc),
            "cost_position": self._score_cost_position(dev),
            "control": self._score_control(profit, conc),
        }
        sub_scores["composite"] = self._score_composite(sub_scores)

        total = sum(sub_scores.values())
        weighted = round(total / 15 * 20, 1)

        return {
            "code": code, "total": total, "weighted": weighted,
            "sub_scores": sub_scores, "error": None,
        }

    def batch_score(self, codes: List[str]) -> pd.DataFrame:
        results = []
        for code in codes:
            r = self.score(code)
            if r["error"] is None:
                results.append({
                    "code": code,
                    "total": r["total"],
                    "weighted": r["weighted"],
                    **{f"chip_{k}": v for k, v in r["sub_scores"].items()},
                })
        return pd.DataFrame(results)
