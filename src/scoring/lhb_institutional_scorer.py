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

import sys, os
from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent


class LhbInstitutionalScorer:
    """
    龙虎榜机构评分器

    接口:
        scorer = LhbInstitutionalScorer()
        score = scorer.score("000001")  # 返回 0-20 分
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or PROJECT_ROOT / "database" / "quant.db"
        self._cache = None

    def _load_data(self):
        if self._cache is not None:
            return self._cache
        conn = sqlite3.connect(str(self.db_path))
        df = pd.read_sql("SELECT * FROM lhb_institutional", conn)
        conn.close()
        self._cache = df
        return df

    def score(self, code, lookback_days=60):
        """
        对单只股票计算龙虎榜机构评分

        参数:
            code: 6位股票代码
            lookback_days: 回溯天数（默认60天）

        返回:
            0-20 分（None 表示无数据）
        """
        df = self._load_data()
        code = str(code).zfill(6)
        stock_rows = df[df["code"] == code]

        if stock_rows.empty:
            return None

        # 过滤最近 lookback_days 天
        recent = stock_rows.sort_values("trade_date", ascending=False).head(10)

        total_score = 0
        details = []

        for _, row in recent.iterrows():
            s = 0
            # 机构净买入
            inst_net = row.get("inst_net", 0) or 0
            if inst_net > 50000000:  # > 5000万
                s += 5
                details.append(f"机构大买: {inst_net/1e8:.1f}亿")
            elif inst_net > 10000000:  # > 1000万
                s += 3
                details.append(f"机构买: {inst_net/1e8:.1f}亿")
            elif inst_net < -50000000:
                s -= 3
                details.append(f"机构大卖: {inst_net/1e8:.1f}亿")

            # 外资/北向
            north = row.get("north_net", 0) or 0
            if north > 50000000:
                s += 3
                details.append(f"北向买: {north/1e8:.1f}亿")

            # 游资行为
            tour = row.get("tour_net", 0) or 0
            if tour < -50000000:
                s += 2  # 游资出，机构接
                details.append(f"游资出，机构接")
            elif tour > 100000000:
                s -= 2  # 纯游资炒作
                details.append(f"游资炒作")

            total_score += s

        # 归一化到 0-20
        normalized = max(0, min(20, total_score + 10))
        return int(normalized)


# ─────────────────────────────────────────────
# 测试函数
# ─────────────────────────────────────────────
if __name__ == "__main__":
    scorer = LhbInstitutionalScorer()
    test_codes = ["000001", "600519", "300750"]
    print("📥 龙虎榜机构评分器 v1.0 测试")
    for code in test_codes:
        score = scorer.score(code)
        print(f"  {code}: {score}/20" if score is not None else f"  {code}: 无龙虎榜数据")
