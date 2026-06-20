"""
策略 v5 — 超卖反转 + 趋势确认 双重验证
v3检测超卖 → v2确认趋势回升 → 买入

融合逻辑:
  1. 超卖检测 (v3): RSI低 + BB下轨 + 大幅回撤 + 反弹确认
  2. 趋势过滤 (v2): EMA20>EMA60 OR R²>0.5 OR 60日跌幅收窄
  3. 双重验证后才买入 — 避免抄底抄在半山腰

预期: 比v3信号多(放宽超卖门槛) + 比v2准确(有超卖前提)
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import List, Tuple
from backtest.engine import Signal, Trade, BaseStrategy


class V5HybridStrategy(BaseStrategy):
    """v5 超卖反转 + 趋势确认"""

    name = "v5_hybrid"

    # === 超卖检测 (v3放宽版，#54 参数扫描优化) ===
    MAX_RSI_14 = 38          # ✅ 最优: 38 (夏普 0.81, 年化+6.5%)
    MAX_RSI_6 = 26           # ✅ 最优: 26 (固定不变)
    MAX_BB_POSITION = 0.10   # ✅ 最优: 0.10 (0.15→0.10, 更严格)
    MAX_DRAWDOWN_60D = -8    # ✅ 最优: -8 (-12%→-8%, 略微放宽)

    # === 反转确认 (v3) ===
    MIN_PRICE_CHG = 0.8      # 至少涨0.8%
    MIN_VOL_RATIO = 1.2      # 量比≥1.2
    RSI6_MIN_DELTA = 1.5     # RSI6回升≥1.5

    # === 趋势确认 (v2简化版) ===
    # 至少满足 3 条件中的 TREND_CHECKS_MIN 个
    MIN_R2_60D = 0.45        # R²≥0.45（微弱趋势即可）
    MIN_EMA_ALIGN = True     # EMA20>EMA60
    MAX_RET_60D = -8         # 60日跌幅≤8%（跌幅已收窄）
    TREND_CHECKS_MIN = 2     # 需要满足的条件数（可配置为1放宽）

    # === 风控 (#67 缩小仓位: 10%->6%) ===
    STOP_LOSS = -0.07
    TAKE_PROFIT = 0.15
    TRAILING_STOP = 0.12
    TRAILING_DD = -0.03
    TIME_STOP_DAYS = 22
    TIME_STOP_RETURN = 0.02
    MIN_MCAP = 100
    POSITION_PCT = 0.08          # 由0.10降至0.08，平衡回撤与收益

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k.upper()):
                setattr(self, k.upper(), v)

    # ---------- 扫描 ----------

    def scan(self, today_data: pd.DataFrame, today, context: dict) -> List[Signal]:
        excluded = context["excluded_codes"]
        spot_map = context.get("spot_map", {})
        held = context.get("held_codes", set())

        # === 市场广度过滤 ===
        breadth = context.get("breadth")
        if breadth is not None and not breadth.get("pass", True):
            return []  # 市场不好，不开仓

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

            # ===== Phase 1: 超卖检测 (v3放宽) =====
            rsi14 = row.get("rsi_14_d", 50) or 50
            rsi6 = row.get("rsi_6_d", 50) or 50
            bb_pos = row.get("bb_pos_d", 0.5)
            max_dd_60 = row.get("max_dd_60d", 0) or -100

            if pd.isna(rsi14) or pd.isna(rsi6): continue
            if rsi14 > self.MAX_RSI_14: continue
            if rsi6 > self.MAX_RSI_6: continue
            if bb_pos is not None and bb_pos > self.MAX_BB_POSITION: continue
            if max_dd_60 > self.MAX_DRAWDOWN_60D: continue

            # ===== Phase 2: 反转确认 =====
            prev_close = row.get("prev_close_d", close)
            price_chg = (close / prev_close - 1) * 100 if prev_close > 0 else 0
            if price_chg < self.MIN_PRICE_CHG: continue

            vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
            if vol_ratio < self.MIN_VOL_RATIO: continue

            prev_rsi6 = row.get("prev_rsi6_d", 50) or 50
            rsi6_delta = rsi6 - prev_rsi6
            if rsi6_delta < self.RSI6_MIN_DELTA: continue

            # ===== Phase 3: 趋势确认 (v2简化) =====
            # 至少满足3个条件中的2个
            trend_checks = 0

            # 条件A: EMA多头
            ema20 = row.get("ema20_d", 0)
            ema60 = row.get("ema60_d", 0)
            if not pd.isna(ema20) and not pd.isna(ema60) and ema20 > ema60:
                trend_checks += 1

            # 条件B: R²趋势存在
            r2_60d = row.get("r2_60d", 0) or 0
            if r2_60d >= self.MIN_R2_60D:
                trend_checks += 1

            # 条件C: 60日跌幅已收窄
            ret_60d = row.get("ret_60d_pct", -100) or -100
            if ret_60d > self.MAX_RET_60D:
                trend_checks += 1

            if trend_checks < self.TREND_CHECKS_MIN:
                continue  # 趋势确认不通过

            # ===== Phase 4: 评分 =====
            rsi_score = max(0, (self.MAX_RSI_14 - rsi14) / self.MAX_RSI_14) * 20
            bb_score = max(0, (self.MAX_BB_POSITION - (bb_pos or 0.5)) / 0.5) * 10
            dd_score = max(0, (-max_dd_60 + self.MAX_DRAWDOWN_60D) / 25) * 5
            reversal_score = min(price_chg * 3, 15)
            vol_score = min(vol_ratio - 1, 1.5) * 8
            trend_score = (r2_60d * 10 + min(ret_60d / 5, 5) * 2)
            quality_score = min(mcap / 1000, 1.0) * 10

            score = rsi_score + bb_score + dd_score + reversal_score + vol_score + trend_score + quality_score

            candidates.append({
                "code": code, "close": close,
                "name": info.get("name", ""), "score": round(score, 1),
                "extra": {"rsi14": round(rsi14, 1), "bb_pos": round(bb_pos or 0, 3),
                          "trend_checks": trend_checks}
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

    # ---------- 持仓更新 (同v3) ----------

    def update_positions(self, positions: List[dict], row: pd.Series, today, today_str: str
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
