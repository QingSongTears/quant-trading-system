"""
技术面评分器 v3 — 极端反转模型 (权重 20%)
=============================================

版本演进:
  v1 (追涨):  奖励强势信号 → 牛股评分反而更低 ❌
  v2 (潜伏反转): 奖励温和回调 → 温和信号不如极端信号 ❌
  v3 (极端反转): 奖励极端超卖+底背离 → 基于3.1万样本实证 ✅

核心洞察 (3.1万样本归因分析):
  - 极端超卖信号预测更高回报（深度回调+12%, 胜率61% > 温和回调+8%, 胜率56%）
  - MACD底背离是唯一跨牛/熊/震荡周期正预测的维度 (熊市+0.100, 牛市+0.054)
  - ma_trend=1 (水下初转) 胜率69.1%，远高于ma_trend=3 (均线收敛) 胜率57.6%
  - 结论: 在极端中找反转，而非在温和中找潜伏

7个子指标 (每项 0-3 分，满分 21，归一化到 0-20):
  1. MA趋势 (0-3): 极端偏离后的初转信号 — 水下初转 > 均线收敛
  2. MACD   (0-3): 底背离 + 底部收敛 — v2逻辑保留 (最有效维度)
  3. RSI    (0-3): 极端超卖恢复 — <30反弹 > 35-50蓄力
  4. 布林带  (0-3): 下轨极端位置 — 触及/跌破下轨 > 下轨附近
  5. 量价配合 (0-3): 地量止跌 — v2逻辑保留
  6. 突破形态 (0-3): 窄幅整理未突破 — v2逻辑保留
  7. 回调深度 (0-3): 深度回调反弹 — 15-25% > 5-15% (INVERTED!)

用法:
    scorer = TechnicalScorer()
    result = scorer.score(code="000001", as_of_date="2024-06-15")
"""
from datetime import date, timedelta
from typing import Dict, Optional, Any, List, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from ..config import get_config, get_db_url


class TechnicalScorer:
    """技术面评分器 v3 — 极端反转模型"""

    def __init__(self, engine=None):
        if engine is None:
            config = get_config()
            db_url = get_db_url(config)
            self.engine = create_engine(db_url, echo=False)
        else:
            self.engine = engine

    # ============================================================
    #  数据加载
    # ============================================================
    def _load_price_data(
        self, code: str, as_of_date_str: str, lookback_days: int = 150
    ) -> pd.DataFrame:
        query = f"""
            SELECT trade_date, open, high, low, close, volume, amount, pct_change
            FROM daily_price
            WHERE code = '{code}'
              AND trade_date <= '{as_of_date_str}'
            ORDER BY trade_date DESC
            LIMIT {lookback_days}
        """
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return df
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    def _load_bulk_price_data(
        self, codes: List[str], as_of_date_str: str, lookback_days: int = 150
    ) -> Dict[str, pd.DataFrame]:
        if not codes:
            return {}
        codes_str = "', '".join(codes)
        query = f"""
            SELECT code, trade_date, open, high, low, close, volume, amount, pct_change
            FROM daily_price
            WHERE code IN ('{codes_str}')
              AND trade_date <= '{as_of_date_str}'
            ORDER BY code, trade_date DESC
        """
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return {}

        result = {}
        for code, group in df.groupby("code"):
            group = group.sort_values("trade_date").reset_index(drop=True)
            group["trade_date"] = pd.to_datetime(group["trade_date"])
            if len(group) >= 60:
                result[code] = group.tail(lookback_days)
        return result

    # ============================================================
    #  1. MA趋势评分 (0-3) — 极端偏离后的初转
    # ============================================================
    def _score_ma_trend(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 奖励极端偏离后的初转信号。

        实证: ma_trend=1 (水下初转) 胜率69.1%, +14.8%收益
              ma_trend=3 (均线收敛) 胜率57.6%, +8.7%收益
              ma_trend=0 (深度空头) 胜率61.2%, +10.8%收益

        结论: 深度空头和初转信号都优于温和收敛。

        3分: 深度空头偏离后出现初转 (价格远在MA60下方, 但短期开始反弹)
        2分: 水下初转 (价格在MA60下方但站上MA20, 或MA20斜率转正)
        1分: 价格在MA60上方但短期回调中 (回踩支撑)
        0分: 温和多头排列 (v1/v2的高分区间, 但实证显示回报平庸)
        """
        if len(df) < 60:
            return 0

        close = df["close"].values.astype(np.float64)
        ma20 = pd.Series(close).rolling(20).mean().values
        ma60 = pd.Series(close).rolling(60).mean().values

        latest_close = close[-1]
        latest_ma20 = ma20[-1]
        latest_ma60 = ma60[-1]

        if np.isnan(latest_ma20) or np.isnan(latest_ma60):
            return 0

        # 斜率
        if len(ma20) >= 6 and ma20[-6] > 0:
            ma20_slope_5d = (ma20[-1] - ma20[-6]) / ma20[-6]
        else:
            ma20_slope_5d = 0

        # 价格 vs MA60 偏离度
        if latest_ma60 > 0:
            deviation = (latest_close - latest_ma60) / latest_ma60
        else:
            deviation = 0

        # 最近3日价格走势
        close_3d_ago = close[-4] if len(close) >= 4 else close[-1]
        price_rising = latest_close > close_3d_ago

        # 5日最低价对比
        low_5d = np.min(close[-5:])

        # === 3分: 深度空头偏离 + 初转信号 ===
        # 价格远在MA60下方 (>10%), 但最近3日反弹, 或短期低点抬高
        deep_bearish = deviation < -0.08
        early_reversal = price_rising or latest_close > low_5d * 1.02
        if deep_bearish and early_reversal:
            return 3

        # === 2分: 水下初转 ===
        # 价格在MA60下方但MA20开始走平/上翘, 或价格刚突破MA20
        if deviation < 0:
            if ma20_slope_5d > -0.005 and price_rising:
                return 2
            if latest_close > latest_ma20 and ma20_slope_5d > -0.01:
                return 2

        # === 1分: 回踩支撑 ===
        # 价格在MA60上方但短期偏弱 (牛市回调)
        if deviation > 0 and deviation < 0.10 and ma20_slope_5d < 0.01:
            return 1

        # === 0分: 温和多头 (平庸回报区间) ===
        # 价格在MA60上方且MA20>MA60 — 看起来很健康但后续回报平庸
        return 0

    # ============================================================
    #  2. MACD 评分 (0-3) — 底背离 + 底部收敛
    # ============================================================
    def _score_macd(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 保留 v2，这是最有效的维度。

        实证: macd=3 (底背离/即将金叉) +16.5%收益, 胜率67.5%
              macd在熊市相关性+0.100, 牛市+0.054

        3分: 底背离（价格创新低但DIF未创新低），
             或 DIF从下方快速接近DEA即将金叉 + 柱状线连续收窄
        2分: DIF < DEA 但 DIF斜率明显向上（底部收敛中）
        1分: DIF > DEA (已金叉) 但在零轴附近（未大幅拉升）
        0分: DIF < DEA < 0，柱状线仍在放大（加速下跌）
        """
        if len(df) < 35:
            return 0

        close = df["close"].values.astype(np.float64)
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        dif = ema12 - ema26
        dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
        macd_bar = 2 * (dif - dea)

        latest_dif = dif[-1]
        latest_dea = dea[-1]
        latest_bar = macd_bar[-1]
        prev_bar = macd_bar[-2] if len(macd_bar) >= 2 else 0
        prev2_bar = macd_bar[-3] if len(macd_bar) >= 3 else 0

        if np.isnan(latest_dif) or np.isnan(latest_dea):
            return 0

        # DIF斜率
        if len(dif) >= 6:
            dif_slope_5d = (dif[-1] - dif[-6]) / (abs(dif[-6]) + 0.01)
        else:
            dif_slope_5d = 0

        # === 底背离检测 ===
        price_20d_low = np.min(close[-20:])
        price_40d_low = np.min(close[-40:-20]) if len(close) >= 40 else price_20d_low
        dif_20d_low = np.min(dif[-20:])
        dif_40d_low = np.min(dif[-40:-20]) if len(dif) >= 40 else dif_20d_low

        has_bottom_divergence = (
            price_20d_low < price_40d_low * 0.98
            and dif_20d_low > dif_40d_low
        )

        # === 即将金叉 ===
        about_to_cross = (
            latest_dif < latest_dea
            and (latest_dea - latest_dif) < abs(latest_dea) * 0.15
            and dif_slope_5d > 0
        )

        # === 柱状线连续收窄 ===
        bar_shrinking = (
            latest_bar < 0
            and latest_bar > prev_bar
            and prev_bar > prev2_bar
        )

        # === 3分 ===
        if has_bottom_divergence or (about_to_cross and bar_shrinking):
            return 3

        # === 2分: 底部收敛 ===
        if latest_dif < latest_dea and dif_slope_5d > 0.02:
            return 2

        # === 1分: 已金叉/零轴附近 ===
        if latest_dif > latest_dea:
            dif_vs_close = abs(latest_dif) / (abs(close[-1]) * 0.01 + 0.01)
            if dif_vs_close < 3.0:
                return 1
            return 1

        # === 0分: 加速下跌 ===
        if latest_dif < latest_dea < 0 and latest_bar < prev_bar:
            return 0

        return 1

    # ============================================================
    #  3. RSI 评分 (0-3) — 极端超卖恢复
    # ============================================================
    def _score_rsi(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 奖励极端超卖恢复，而非温和蓄力。

        实证: RSI维度与收益相关性很弱（~0），但极端值有方向性。
              RSI<30 超卖反弹的收益高于 RSI 35-50 "蓄力区间"。

        3分: RSI < 30，极端超卖（恐慌性抛售后反弹潜力最大）
        2分: RSI 30-40，超卖恢复初期
        1分: RSI 40-55，中性区间
        0分: RSI > 70，超买（过热追高风险）
        """
        if len(df) < 15:
            return 0

        close = df["close"].values.astype(np.float64)
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = pd.Series(gains).rolling(14).mean().values[-1]
        avg_loss = pd.Series(losses).rolling(14).mean().values[-1]

        if np.isnan(avg_gain) or np.isnan(avg_loss):
            return 1
        if avg_loss == 0:
            return 0  # 无下跌 = 极强势 = 过热

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # === 3分: 极端超卖 ===
        if rsi < 30:
            return 3

        # === 2分: 超卖恢复 ===
        if 30 <= rsi < 40:
            return 2

        # === 1分: 中性 ===
        if 40 <= rsi <= 60:
            return 1

        # === 0分: 超买 ===
        if rsi > 70:
            return 0

        # 60-70: 偏强但未过热
        return 1

    # ============================================================
    #  4. 布林带评分 (0-3) — 下轨极端位置
    # ============================================================
    def _score_bollinger(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 奖励触及/跌破下轨，而非下轨附近企稳。

        实证: bollinger维度相关性弱，但极端下轨位置的反弹
              幅度大于温和的下轨附近位置。

        3分: 触及或跌破下轨（位置<0.1），极端超卖
        2分: 下轨附近（位置0.1-0.3），超卖区间
        1分: 下半区（位置0.3-0.5），偏弱
        0分: 中轨以上（位置>0.5），无超卖优势
        """
        if len(df) < 20:
            return 0

        close = df["close"].values.astype(np.float64)
        ma20 = pd.Series(close).rolling(20).mean().values
        std20 = pd.Series(close).rolling(20).std().values

        latest_close = close[-1]
        latest_ma = ma20[-1]
        latest_std = std20[-1]

        if np.isnan(latest_ma) or np.isnan(latest_std) or latest_std == 0:
            return 0

        upper = latest_ma + 2 * latest_std
        lower = latest_ma - 2 * latest_std
        boll_position = (latest_close - lower) / (upper - lower) if upper != lower else 0.5

        # 最近走势
        close_1d = close[-2] if len(close) >= 2 else close[-1]
        today_up = latest_close > close_1d

        # === 3分: 触及/跌破下轨 ===
        if boll_position <= 0.10:
            # 如果今天反弹收阳，加分
            return 3

        # === 2分: 下轨附近 ===
        if 0.10 < boll_position <= 0.30:
            return 2

        # === 1分: 下半区 ===
        if 0.30 < boll_position <= 0.50:
            return 1

        # === 0分: 中轨以上 ===
        return 0

    # ============================================================
    #  5. 量价配合评分 (0-3) — 地量止跌
    # ============================================================
    def _score_volume_price(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 保留 v2 地量止跌逻辑，微调阈值。

        实证: volume_price=3 (地量止跌) +10.0%收益, 胜率58.8%
              volume_price=2 (缩量回调) +9.3%收益, 胜率57.5%

        3分: 地量止跌 — 量在20日最低区 + 价格横盘微涨
        2分: 缩量回调 — 量缩30%+ + 价格微跌（洗盘蓄力）
        1分: 量价平稳 — 正常范围
        0分: 异常放量 — 放量暴跌或天量冲顶
        """
        if len(df) < 10:
            return 0

        volume = df["volume"].values.astype(np.float64)
        close = df["close"].values.astype(np.float64)

        recent5_vol = volume[-5:]
        prev5_vol = volume[-10:-5] if len(volume) >= 10 else volume[:5]

        avg_vol_recent = np.mean(recent5_vol)
        avg_vol_prev = np.mean(prev5_vol)

        if avg_vol_prev == 0:
            return 1

        vol_ratio = avg_vol_recent / avg_vol_prev
        price_change_5d = (close[-1] - close[-5]) / close[-5] if close[-5] != 0 else 0

        # 20日量能分析
        if len(volume) >= 20:
            vol_20d = volume[-20:]
            vol_20d_p20 = np.percentile(vol_20d, 20)
            is_volume_floor = avg_vol_recent <= vol_20d_p20 * 1.05
            vol_trend = (np.mean(vol_20d[-10:]) - np.mean(vol_20d[:10])) / np.mean(vol_20d[:10]) if np.mean(vol_20d[:10]) > 0 else 0
        else:
            vol_20d_min = np.min(volume)
            is_volume_floor = avg_vol_recent < vol_20d_min * 1.2
            vol_trend = 0

        # === 3分: 地量止跌 ===
        if is_volume_floor and -0.02 < price_change_5d <= 0.03:
            return 3

        # === 2分: 缩量回调/缩量横盘 ===
        if vol_ratio < 0.70 and -0.05 <= price_change_5d <= 0.02:
            return 2
        if vol_trend < -0.10 and -0.03 <= price_change_5d <= 0.03:
            return 2

        # === 1分: 量价平稳 ===
        if 0.70 <= vol_ratio <= 1.50 and -0.04 <= price_change_5d <= 0.04:
            return 1

        # === 0分: 异常放量 ===
        if price_change_5d < -0.03 and vol_ratio > 1.5:
            return 0
        if price_change_5d > 0.05 and vol_ratio > 2.5:
            return 0
        if vol_ratio > 1.5 and -0.01 <= price_change_5d <= 0.01:
            return 0

        if vol_ratio < 0.85:
            return 2
        elif vol_ratio > 1.8:
            return 0
        return 1

    # ============================================================
    #  6. 突破形态评分 (0-3) — 窄幅整理未突破
    # ============================================================
    def _score_breakout(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 保留 v2 窄幅整理逻辑。

        实证: breakout=3 (窄幅整理未突破) +9.9%收益, 胜率60.6%
              breakout=1 (刚突破10日高) +7.2%收益, 胜率50.4% ← 最差!

        3分: 未突破20日高点，前20日振幅<12%（窄幅整理，蓄势待发）
        2分: 未突破20日高点，振幅12-20%（宽幅整理）
        1分: 已突破20日高点（鱼身已过半）
        0分: 持续新低且振幅大（趋势破坏）
        """
        if len(df) < 30:
            return 0

        close = df["close"].values.astype(np.float64)
        latest = close[-1]

        high_20d = np.max(close[-20:])
        low_20d = np.min(close[-20:])

        if high_20d == 0:
            return 0

        range_20d = (high_20d - low_20d) / high_20d
        broke_20d = latest >= high_20d * 0.995

        # === 3分: 窄幅整理未突破 ===
        if not broke_20d and range_20d < 0.12:
            return 3

        # === 2分: 宽幅整理未突破 ===
        if not broke_20d and 0.12 <= range_20d < 0.20:
            return 2

        # === 1分: 已突破 (鱼身已过) ===
        if broke_20d:
            return 1

        # === 0分: 持续低迷 ===
        if latest < low_20d * 1.01 and range_20d > 0.15:
            return 0

        return 1

    # ============================================================
    #  7. 回调深度评分 (0-3) — 深度回调反弹 (INVERTED!)
    # ============================================================
    def _score_pullback(self, df: pd.DataFrame) -> int:
        """
        v3 逻辑 — 完全反转！深度回调 > 温和回调。

        实证: pullback=0 (深度回调>25%) +12.0%收益, 胜率61.3%
              pullback=3 (温和回调5-15%) +8.3%收益, 胜率55.6%
              深度回调后反弹更强，均值回归效应明显。

        3分: 深度回调 15-25%（超跌反弹的最佳区间）
        2分: 极端回调 >25%（可能过度，但反弹幅度也大）
        1分: 温和回调 5-15%（平庸回报）
        0分: 浅回调 <5% 或处于高位（无回调=无反弹空间）
        """
        if len(df) < 60:
            return 0

        close = df["close"].values.astype(np.float64)
        high_60d = np.max(close[-60:])
        latest = close[-1]

        if high_60d == 0:
            return 0

        drawdown = (high_60d - latest) / high_60d

        # === 3分: 深度回调 15-25% ===
        if 0.15 < drawdown <= 0.25:
            return 3

        # === 2分: 极端回调 >25% ===
        if drawdown > 0.25:
            return 2

        # === 1分: 温和回调 5-15% ===
        if 0.05 < drawdown <= 0.15:
            return 1

        # === 0分: 浅回调 <5% (处于高位) ===
        return 0

    # ============================================================
    #  主评分入口
    # ============================================================
    def score(self, code: str, as_of_date: str) -> Dict[str, Any]:
        df = self._load_price_data(code, as_of_date)

        if df.empty or len(df) < 60:
            return {
                "code": code,
                "as_of_date": as_of_date,
                "total": 0,
                "weighted": 0.0,
                "sub_scores": {},
                "error": "数据不足" if df.empty else f"仅{len(df)}交易日, 需≥60",
            }

        sub_scores = {
            "ma_trend": self._score_ma_trend(df),
            "macd": self._score_macd(df),
            "rsi": self._score_rsi(df),
            "bollinger": self._score_bollinger(df),
            "volume_price": self._score_volume_price(df),
            "breakout": self._score_breakout(df),
            "pullback": self._score_pullback(df),
        }

        total = sum(sub_scores.values())
        weighted = round(total / 21 * 20, 1)

        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": total,
            "weighted": weighted,
            "sub_scores": sub_scores,
            "error": None,
        }

    def batch_score(
        self, codes, as_of_date: Optional[str] = None, verbose: bool = False
    ) -> pd.DataFrame:
        # 统一接口适配: 兼容旧版 codes_and_dates: List[Tuple[str, str]]
        if as_of_date is None and isinstance(codes, list) and codes and isinstance(codes[0], (list, tuple)):
            codes_and_dates = codes
        else:
            codes_and_dates = [(c, as_of_date) for c in codes]
        results = []
        for i, (code, dt) in enumerate(codes_and_dates):
            r = self.score(code, dt)
            if r["error"] is None and r["sub_scores"]:
                results.append({
                    "code": code,
                    "as_of_date": dt,
                    "total": r["total"],
                    "weighted": r["weighted"],
                    **{f"tech_{k}": v for k, v in r["sub_scores"].items()},
                })
            if verbose and (i + 1) % 50 == 0:
                print(f"  ... 已评分 {i + 1}/{len(codes_and_dates)}")
        return pd.DataFrame(results)

    def sample_daily(
        self,
        start_date: str = "2024-06-01",
        end_date: str = "2026-06-01",
        sample_freq: str = "weekly",
        stock_codes: Optional[List[str]] = None,
        max_stocks_per_day: int = 200,
    ) -> pd.DataFrame:
        """逐日/逐周采样"""
        dates_query = f"""
            SELECT DISTINCT trade_date FROM daily_price
            WHERE trade_date >= '{start_date}' AND trade_date <= '{end_date}'
            ORDER BY trade_date
        """
        all_dates = pd.read_sql(dates_query, self.engine)["trade_date"].tolist()

        if sample_freq == "weekly":
            all_dates = pd.DatetimeIndex([pd.Timestamp(d) for d in all_dates])
            df_d = pd.DataFrame({"date": all_dates})
            df_d["week"] = df_d["date"].dt.isocalendar().week
            df_d["year"] = df_d["date"].dt.isocalendar().year
            sample_dates = [str(d.date()) for d in df_d.groupby(["year", "week"])["date"].max()]
        else:
            sample_dates = all_dates

        print(f"采样: {sample_freq}, {len(sample_dates)} 日, {sample_dates[0]}~{sample_dates[-1]}")

        all_results = []
        total_scored = 0

        for i, sample_date in enumerate(sample_dates):
            codes_sql = f"SELECT DISTINCT code FROM daily_price WHERE trade_date = '{sample_date}'"
            if stock_codes:
                codes_sql += f" AND code IN ('{stock_codes}')"
            available = pd.read_sql(codes_sql, self.engine)["code"].tolist()

            if len(available) > max_stocks_per_day:
                np.random.seed(i)
                available = np.random.choice(available, max_stocks_per_day, replace=False).tolist()

            bulk_data = self._load_bulk_price_data(available, sample_date)
            for code, df in bulk_data.items():
                sub = {
                    "ma_trend": self._score_ma_trend(df),
                    "macd": self._score_macd(df),
                    "rsi": self._score_rsi(df),
                    "bollinger": self._score_bollinger(df),
                    "volume_price": self._score_volume_price(df),
                    "breakout": self._score_breakout(df),
                    "pullback": self._score_pullback(df),
                }
                total = sum(sub.values())
                all_results.append({
                    "code": code,
                    "as_of_date": sample_date,
                    "total": total,
                    "weighted": round(total / 21 * 20, 1),
                    **{f"tech_{k}": v for k, v in sub.items()},
                })
                total_scored += 1

            if (i + 1) % 10 == 0 or i == 0:
                print(f"  [{i+1}/{len(sample_dates)}] {sample_date}: {len(bulk_data)}只, 累计{total_scored}")

        print(f"完成: {total_scored} 条")
        return pd.DataFrame(all_results)
