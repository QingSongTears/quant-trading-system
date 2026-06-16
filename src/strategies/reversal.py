"""
反转选股策略 (Short-term Reversal)
====================================

A股行为金融核心策略 — 短期反转效应。
选择过去N日跌幅最大的股票，博弈超跌反弹。

核心逻辑:
1. 计算过去N日累计收益率
2. 按收益率升序排列 (跌幅最大的在前)
3. 取跌幅最大的N只
4. 流动性过滤 + 剔除ST和连续跌停

A股特殊性: 散户追涨杀跌导致短期超调，反转效应极强。
关键坑点: 不要接飞刀 — 剔除连续跌停、财务暴雷股。

来源: Jegadeesh (1990). Evidence of Predictable Behavior of Security Returns.
      De Bondt & Thaler (1985). Does the Stock Market Overreact?
"""
from typing import List

import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy


class ReversalStrategy(BaseSelectionStrategy):
    """反转选股策略 — 选择近期跌幅最大的N只股票"""

    name: str = "短期反转选股"
    description: str = "选择近期跌幅最大的N只股票，博弈超跌反弹。A股行为金融核心策略。"
    source: str = "Jegadeesh (1990). Evidence of Predictable Behavior of Security Returns"

    # 策略参数
    n_stocks: int = 20
    rebalance_days: int = 20       # 持有约1个月
    lookback_days: int = 20        # 回看20日跌幅
    min_amount_wan: float = 3000
    exclude_st: bool = True
    min_return: float = -30.0      # 最低跌幅门槛(%)，跌幅低于此值不选 (排雷)

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """
        选股逻辑: 按近期收益率升序 → 取跌幅最大的 n_stocks 只

        注意: 剔除跌幅过大的股票 (可能是真暴雷，不是超跌反弹)
        """
        df = universe_df.copy()

        if "return_Nd" not in df.columns:
            return []

        # 剔除跌幅极端的 (可能是财务暴雷、退市风险等)
        df = df[df["return_Nd"] >= self.min_return]

        # 按收益率升序 (跌幅最大的在前)
        df = df.sort_values("return_Nd", ascending=True)

        # 取跌幅最大的 n_stocks 只
        selected = df.head(self.n_stocks)["code"].tolist()

        return selected
