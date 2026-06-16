"""
低换手选股策略 (Low Turnover)
==============================

A股筹码锁定策略 — 流动性溢价。
选择日均换手率极低的股票，筹码集中意味着供给稀缺，少量买盘就能推涨。

核心逻辑:
1. 计算过去N日平均换手率
2. 按换手率升序排列 (最低换手在前)
3. 取换手率最低的N只
4. 额外过滤: 流通市值门槛 (排除仙股)

策略特点:
- 换手率极低 = 筹码锁定 = 供给稀缺
- 适合作为其他策略的叠加过滤器
- 在震荡市中表现最佳

来源: Datar, Naik & Radcliffe (1998). Liquidity and Stock Returns:
      An Alternative Test of Amihud and Mendelson's Model.
      Amihud & Mendelson (1986). Asset Pricing and the Bid-Ask Spread.
"""
from typing import List

import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy


class LowTurnoverStrategy(BaseSelectionStrategy):
    """低换手选股策略 — 选择换手率最低的N只股票"""

    name: str = "低换手选股"
    description: str = "选择换手率最低的N只股票，流动性溢价策略。筹码锁定信号。"
    source: str = "Datar, Naik & Radcliffe (1998). Liquidity and Stock Returns"

    # 策略参数
    n_stocks: int = 20
    rebalance_days: int = 60       # 季度调仓 (换手率变化慢)
    lookback_days: int = 60        # 60日平均换手率
    min_amount_wan: float = 3000
    min_market_cap_yi: float = 50  # 最小市值50亿 (排除仙股)
    max_turnover: float = 1.0      # 换手率上限(%) — 低于此值才入选
    exclude_st: bool = True

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """
        选股逻辑: 按日均换手率升序 → 取换手最低的 n_stocks 只

        额外过滤:
        - 换手率必须低于 max_turnover (默认1%)
        - 流通市值 >= min_market_cap_yi (默认50亿)
        """
        df = universe_df.copy()

        if "avg_turnover" not in df.columns:
            return []

        # 筛选: 换手率 <= max_turnover
        df = df[df["avg_turnover"] <= self.max_turnover]

        # 筛选: 市值 >= min_market_cap_yi
        if "market_cap_yi" in df.columns:
            df = df[df["market_cap_yi"] >= self.min_market_cap_yi]

        # 按日均换手率升序
        df = df.sort_values("avg_turnover", ascending=True)

        # 取换手最低的 n_stocks 只
        selected = df.head(self.n_stocks)["code"].tolist()

        return selected
