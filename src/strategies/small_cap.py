"""
小市值选股策略 (Small Cap)
===========================

A股最可靠 Alpha 来源 — 小市值溢价。
选择估算总市值最小的N只股票，等权持有，定期调仓。

核心逻辑:
1. 通过成交额/换手率估算总市值
2. 按市值升序排列，取最小N只
3. 流动性过滤: 日均成交额 >= 3000万
4. 剔除ST股票

来源: Fama & French (1993). Common Risk Factors in the Returns on Stocks and Bonds.
SMB (Small Minus Big) 因子在A股年化超额 5-8%。
"""
from typing import List

import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy


class SmallCapStrategy(BaseSelectionStrategy):
    """小市值选股策略 — 选择估算市值最小的N只股票"""

    name: str = "小市值选股"
    description: str = "选择估算市值最小的N只股票，定期调仓。A股最强因子。"
    source: str = "Fama & French (1993). Common Risk Factors in the Returns on Stocks and Bonds"

    # 策略参数
    n_stocks: int = 20
    rebalance_days: int = 20       # 月度调仓
    lookback_days: int = 20
    min_amount_wan: float = 3000  # 日均成交额 >= 3000万
    exclude_st: bool = True

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """
        选股逻辑: 按估算市值升序 → 取最小 n_stocks 只

        Args:
            rebalance_date: 当前调仓日
            universe_df: 已经过 filter_universe() 过滤的股票池

        Returns:
            选中的股票代码列表
        """
        df = universe_df.copy()

        # 按市值升序排列
        if "market_cap_yi" not in df.columns:
            return []

        df = df.sort_values("market_cap_yi", ascending=True)

        # 取最小 n_stocks 只
        selected = df.head(self.n_stocks)["code"].tolist()

        return selected
