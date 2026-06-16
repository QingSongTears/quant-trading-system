"""
市场温度计 (Market Thermometer)
================================

独立工具模块 — 判断当前A股市场状态 (进攻/中性/防御)，
用于 M2 盾+矛模型的动态仓位调节。

判断逻辑 (基于沪深300指数):
  进攻 🔥: 收盘价 > MA60 且 MA20 > MA60   → 趋势向上，加大进攻仓位
  中性 🌤️: 收盘价在 MA60 ±3% 区间内       → 震荡市，均衡配置
  防御 🧊: 收盘价 < MA60 且 MA20 < MA60   → 趋势向下，加大防御仓位

使用:
  from .market_thermometer import MarketThermometer
  thermo = MarketThermometer()
  regime = thermo.judge(as_of_date)  # → "bull" / "neutral" / "bear"
"""
import logging
from datetime import date, timedelta
from typing import Dict, Literal

import pandas as pd

from ..config import get_config
from ..models.repository import DataRepository

logger = logging.getLogger(__name__)

Regime = Literal["bull", "neutral", "bear"]


class MarketThermometer:
    """
    市场温度计 — 基于沪深300均线判断市场状态

    配置参数:
      ma_fast: int = 20   快速均线
      ma_slow: int = 60   慢速均线
      neutral_band: float = 0.03  中性区间 (±3%)
      index_code: str = "000300"  沪深300
    """

    def __init__(self):
        config = get_config()
        self.repo = DataRepository()
        self.ma_fast = 20
        self.ma_slow = 60
        self.neutral_band = 0.03
        self.index_code = config["backtest"]["benchmark"]

    def judge(self, as_of_date: date) -> Regime:
        """
        判断 as_of_date 当天的市场状态

        Returns:
            "bull"  — 进攻 (趋势向上)
            "neutral" — 中性 (震荡)
            "bear"  — 防御 (趋势向下)
        """
        try:
            # 加载足够长的历史数据以计算 MA60
            lookback = as_of_date - timedelta(days=365)
            df = self.repo.get_benchmark_data(
                self.index_code, lookback, as_of_date
            )
            if df.empty or len(df) < self.ma_slow:
                return "neutral"  # 数据不足，默认中性

            close = df["close"]
            ma_fast_val = close.iloc[-self.ma_fast:].mean()
            ma_slow_val = close.iloc[-self.ma_slow:].mean()
            current_close = close.iloc[-1]

            # 判断
            deviation = (current_close - ma_slow_val) / ma_slow_val

            if current_close > ma_slow_val and ma_fast_val > ma_slow_val:
                return "bull"
            elif current_close < ma_slow_val and ma_fast_val < ma_slow_val:
                return "bear"
            elif abs(deviation) <= self.neutral_band:
                return "neutral"
            else:
                # 均线方向不一致 → 偏中性
                return "neutral"

        except Exception as e:
            logger.warning(f"温度计判断失败 ({as_of_date}): {e}")
            return "neutral"

    def get_allocation(self, regime: Regime) -> Dict[str, float]:
        """
        根据市场状态返回 盾:矛 配比

        Returns:
            {"shield": 0.70, "spear": 0.30}   (防御)
            {"shield": 0.50, "spear": 0.50}   (中性)
            {"shield": 0.30, "spear": 0.70}   (进攻)
        """
        allocations = {
            "bull":    {"shield": 0.30, "spear": 0.70},
            "neutral": {"shield": 0.50, "spear": 0.50},
            "bear":    {"shield": 0.70, "spear": 0.30},
        }
        return allocations[regime]

    def get_regime_name(self, regime: Regime) -> str:
        names = {"bull": "🔥 进攻", "neutral": "🌤️ 中性", "bear": "🧊 防御"}
        return names.get(regime, "未知")
