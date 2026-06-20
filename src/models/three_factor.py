"""
模型一：三因子均衡模型 (Three-Factor Equilibrium)
=================================================

定位: 长期持有基准，对标沪深300，追求年化超额 5-8%

设计理念:
A股被验证最有效的三个因子等权配置，不赌单一因子，依靠分散化平滑波动。

选股流程 (月度调仓):
Step 1: 剔除 ST/*ST、上市不足1年、股价<2元
Step 2: 剔除日均成交额 < 3000万
Step 3-A: 按估算市值升序 → 取前100只 (小市值因子, 33.3%)
Step 3-B: 按近20日收益率升序 → 取前100只 (反转因子, 33.3%)
Step 3-C: 按近252日波动率升序 → 取前100只 (低波动因子, 33.3%)
Step 4: 三组合并，等权持有，最多300只

因子来源:
- SMB因子: Fama & French (1993)
- 反转因子: Jegadeesh (1990)
- 低波动因子: Baker, Bradley & Wurgler (2011)
"""
from datetime import timedelta
from typing import List

import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy
from ..strategies.small_cap import SmallCapStrategy
from ..strategies.reversal import ReversalStrategy
from ..strategies.low_volatility import LowVolatilityStrategy
from ._list_date import add_days_since_list


class ThreeFactorStrategy(BaseSelectionStrategy):
    """三因子均衡模型 — 小市值 + 反转 + 低波动等权组合"""

    name: str = "三因子均衡"
    description: str = (
        "三因子等权组合：小市值(33%)+反转(33%)+低波动(33%)，"
        "月度调仓，最多300只等权持有。A股最稳健Alpha组合。"
    )
    source: str = (
        "Fama & French (1993); Jegadeesh (1990); "
        "Baker, Bradley & Wurgler (2011)"
    )

    # ── 模型参数 ──
    n_stocks: int = 300              # 最多300只 (每因子100)
    rebalance_days: int = 20         # 月度调仓
    lookback_days: int = 252         # 低波因子需要252日数据

    # 过滤参数
    min_amount_wan: float = 3000.0   # 日均成交额 ≥ 3000万
    min_price: float = 2.0           # 最低股价 2元
    min_list_days: int = 250         # 上市至少250个交易日(约1年)
    exclude_st: bool = True

    def __init__(self):
        super().__init__()
        # ── 子策略 (各选100只) ──
        self._small_cap = SmallCapStrategy()
        self._small_cap.n_stocks = 100

        self._reversal = ReversalStrategy()
        self._reversal.n_stocks = 100
        self._reversal.lookback_days = 20

        self._low_vol = LowVolatilityStrategy()
        self._low_vol.n_stocks = 100
        self._low_vol.lookback_days = 252
        self._low_vol.rebalance_days = 60

    def filter_universe(self, universe_df: pd.DataFrame) -> pd.DataFrame:
        """扩展过滤: 基础过滤 + 股价门槛 + 上市天数门槛"""
        df = super().filter_universe(universe_df)

        # 股价 ≥ min_price
        if "close" in df.columns:
            df = df[df["close"] >= self.min_price]

        # 上市 ≥ min_list_days 个自然日 (≈1年)
        # NaT / 缺失 list_date 自动填 99999 → 被 min_list_days 过滤掉
        if "list_date" in df.columns:
            df = add_days_since_list(df, "list_date")
            df = df[df["_days_since_list"] >= self.min_list_days]
            df = df.drop(columns=["_days_since_list"], errors="ignore")

        return df

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """
        三因子选股: 小市值100 + 反转100 + 低波动100 → 合并去重

        合并后去重但保持顺序。等权持有意味着每只股票 1/N，
        三因子自然各占约 33.3% (如有重叠则略有偏差)。
        """
        # 各子策略独立选股
        sc = self._small_cap.select(rebalance_date, universe_df)
        rv = self._reversal.select(rebalance_date, universe_df)
        lv = self._low_vol.select(rebalance_date, universe_df)

        # 合并去重 (保持顺序: 小市值在前，反转居中，低波在后)
        seen = set()
        combined = []
        for code in sc + rv + lv:
            if code not in seen:
                seen.add(code)
                combined.append(code)

        return combined[:self.n_stocks]
