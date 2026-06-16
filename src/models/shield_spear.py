"""
模型二：盾+矛全天候模型 (Shield & Spear All-Weather)
======================================================

定位: 攻守兼备，熊市不崩、牛市跟上

设计理念:
将策略分为防守组（低波动 + 未来:红利/质量）和进攻组（小市值+反转），
通过市场温度计动态调节两组权重。

运作流程:
  每月初 → 温度计判断市场状态 → 调节盾矛配比 → 执行选股

市场温度计（决定盾/矛配比）:
  进攻 🔥: 盾30:矛70  (熊市震荡后抄底最佳窗口)
  中性 🌤️: 盾50:矛50  (均衡配置)
  防御 🧊: 盾70:矛30  (降低风险敞口)

防守组（盾）— 当前:
  低波动: 100% (等红利/质量财务数据到位后扩展)

进攻组（矛）:
  小市值: 55%
  反转:   45%

当前状态: 矛端完整，盾端用低波动代理。
          红利(股息率)/质量(ROE) 等待 financial_data 表扩展。
"""
from datetime import date
from typing import List

import pandas as pd

from ..backtest.base_selection_strategy import BaseSelectionStrategy
from ..strategies.small_cap import SmallCapStrategy
from ..strategies.reversal import ReversalStrategy
from ..strategies.low_volatility import LowVolatilityStrategy
from .market_thermometer import MarketThermometer, Regime


class ShieldSpearStrategy(BaseSelectionStrategy):
    """盾+矛全天候模型 — 温度计动态调节攻守配比"""

    name: str = "盾+矛全天候"
    description: str = (
        "市场温度计动态调节：进攻(盾30:矛70)/中性(50:50)/防御(70:30)。"
        "盾=低波动代理，矛=小市值+反转。熊市抗跌、牛市跟涨。"
    )
    source: str = (
        "市场温度计: 基于沪深300 MA20/MA60 趋势判断; "
        "矛端: Fama & French (1993) + Jegadeesh (1990); "
        "盾端: Baker, Bradley & Wurgler (2011)"
    )

    # ── 模型参数 ──
    n_stocks: int = 200              # 总持仓 ~200只
    rebalance_days: int = 20         # 月度调仓
    lookback_days: int = 60

    # 过滤参数
    min_amount_wan: float = 3000.0
    min_price: float = 2.0
    min_list_days: int = 250
    exclude_st: bool = True

    # ── 矛端权重 ──
    spear_small_cap_pct: float = 0.55   # 小市值占矛端55%
    spear_reversal_pct: float = 0.45    # 反转占矛端45%

    def __init__(self):
        super().__init__()
        self._thermometer = MarketThermometer()

        # ── 子策略 ──
        self._small_cap = SmallCapStrategy()
        self._reversal = ReversalStrategy()
        self._low_vol = LowVolatilityStrategy()

        # 子策略参数: 不设 n_stocks，由 select() 动态分配

    def filter_universe(self, universe_df: pd.DataFrame) -> pd.DataFrame:
        """通用过滤 + 盾矛额外过滤"""
        df = super().filter_universe(universe_df)

        if "close" in df.columns:
            df = df[df["close"] >= self.min_price]

        if "list_date" in df.columns:
            df["_list_date_dt"] = pd.to_datetime(df["list_date"])
            today = date.today()
            df["_days_since_list"] = df["_list_date_dt"].apply(
                lambda d: (today - d.date()).days if hasattr(d, 'date') else 9999
            )
            df = df[df["_days_since_list"] >= self.min_list_days]
            df = df.drop(columns=["_list_date_dt", "_days_since_list"], errors="ignore")

        return df

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """
        温度计调节盾矛配比 → 执行选股

        流程:
          1. 判断市场状态 → 盾:矛配比
          2. 矛端: 小市值 + 反转按比例选股
          3. 盾端: 低波动选股
          4. 合并去重
        """
        # ── 1. 温度计判断 ──
        dt = rebalance_date.date() if hasattr(rebalance_date, 'date') else rebalance_date
        regime = self._thermometer.judge(dt)
        alloc = self._thermometer.get_allocation(regime)

        spear_slots = int(self.n_stocks * alloc["spear"])
        shield_slots = self.n_stocks - spear_slots

        # ── 2. 矛端选股 ──
        sc_slots = int(spear_slots * self.spear_small_cap_pct)
        rv_slots = spear_slots - sc_slots

        self._small_cap.n_stocks = sc_slots
        self._reversal.n_stocks = rv_slots
        self._low_vol.n_stocks = shield_slots

        # 各子策略独立过滤 (盾端可以有不同的过滤参数)
        sc_filtered = self._small_cap.filter_universe(universe_df)
        rv_filtered = self._reversal.filter_universe(universe_df)
        lv_filtered = self._low_vol.filter_universe(universe_df)

        sc_picks = self._small_cap.select(rebalance_date, sc_filtered) if sc_slots > 0 else []
        rv_picks = self._reversal.select(rebalance_date, rv_filtered) if rv_slots > 0 else []
        lv_picks = self._low_vol.select(rebalance_date, lv_filtered) if shield_slots > 0 else []

        # ── 3. 合并去重 (矛在前，盾在后) ──
        seen = set()
        combined = []
        for code in sc_picks + rv_picks + lv_picks:
            if code not in seen:
                seen.add(code)
                combined.append(code)

        return combined[:self.n_stocks]

    def get_current_regime(self, as_of_date: date = None) -> Regime:
        """公开方法: 获取当前市场状态（供前端/日志使用）"""
        if as_of_date is None:
            as_of_date = date.today()
        return self._thermometer.judge(as_of_date)
