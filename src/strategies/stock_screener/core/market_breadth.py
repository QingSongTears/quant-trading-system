"""
市场广度过滤器 — 大盘择时
在全线暴跌时空仓，降低最大回撤

逻辑: 每日计算全市场上涨比例，低于阈值时不开新仓
用法: 注入到 BacktestEngine 的 context 中
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import Optional


class MarketBreadthFilter:
    """
    市场广度过滤器

    计算指标:
    - up_ratio: 上涨家数占比
    - adv_decline: 涨跌比
    - mcap_weighted_up: 市值加权上涨比例
    """

    def __init__(
        self,
        min_up_ratio: float = 0.40,     # 上涨比例<40%空仓
        min_adv_decline: float = 0.7,   # 涨跌比<0.7空仓
        lookback_days: int = 5,          # SMA平滑窗口
    ):
        self.min_up_ratio = min_up_ratio
        self.min_adv_decline = min_adv_decline
        self.lookback_days = lookback_days

    def compute(self, today_data: pd.DataFrame, spot_map: dict) -> dict:
        """
        计算今日市场广度

        参数:
            today_data: 当日全市场K线 DataFrame (含 prev_close_d)
            spot_map: code→{name, mcap_yi, ...}

        返回:
            {
                "up_ratio": float,       # 上涨占比
                "adv_decline": float,    # 涨跌比
                "pass": bool,            # 是否通过过滤
                "reason": str,           # 不通过原因
            }
        """
        if today_data.empty:
            return {"up_ratio": 0, "adv_decline": 0, "pass": False, "reason": "无数据"}

        closes = today_data["close"].values
        prevs = today_data.get("prev_close_d", pd.Series(closes)).values

        # 过滤无效值
        valid = (~np.isnan(closes)) & (~np.isnan(prevs)) & (prevs > 0)
        if valid.sum() < 50:
            return {"up_ratio": 0, "adv_decline": 0, "pass": False, "reason": "有效样本不足"}

        changes = closes[valid] / prevs[valid] - 1
        up_count = (changes > 0).sum()
        down_count = (changes < 0).sum()
        total = len(changes)

        up_ratio = up_count / total
        adv_decline = up_count / max(down_count, 1)

        # 通过条件
        if up_ratio < self.min_up_ratio:
            return {"up_ratio": round(up_ratio, 3), "adv_decline": round(adv_decline, 2),
                    "pass": False, "reason": f"上涨占比{up_ratio:.1%}<{self.min_up_ratio:.0%}",
                    "up_count": int(up_count), "total": total}
        if adv_decline < self.min_adv_decline:
            return {"up_ratio": round(up_ratio, 3), "adv_decline": round(adv_decline, 2),
                    "pass": False, "reason": f"涨跌比{adv_decline:.2f}<{self.min_adv_decline}",
                    "up_count": int(up_count), "total": total}

        return {"up_ratio": round(up_ratio, 3), "adv_decline": round(adv_decline, 2),
                "pass": True, "reason": "OK",
                "up_count": int(up_count), "total": total}

    def compute_smoothed(self, kline: pd.DataFrame, today, spot_map: dict) -> dict:
        """
        计算今日市场广度(含SMA平滑) - 更稳健
        """
        today_str = today.strftime("%Y-%m-%d")
        days = sorted(kline["date"].unique())
        today_idx = days.index(today) if today in days else -1
        if today_idx < 0:
            return self.compute(kline[kline["date"] == today], spot_map)

        # 取近N个交易日
        start_idx = max(0, today_idx - self.lookback_days + 1)
        recent = days[start_idx:today_idx + 1]

        ratios = []
        for d in recent:
            day_data = kline[kline["date"] == d]
            if day_data.empty:
                continue
            closes = day_data["close"].values
            prevs = day_data.get("prev_close_d", pd.Series(closes)).values
            valid = (~np.isnan(closes)) & (~np.isnan(prevs)) & (prevs > 0)
            if valid.sum() < 50:
                continue
            chg = closes[valid] / prevs[valid] - 1
            ratios.append((chg > 0).sum() / len(chg))

        if not ratios:
            return {"up_ratio": 0, "adv_decline": 0, "pass": False, "reason": "历史数据不足"}

        # SMA平滑
        sma_up_ratio = np.mean(ratios)

        # 重新计算今日涨跌比
        today_data = kline[kline["date"] == today]
        raw = self.compute(today_data, spot_map)

        if sma_up_ratio < self.min_up_ratio:
            raw["smoothed_up_ratio"] = round(sma_up_ratio, 3)
            raw["pass"] = False
            raw["reason"] = f"SMA上涨占比{sma_up_ratio:.1%}<{self.min_up_ratio:.0%}"
        else:
            raw["smoothed_up_ratio"] = round(sma_up_ratio, 3)

        return raw
