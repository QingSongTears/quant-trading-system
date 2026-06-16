"""
策略 v2 — 趋势确认 + 回调上车
基于100只机构稳步上涨票的研究结论

逻辑:
  1. 趋势确认: EMA20>EMA60, R²>0.65, 60日涨>8%, 回撤<28%
  2. 回调上车: 浅回踩(-5%~0%) + RSI 38-62 + 量比 0.6-1.8
  3. 风控: 止损-8%, 止盈+12%, EMA20跌破离场
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import List, Tuple
from backtest.engine import Signal, Trade, BaseStrategy


class V2TrendStrategy(BaseStrategy):
    """v2 趋势确认 + 回调上车"""

    name = "v2_trend"

    # === 趋势确认 ===
    MIN_R_SQUARED = 0.65
    MIN_RETURN_60D = 8.0
    MAX_DRAWDOWN = -28.0

    # === 回调上车 ===
    PULLBACK_LO = -5.0
    PULLBACK_HI = 0.0
    RSI_LO = 38
    RSI_HI = 62
    VOL_RATIO_LO = 0.6
    VOL_RATIO_HI = 1.8

    # === 风控 ===
    STOP_LOSS = -0.08
    TAKE_PROFIT = 0.12
    MIN_MCAP = 100

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k.upper()):
                setattr(self, k.upper(), v)

    # ---------- 扫描 ----------

    def scan(self, today_data: pd.DataFrame, today, context: dict) -> List[Signal]:
        excluded = context["excluded_codes"]
        spot_map = context.get("spot_map", {})
        held = context.get("held_codes", set())

        candidates = []
        for _, row in today_data.iterrows():
            code = row["code"]
            if code in excluded or code in held:
                continue

            info = spot_map.get(code, {})
            mcap = info.get("mcap_yi", 0) or 0
            if mcap < self.MIN_MCAP:
                continue

            # 趋势确认
            ema20 = row.get("ema20_d", 0)
            ema60 = row.get("ema60_d", 0)
            close = row["close"]
            if ema20 <= ema60 or pd.isna(ema20) or pd.isna(ema60):
                continue
            if close < ema20:
                continue

            r_sq = row.get("r2_60d", 0) or 0
            ret_60d = row.get("ret_60d_pct", 0) or 0
            max_dd = row.get("max_dd_60d", 0) or -100
            if r_sq < self.MIN_R_SQUARED: continue
            if ret_60d < self.MIN_RETURN_60D: continue
            if max_dd < self.MAX_DRAWDOWN: continue

            # 回调上车
            high_20d = row.get("high_20d", close)
            pullback = (close / high_20d - 1) * 100 if high_20d > 0 else 0
            if pullback < self.PULLBACK_LO or pullback > self.PULLBACK_HI:
                continue

            rsi14 = row.get("rsi_14_d", 50) or 50
            if rsi14 < self.RSI_LO or rsi14 > self.RSI_HI:
                continue

            vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
            if vol_ratio < self.VOL_RATIO_LO or vol_ratio > self.VOL_RATIO_HI:
                continue

            # 评分
            score = (r_sq * 30 + (ret_60d / 60 * 25) + (-pullback) * 15 +
                     (50 - abs(rsi14 - 50)) * 0.3)

            candidates.append({
                "code": code, "close": close, "name": info.get("name", ""),
                "score": round(score, 1), "extra": {}
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return [Signal(**c, strategy=self.name) for c in candidates[:5]]

    # ---------- 持仓更新 ----------

    def update_positions(
        self, positions: List[dict], row: pd.Series, today, today_str: str
    ) -> Tuple[List[dict], List[Trade]]:
        closed, surviving = [], []
        for p in positions:
            cp = row["close"]
            hold = (today - pd.Timestamp(p["entry_date"])).days
            ret = (cp - p["entry_price"]) / p["entry_price"]

            if ret <= self.STOP_LOSS:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "stop_loss",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            if ret >= self.TAKE_PROFIT:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "take_profit",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            ema20 = row.get("ema20_d", cp)
            if cp < ema20 and hold > 5:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "ema_exit",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            surviving.append(p)
        return surviving, closed
