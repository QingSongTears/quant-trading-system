"""
策略 v3_dynamic — 超卖反转 + 动态止盈

基于 v3_reversal 基线，新增动态止盈逻辑：
  - 入场评分 ≥ 60 分 → 止盈+18%（强势反转，让利润奔跑）
  - 入场评分 40-60 → 止盈+15%（标准）
  - 入场评分 < 40 → 止盈+10%（弱势反转，见好就收）
  - RSI6 回升 ≥ 4 → 额外放宽止盈 2%
  - 高量比 (>2.0) → 额外放宽止盈 2%
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import List, Tuple
from backtest.engine import Signal, Trade, BaseStrategy


class V3ReversalDynamicStrategy(BaseStrategy):
    """v3 超卖反转 + 动态止盈"""

    name = "v3_dynamic"

    # === 超卖检测 (同v3基线) ===
    MAX_RSI_14 = 30
    MAX_RSI_6 = 20
    MAX_BB_POSITION = 0.08
    MAX_DRAWDOWN_60D = -15

    # === 反转确认 (同v3基线) ===
    MIN_PRICE_CHG = 1.0
    MIN_VOL_RATIO = 1.3
    RSI6_MIN_DELTA = 2.0

    # === 趋势位置 ===
    PRICE_BELOW_MA20 = True
    PRICE_BELOW_MA60 = True

    # === 风控 (基础值，动态调整) ===
    STOP_LOSS = -0.07
    TAKE_PROFIT_BASE = 0.15       # 标准止盈
    TAKE_PROFIT_STRONG = 0.18     # 强势止盈
    TAKE_PROFIT_WEAK = 0.10       # 弱势止盈
    TAKE_PROFIT_BONUS = 0.02      # 额外放宽（RSI强回升/高量比）
    TRAILING_STOP = 0.12
    TRAILING_DD = -0.03
    TIME_STOP_DAYS = 20
    TIME_STOP_RETURN = 0.02
    MIN_MCAP = 100

    # 动态止盈评分阈值
    SCORE_STRONG = 60
    SCORE_WEAK = 40

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
            turnover = info.get("turnover_pct", 0) or 0
            if turnover < 0.3:
                continue

            close = row["close"]
            open_p = row.get("open", close)

            # 超卖四条件
            rsi14 = row.get("rsi_14_d", 50) or 50
            rsi6 = row.get("rsi_6_d", 50) or 50
            bb_pos = row.get("bb_pos_d", 0.5)
            max_dd_60 = row.get("max_dd_60d", 0) or -100

            if pd.isna(rsi14) or pd.isna(rsi6) or pd.isna(bb_pos) or pd.isna(max_dd_60):
                continue
            if rsi14 > self.MAX_RSI_14: continue
            if rsi6 > self.MAX_RSI_6: continue
            if bb_pos is not None and bb_pos > self.MAX_BB_POSITION: continue
            if max_dd_60 > self.MAX_DRAWDOWN_60D: continue

            # 趋势位置
            ma20 = row.get("ma20_d", close)
            ma60 = row.get("ma60_d", close)
            if self.PRICE_BELOW_MA20 and close > ma20: continue
            if self.PRICE_BELOW_MA60 and close > ma60: continue

            # 反转确认
            prev_close = row.get("prev_close_d", close)
            price_chg_pct = (close / prev_close - 1) * 100 if prev_close > 0 else 0
            if price_chg_pct < self.MIN_PRICE_CHG: continue
            if close <= open_p: continue

            vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
            if vol_ratio < self.MIN_VOL_RATIO: continue

            prev_rsi6 = row.get("prev_rsi6_d", 50) or 50
            rsi6_delta = rsi6 - prev_rsi6
            if rsi6_delta < self.RSI6_MIN_DELTA: continue

            # 评分
            rsi_score = max(0, (self.MAX_RSI_14 - rsi14) / self.MAX_RSI_14) * 25
            bb_score = max(0, (self.MAX_BB_POSITION - (bb_pos or 0.5)) / 0.5) * 10
            dd_score = max(0, (-max_dd_60 + self.MAX_DRAWDOWN_60D) / 30) * 5
            reversal_score = min(price_chg_pct * 3, 15)
            rsi_turn_score = max(0, rsi6_delta) * 2
            vol_score = min(vol_ratio - 1, 1.5) * 10
            quality_score = min(mcap / 1000, 1.0) * 10

            score = rsi_score + bb_score + dd_score + reversal_score + rsi_turn_score + vol_score + quality_score

            # 计算动态止盈
            tp = self._calc_take_profit(score, rsi6_delta, vol_ratio)

            candidates.append({
                "code": code, "close": close,
                "name": info.get("name", ""),
                "score": round(score, 1),
                "extra": {
                    "rsi14": round(rsi14, 1), "bb_pos": round(bb_pos or 0, 3),
                    "max_dd_60": round(max_dd_60, 1), "price_chg": round(price_chg_pct, 2),
                    "vol_ratio": round(vol_ratio, 2), "mcap": mcap,
                    "dynamic_tp": tp, "rsi6_delta": round(rsi6_delta, 1),
                }
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        seen = set()
        unique = []
        for c in candidates:
            if c["code"] not in seen:
                seen.add(c["code"])
                unique.append(c)
            if len(unique) >= 16:
                break
        return [Signal(**c, strategy=self.name) for c in unique[:8]]

    def _calc_take_profit(self, score: float, rsi6_delta: float, vol_ratio: float) -> float:
        """动态计算止盈目标"""
        if score >= self.SCORE_STRONG:
            tp = self.TAKE_PROFIT_STRONG
        elif score >= self.SCORE_WEAK:
            tp = self.TAKE_PROFIT_BASE
        else:
            tp = self.TAKE_PROFIT_WEAK

        # 强RSI回升额外放宽
        if rsi6_delta >= 4:
            tp += self.TAKE_PROFIT_BONUS
        # 高量比额外放宽
        if vol_ratio >= 2.0:
            tp += self.TAKE_PROFIT_BONUS

        return round(min(tp, 0.22), 2)  # 上限22%

    # ---------- 持仓更新（动态止盈版）----------

    def update_positions(
        self, positions: List[dict], row: pd.Series, today, today_str: str
    ) -> Tuple[List[dict], List[Trade]]:
        closed, surviving = [], []
        for p in positions:
            cp = row["close"]
            hold = (today - pd.Timestamp(p["entry_date"])).days
            ret = (cp - p["entry_price"]) / p["entry_price"]

            high_water = p.get("high_water", p["entry_price"])
            if cp > high_water:
                p["high_water"] = cp
                high_water = cp
            highest_ret = (high_water - p["entry_price"]) / p["entry_price"]

            # 使用动态止盈（入场时计算并存储）
            tp = p.get("dynamic_tp", self.TAKE_PROFIT_BASE)

            # 止损
            if ret <= self.STOP_LOSS:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "stop_loss",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            # 动态止盈
            if ret >= tp:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, f"take_profit_{tp*100:.0f}pct",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            # 移动止损
            if highest_ret > self.TRAILING_STOP:
                trail_dd = (cp - high_water) / high_water
                if trail_dd <= self.TRAILING_DD:
                    t = Trade(p["code"], p.get("name", ""), self.name,
                              p["entry_date"], today_str,
                              p["entry_price"], cp, "trailing_stop",
                              ret * 100, hold, p["shares"])
                    closed.append(t)
                    continue

            # 时间止损
            if hold > self.TIME_STOP_DAYS and ret < self.TIME_STOP_RETURN:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "time_stop",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            surviving.append(p)
        return surviving, closed
