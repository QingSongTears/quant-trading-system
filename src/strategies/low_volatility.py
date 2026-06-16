"""
低波动选股策略 (Low Volatility)
===============================

A股防御利器 — 低波动异象。
选择过去N日收益率波动率最低的股票，等权持有。

核心逻辑:
1. 计算过去N日收益率的日标准差
2. 按波动率升序排列 (最低波动在前)
3. 取波动率最低的N只
4. 流动性过滤

策略特点:
- 震荡市/熊市表现最佳
- 夏普比率通常比大盘高30-40%
- 牛市后期可能跑输

来源: Baker, Bradley & Wurgler (2011). Benchmarks as Limits to Arbitrage:
      Why Low Volatility Stocks Outperform.
      Haugen & Baker (1991). The Efficient Market Inefficiency of Capitalization-
      Weighted Stock Portfolios.
"""
from typing import List

import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy


class LowVolatilityStrategy(BaseSelectionStrategy):
    """低波动选股策略 — 选择波动率最低的N只股票"""

    name: str = "低波动选股"
    description: str = "选择波动率最低的N只股票，低波动异象策略。防御利器。"
    source: str = "Baker, Bradley & Wurgler (2011). Benchmarks as Limits to Arbitrage"

    # 策略参数
    n_stocks: int = 20
    rebalance_days: int = 60       # 季度调仓
    lookback_days: int = 60        # 60日波动率 (约3个月)
    min_amount_wan: float = 2000   # 日均成交额 >= 2000万
    exclude_st: bool = True

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """
        选股逻辑: 按波动率升序 → 取波动最低的 n_stocks 只
        """
        df = universe_df.copy()

        if "volatility_Nd" not in df.columns:
            return []

        # 按波动率升序
        df = df.sort_values("volatility_Nd", ascending=True)

        # 取波动最低的 n_stocks 只
        selected = df.head(self.n_stocks)["code"].tolist()

        return selected
