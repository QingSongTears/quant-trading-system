"""
策略 v6 — v3超卖反转 + 结构性改进

基于优化结论（v3止损-5%是最优单参数，但-79%回撤无法靠参数调优解决），
从策略层面加入三项改进:

1. 连续2日阳线确认 — 今日+昨日都收阳，防假反转
2. ATR波动率自适应头寸 — 高波动股自动减仓（由引擎处理）
3. 量价背离过滤 — 放量下跌时拒绝入场

预期: 降低假信号率 → 降低回撤
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import List, Tuple
from backtest.engine import Signal, Trade, BaseStrategy


class V6ImprovedStrategy(BaseStrategy):
    """v6 v3改进 — 连续阳线 + 量价过滤"""

    name = "v6_improved"

    # === 超卖检测 (同v3) ===
    MAX_RSI_14 = 30
    MAX_RSI_6 = 20
    MAX_BB_POSITION = 0.08
    MAX_DRAWDOWN_60D = -15

    # === 反转确认 (加强版) ===
    MIN_PRICE_CHG = 1.0
    MIN_VOL_RATIO = 1.3
    RSI6_MIN_DELTA = 2.0
    CONSECUTIVE_UP = 1        # 连续阳线天数（1=关闭/v3原版, 2=开启/交易过少16→46笔）

    # === 量价过滤 ===
    MAX_VOL_DOWN_CHG = -2.0    # 放量>1.5倍且跌>2%拒绝入场

    # === 趋势位置 ===
    PRICE_BELOW_MA20 = True
    PRICE_BELOW_MA60 = True

    # === 风控 (v3优化版) ===
    STOP_LOSS = -0.05
    TAKE_PROFIT = 0.15
    TRAILING_STOP = 0.12
    TRAILING_DD = -0.03
    TIME_STOP_DAYS = 20
    TIME_STOP_RETURN = 0.02
    MIN_MCAP = 100

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k.upper()):
                setattr(self, k.upper(), v)

    def scan(self, today_data: pd.DataFrame, today, context: dict) -> List[Signal]:
        excluded = context["excluded_codes"]
        spot_map = context.get("spot_map", {})
        held = context.get("held_codes", set())

        # 构建前一日索引（用于连续阳线判断）
        prev_day_map = {}
        if self.CONSECUTIVE_UP >= 2:
            for _, row in today_data.iterrows():
                prev_chg = row.get("prev_chg_d", 0) or 0
                prev_day_map[row["code"]] = prev_chg

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

            # 🔥 v6新增: 连续阳线确认
            if self.CONSECUTIVE_UP >= 2:
                prev_chg = prev_day_map.get(code, 0)
                if prev_chg <= 0:  # 昨日未收阳
                    continue

            vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
            if vol_ratio < self.MIN_VOL_RATIO: continue

            # 🔥 v6新增: 量价背离过滤 — 放量下跌时拒绝
            if vol_ratio > 1.5 and price_chg_pct < self.MAX_VOL_DOWN_CHG:
                continue

            prev_rsi6 = row.get("prev_rsi6_d", 50) or 50
            rsi6_delta = rsi6 - prev_rsi6
            if rsi6_delta < self.RSI6_MIN_DELTA: continue

            # ATR百分比（用于引擎自适应头寸）
            atr_pct = row.get("atr_pct_d", 2.0) or 2.0

            # 评分（加入ATR惩罚）
            rsi_score = max(0, (self.MAX_RSI_14 - rsi14) / self.MAX_RSI_14) * 25
            bb_score = max(0, (self.MAX_BB_POSITION - (bb_pos or 0.5)) / 0.5) * 10
            dd_score = max(0, (-max_dd_60 + self.MAX_DRAWDOWN_60D) / 30) * 5
            reversal_score = min(price_chg_pct * 3, 15)
            rsi_turn_score = max(0, rsi6_delta) * 2
            vol_score = min(vol_ratio - 1, 1.5) * 10
            quality_score = min(mcap / 1000, 1.0) * 10
            atr_penalty = max(0, (5 - atr_pct)) * 2  # 高波动扣分

            score = rsi_score + bb_score + dd_score + reversal_score + rsi_turn_score + vol_score + quality_score - atr_penalty

            candidates.append({
                "code": code, "close": close,
                "name": info.get("name", ""),
                "score": round(score, 1),
                "extra": {
                    "rsi14": round(rsi14, 1), "bb_pos": round(bb_pos or 0, 3),
                    "max_dd_60": round(max_dd_60, 1), "price_chg": round(price_chg_pct, 2),
                    "vol_ratio": round(vol_ratio, 2), "mcap": mcap,
                    "atr_pct": round(atr_pct, 2),  # 引擎用来自适应头寸
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

    # 持仓更新（同v3）
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

            if ret <= self.STOP_LOSS:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str, p["entry_price"], cp,
                          "stop_loss", ret * 100, hold, p["shares"])
                closed.append(t); continue
            if ret >= self.TAKE_PROFIT:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str, p["entry_price"], cp,
                          "take_profit", ret * 100, hold, p["shares"])
                closed.append(t); continue
            if highest_ret > self.TRAILING_STOP:
                trail_dd = (cp - high_water) / high_water
                if trail_dd <= self.TRAILING_DD:
                    t = Trade(p["code"], p.get("name", ""), self.name,
                              p["entry_date"], today_str, p["entry_price"], cp,
                              "trailing_stop", ret * 100, hold, p["shares"])
                    closed.append(t); continue
            if hold > self.TIME_STOP_DAYS and ret < self.TIME_STOP_RETURN:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str, p["entry_price"], cp,
                          "time_stop", ret * 100, hold, p["shares"])
                closed.append(t); continue
            surviving.append(p)
        return surviving, closed
