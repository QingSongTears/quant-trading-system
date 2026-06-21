"""
模型五：极致小市值模型 (Extreme Small Cap)
=============================================

定位: 高风险高收益，适合卫星仓位，追求年化 15-25%

设计理念:
纯粹的小市值策略，五层过滤逐级筛选:
  层1: 全A股 (~5500只)
  层2: 剔除ST/股价<3元/上市<250天 → 剩~4700只
  层3: 剔除日均成交额<5000万 → 剩~3200只
  层4: 按估算市值升序取最小200只
  层5: ROE>5% ∧ 营收增速>10% → 取前50只 (待财务数据)

当前状态: 前四层已实现，第五层暂用市值排序替代 (取市值最小的50只)

风险控制:
  组合止损: -25%
  单只止损: -15%
  最大持仓: 50只
"""
from __future__ import annotations
from datetime import date


import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy
from ._list_date import add_days_since_list


class ExtremeSmallCapStrategy(BaseSelectionStrategy):
    """极致小市值模型 — 五层过滤选50只最小市值"""

    name: str = "极致小市值"
    description: str = (
        "五层过滤选最小50只: 剔除ST+低股价+次新+低流动，"
        "市值最小200中精选。适合卫星仓位。"
    )
    source: str = (
        "Fama & French (1993) SMB因子增强; "
        "Asness et al. (2019). Size Matters, If You Control Your Junk"
    )

    # ── 模型参数 ──
    n_stocks: int = 50               # 最终持仓50只
    rebalance_days: int = 20         # 月度调仓
    lookback_days: int = 20

    # 过滤参数
    min_amount_wan: float = 5000.0   # 层3: 日均成交额 ≥ 5000万
    min_price: float = 3.0           # 层2: 最低股价 3元
    min_list_days: int = 250         # 层2: 上市至少250交易日
    exclude_st: bool = True

    # 层4: 中间池大小 (取市值最小的前N只)
    candidate_pool: int = 200

    # 风控参数 (由 PortfolioBacktestEngine 或未来独立风控层处理)
    max_drawdown_exit: float = -0.25   # 组合回撤>25%清仓
    single_stop_loss: float = -0.15    # 单只止损 -15%

    def filter_universe(self, universe_df: pd.DataFrame) -> pd.DataFrame:
        """
        层2 + 层3 过滤:
          - 层2: 剔除 ST、股价 < min_price、上市 < min_list_days
          - 层3: 日均成交额 ≥ min_amount_wan
        """
        df = universe_df.copy()

        # ── 层2: 基础过滤 ──
        # 剔除 ST
        if self.exclude_st and "name" in df.columns:
            df = df[~df["name"].str.contains("ST", na=False)]

        # 股价门槛
        if "close" in df.columns:
            df = df[df["close"] >= self.min_price]

        # 上市天数 (NaT 自动填 99999 → 被 min_list_days 过滤)
        if "list_date" in df.columns:
            df = add_days_since_list(df, "list_date")
            df = df[df["_days_since_list"] >= self.min_list_days]
            df = df.drop(columns=["_days_since_list"], errors="ignore")

        # ── 层3: 流动性过滤 ──
        if "avg_amount_wan" in df.columns:
            df = df[df["avg_amount_wan"] >= self.min_amount_wan]

        # 剔除无效数据
        if "market_cap_yi" in df.columns:
            df = df[df["market_cap_yi"] > 0]
        if "close" in df.columns:
            df = df[df["close"] > 0]

        return df

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> list[str]:
        """
        层4 + 层5: 市值最小200 → 精选50

        当前层5使用市值排序替代 ROE/营收增速过滤。
        等 financial_data 表到位后，改为:
          df = df[(df["roe"] > 5) & (df["revenue_growth"] > 10)]
          selected = df.nsmallest(self.n_stocks, "market_cap_yi")
        """
        df = universe_df.copy()

        if "market_cap_yi" not in df.columns or df.empty:
            return []

        # ── 层4: 市值最小200只 ──
        df = df.sort_values("market_cap_yi", ascending=True)
        candidates = df.head(self.candidate_pool)

        # ── 层5: 精选50只 ──
        # TODO: 等 financial_data 就绪后加入 ROE/营收增速过滤
        # candidates = candidates[
        #     (candidates["roe"] > 5) & (candidates["revenue_growth"] > 10)
        # ]
        selected = candidates.head(self.n_stocks)["code"].tolist()

        return selected
