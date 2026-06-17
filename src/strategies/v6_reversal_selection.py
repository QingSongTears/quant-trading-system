"""
V6 超卖反转选股策略 — 新架构桥接版
====================================

将 v6_improved 策略的核心信号逻辑移植到 BaseSelectionStrategy 接口，
使用 quant.db 作为数据源，通过 PortfolioBacktestEngine 运行组合级回测。

注意：
  - 新引擎使用定期等权调仓模式，不支持个股级别的止损/止盈/移动止损
  - 这是一个"信号质量"版本，仅比较选股能力
  - 与旧 v6 回测结果不可直接对比（退出机制不同）

策略来源:
  v3_reversal 实证优化 → v6: 连续阳线确认 + ATR波动率自适应 + 量价背离过滤
"""
from typing import List, Optional
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from ..backtest.base_selection_strategy import BaseSelectionStrategy
from ..config import get_config, get_db_url
from ..models.repository import DataRepository


class V6ReversalSelectionStrategy(BaseSelectionStrategy):
    """
    V6 超卖反转选股策略 — 新架构版

    信号逻辑（v3核心 + v6增强）:
    1. 超卖四条件: RSI(14)≤30, RSI(6)≤20, BB位置≤0.08, 60日回撤≤-15%
    2. 趋势位置: 股价在MA20/MA60之下
    3. 反转确认: 当日涨幅≥1%, 收盘>开盘, 放量≥1.3倍
    4. v6增强: 连续阳线确认(可选), 量价背离过滤
    5. 评分排名: RSI得分+BB得分+回撤得分+反转强度+量比+ATR惩罚
    """

    name: str = "v6_reversal"
    description: str = (
        "V6 超卖反转 — RSI超卖+BB挤压+60日深回撤 → 反转确认 "
        "+ 连续阳线 + 量价过滤 + ATR波动率评价"
    )
    source: str = "v3_reversal 实证优化 → v6: ATR自适应头寸+连续阳线+量价过滤"

    # 选股参数
    n_stocks: int = 8
    rebalance_days: int = 1          # 每日扫描
    lookback_days: int = 120         # 需要足够的历史计算60日回撤和MA60

    # === v6 超卖检测参数 ===
    MAX_RSI_14: float = 30.0
    MAX_RSI_6: float = 20.0
    MAX_BB_POSITION: float = 0.08
    MAX_DRAWDOWN_60D: float = -15.0

    # === 反转确认参数 ===
    MIN_PRICE_CHG: float = 1.0       # 最小当日涨幅(%)
    MIN_VOL_RATIO: float = 1.3       # 最小量比

    # === v6 增强参数 ===
    CONSECUTIVE_UP: int = 1          # 连续阳线天数(1=关闭)
    MAX_VOL_DOWN_CHG: float = -2.0   # 放量下跌阈值

    # === 趋势位置 ===
    PRICE_BELOW_MA20: bool = True
    PRICE_BELOW_MA60: bool = True

    # === 评分权重 ===
    SCORE_RSI_WEIGHT: float = 25.0
    SCORE_BB_WEIGHT: float = 10.0
    SCORE_DD_WEIGHT: float = 5.0
    SCORE_REVERSAL_WEIGHT: float = 3.0   # per % chg
    SCORE_VOL_WEIGHT: float = 10.0
    SCORE_QUALITY_WEIGHT: float = 10.0
    ATR_PENALTY_PER_PCT: float = 2.0

    def __init__(self, **kwargs):
        # 应用自定义参数
        for k, v in kwargs.items():
            attr = k.upper()
            if hasattr(self, attr):
                setattr(self, attr, v)

        # 初始化数据库连接
        config = get_config()
        db_url = get_db_url(config)
        self.engine = create_engine(db_url, echo=False)

        # 指标缓存: {date_str: DataFrame(index=code)}
        self._indicator_cache: dict = {}

    def precompute_all(self, start_date, end_date):
        """
        批量预计算所有日期的v6指标。
        一次性加载全市场数据 + 按日期滚动计算指标 → 大幅加速回测。
        """
        from datetime import timedelta
        data_start = start_date - timedelta(days=self.lookback_days * 2)

        query = f"""
            SELECT code, trade_date, open, high, low, close, volume
            FROM daily_price
            WHERE trade_date >= '{data_start}'
              AND trade_date <= '{end_date}'
            ORDER BY code, trade_date
        """
        print(f"  [预计算] 加载数据...")
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["code", "trade_date"])

        all_dates = sorted(df["trade_date"].unique())
        backtest_dates = [d for d in all_dates if d.date() >= start_date]

        print(f"  [预计算] {len(backtest_dates)} 个交易日, 计算指标...")
        computed = 0
        for dt in backtest_dates:
            date_str = str(dt.date())[:10]
            cutoff = df[df["trade_date"] <= dt]
            recent = cutoff.groupby("code").tail(self.lookback_days)

            result_rows = []
            for code, grp in recent.groupby("code"):
                if len(grp) < 20:
                    continue
                grp = grp.sort_values("trade_date").reset_index(drop=True)
                close_arr = grp["close"].values.astype(float)
                high_arr = grp["high"].values.astype(float)
                low_arr = grp["low"].values.astype(float)
                volume_arr = grp["volume"].values.astype(float)

                row = {"code": code, "close": close_arr[-1],
                       "open": float(grp["open"].iloc[-1])}
                row["rsi_14"] = self._calc_rsi(close_arr, 14)
                row["rsi_6"] = self._calc_rsi(close_arr, 6)
                row["prev_rsi6"] = self._calc_rsi(close_arr[:-1], 6) if len(close_arr) >= 7 else row["rsi_6"]
                bb = self._calc_bollinger(close_arr, 20, 2)
                row.update(bb)
                row["ma20"] = np.mean(close_arr[-20:]) if len(close_arr) >= 20 else close_arr[-1]
                row["ma60"] = np.mean(close_arr[-60:]) if len(close_arr) >= 60 else close_arr[-1]
                row["max_dd_60d"] = self._calc_max_dd(high_arr, 60)
                row["prev_close"] = close_arr[-2] if len(close_arr) >= 2 else close_arr[-1]
                row["prev_chg"] = (close_arr[-1] - close_arr[-2]) / close_arr[-2] * 100 \
                    if len(close_arr) >= 2 and close_arr[-2] > 0 else 0
                if len(volume_arr) >= 6:
                    avg_vol_5 = np.mean(volume_arr[-6:-1])
                    row["vol_ratio"] = volume_arr[-1] / avg_vol_5 if avg_vol_5 > 0 else 1.0
                else:
                    row["vol_ratio"] = 1.0
                row["atr_pct"] = self._calc_atr_pct(high_arr, low_arr, close_arr, 14)
                result_rows.append(row)

            if result_rows:
                self._indicator_cache[date_str] = pd.DataFrame(result_rows).set_index("code")
            computed += 1
            if computed % 30 == 0:
                print(f"  [预计算] {computed}/{len(backtest_dates)} 完成")

        print(f"  [预计算] 完成: {len(self._indicator_cache)} 个日期已缓存")

    def filter_universe(self, universe_df: pd.DataFrame) -> pd.DataFrame:
        """
        覆盖父类过滤逻辑 — 不依赖 market_cap_yi (CSV数据无换手率, NaN)
        使用 avg_amount_wan 做流动性过滤
        """
        df = universe_df.copy()

        # 1. 流动性过滤: 日均成交额 >= 3000万
        if "avg_amount_wan" in df.columns:
            df = df[df["avg_amount_wan"] >= self.min_amount_wan]

        # 2. 剔除ST
        if self.exclude_st and "name" in df.columns:
            df = df[~df["name"].str.contains("ST", na=False)]

        # 3. 剔除市值为0或NaN — 但CSV数据无换手率导致全NaN
        #    改为仅过滤有 market_cap_yi 且 <= 0 的情况
        if "market_cap_yi" in df.columns:
            has_mcap = df["market_cap_yi"].notna()
            if has_mcap.any():
                df = df[~has_mcap | (df["market_cap_yi"] > 0)]

        return df

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
        """执行v6选股（优先使用预计算缓存）"""
        if universe_df.empty:
            return []

        date_str = str(rebalance_date.date()) if hasattr(rebalance_date, 'date') else str(rebalance_date)[:10]

        # 从缓存获取指标
        indicators_df = self._indicator_cache.get(date_str)
        if indicators_df is None:
            codes = universe_df["code"].tolist()
            if not codes:
                return []
            indicators_df = self._compute_indicators(codes, date_str)

        if indicators_df is None or indicators_df.empty:
            return []

        # 只保留 universe 中的股票
        universe_codes = set(universe_df["code"].tolist())
        mask = indicators_df.index.isin(universe_codes)
        indicators_df = indicators_df[mask].reset_index()

        if indicators_df.empty:
            return []

        signals = self._detect_signals(indicators_df, universe_df)
        signals.sort(key=lambda x: x["score"], reverse=True)
        return [s["code"] for s in signals[:self.n_stocks]]

    # ================================================================
    #  技术指标计算
    # ================================================================

    def _compute_indicators(self, codes: List[str], as_of_date: str) -> pd.DataFrame:
        """
        从 quant.db 加载价格数据并计算v6所需的所有技术指标

        所需指标:
          - rsi_14, rsi_6
          - bb_lower, bb_upper, bb_pos = (close - lower) / (upper - lower)
          - ma20, ma60
          - max_dd_60d (60日最高点回撤)
          - prev_close (昨日收盘)
          - prev_chg (昨日涨跌幅)
          - vol_ratio (量比 = volume / 5日均量)
          - atr_14, atr_pct (ATR/close*100)
        """
        # 查询足够的历史数据 (往前120个交易日)
        codes_str = ",".join([f"'{c}'" for c in codes])

        query = f"""
            SELECT code, trade_date, open, high, low, close, volume
            FROM daily_price
            WHERE code IN ({codes_str})
              AND trade_date <= '{as_of_date}'
            ORDER BY code, trade_date
        """
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return pd.DataFrame()

        df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 只保留每个code最近120行
        df = df.sort_values(["code", "trade_date"])
        df = df.groupby("code").tail(self.lookback_days)

        # 按code分组计算指标
        result_rows = []
        for code, grp in df.groupby("code"):
            if len(grp) < 20:  # 最少需要20天
                continue

            grp = grp.sort_values("trade_date").reset_index(drop=True)
            close = grp["close"].values.astype(float)
            high = grp["high"].values.astype(float)
            low = grp["low"].values.astype(float)
            volume = grp["volume"].values.astype(float)

            # 取最新一天的数据
            latest = {
                "code": code,
                "close": close[-1],
                "open": float(grp["open"].iloc[-1]),
            }

            # RSI(14) 和 RSI(6)
            latest["rsi_14"] = self._calc_rsi(close, 14)
            latest["rsi_6"] = self._calc_rsi(close, 6)

            # 前一日的RSI6
            if len(close) >= 7:
                latest["prev_rsi6"] = self._calc_rsi(close[:-1], 6)
            else:
                latest["prev_rsi6"] = latest.get("rsi_6", 50)

            # Bollinger Bands (20, 2)
            bb = self._calc_bollinger(close, 20, 2)
            latest.update(bb)

            # MA20, MA60
            latest["ma20"] = np.mean(close[-20:]) if len(close) >= 20 else close[-1]
            latest["ma60"] = np.mean(close[-60:]) if len(close) >= 60 else close[-1]

            # 60日最大回撤
            latest["max_dd_60d"] = self._calc_max_dd(high, 60)

            # 前一日收盘
            latest["prev_close"] = close[-2] if len(close) >= 2 else close[-1]

            # 昨日涨跌幅
            if len(close) >= 2 and close[-2] > 0:
                latest["prev_chg"] = (close[-1] - close[-2]) / close[-2] * 100
            else:
                latest["prev_chg"] = 0

            # 量比 (volume / 5日均量)
            if len(volume) >= 6:
                avg_vol_5 = np.mean(volume[-6:-1])
                latest["vol_ratio"] = volume[-1] / avg_vol_5 if avg_vol_5 > 0 else 1.0
            else:
                latest["vol_ratio"] = 1.0

            # ATR(14)
            latest["atr_pct"] = self._calc_atr_pct(high, low, close, 14)

            result_rows.append(latest)

        if not result_rows:
            return pd.DataFrame()

        result = pd.DataFrame(result_rows)
        return result

    # ================================================================
    #  信号检测
    # ================================================================

    def _detect_signals(self, indicators: pd.DataFrame,
                        universe: pd.DataFrame) -> List[dict]:
        """应用v6信号逻辑筛选候选股"""
        candidates = []

        for _, row in indicators.iterrows():
            code = row["code"]
            close = row["close"]

            # 超卖四条件
            rsi14 = row.get("rsi_14", 50)
            rsi6 = row.get("rsi_6", 50)
            bb_pos = row.get("bb_pos", 0.5)

            # bb_pos 可能为 None (BB宽度为0)
            if bb_pos is None:
                continue

            max_dd_60 = row.get("max_dd_60d", 0)
            if max_dd_60 is None:
                continue

            if pd.isna(rsi14) or pd.isna(rsi6):
                continue
            if rsi14 > self.MAX_RSI_14:
                continue
            if rsi6 > self.MAX_RSI_6:
                continue
            if bb_pos > self.MAX_BB_POSITION:
                continue
            if max_dd_60 > self.MAX_DRAWDOWN_60D:
                continue

            # 趋势位置
            ma20 = row.get("ma20", close)
            ma60 = row.get("ma60", close)
            if self.PRICE_BELOW_MA20 and close > ma20:
                continue
            if self.PRICE_BELOW_MA60 and close > ma60:
                continue

            # 反转确认: 当日涨幅
            prev_close = row.get("prev_close", close)
            if prev_close > 0:
                price_chg_pct = (close / prev_close - 1) * 100
            else:
                price_chg_pct = 0

            if price_chg_pct < self.MIN_PRICE_CHG:
                continue
            if close <= row.get("open", close):
                continue  # 收盘必须高于开盘

            # 连续阳线确认
            if self.CONSECUTIVE_UP >= 2:
                prev_chg = row.get("prev_chg", 0)
                if prev_chg <= 0:
                    continue

            # 量比
            vol_ratio = row.get("vol_ratio", 1.0)
            if vol_ratio < self.MIN_VOL_RATIO:
                continue

            # 量价背离
            if vol_ratio > 1.5 and price_chg_pct < self.MAX_VOL_DOWN_CHG:
                continue

            # RSI6反弹确认
            prev_rsi6 = row.get("prev_rsi6", 50)
            rsi6_delta = rsi6 - prev_rsi6
            RSI6_MIN_DELTA = 2.0
            if rsi6_delta < RSI6_MIN_DELTA:
                continue

            # ATR
            atr_pct = row.get("atr_pct", 2.0) or 2.0

            # === v6 评分 ===
            rsi_score = max(0, (self.MAX_RSI_14 - rsi14) / self.MAX_RSI_14) * self.SCORE_RSI_WEIGHT
            bb_score = max(0, (self.MAX_BB_POSITION - bb_pos) / 0.5) * self.SCORE_BB_WEIGHT
            dd_score = max(0, (-max_dd_60 + self.MAX_DRAWDOWN_60D) / 30) * self.SCORE_DD_WEIGHT
            reversal_score = min(price_chg_pct * self.SCORE_REVERSAL_WEIGHT, 15)
            rsi_turn_score = max(0, rsi6_delta) * 2
            vol_score = min(vol_ratio - 1, 1.5) * self.SCORE_VOL_WEIGHT
            atr_penalty = max(0, (5 - atr_pct)) * self.ATR_PENALTY_PER_PCT

            score = (rsi_score + bb_score + dd_score + reversal_score +
                     rsi_turn_score + vol_score - atr_penalty)

            candidates.append({
                "code": code,
                "close": close,
                "score": round(score, 1),
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    # ================================================================
    #  技术指标计算函数 (纯numpy, 无依赖)
    # ================================================================

    @staticmethod
    def _calc_rsi(close: np.ndarray, period: int) -> float:
        """计算RSI"""
        if len(close) < period + 1:
            return 50.0
        deltas = np.diff(close[-period-1:])
        gains = np.maximum(deltas, 0)
        losses = np.abs(np.minimum(deltas, 0))
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _calc_bollinger(close: np.ndarray, period: int = 20, nbdev: int = 2) -> dict:
        """计算Bollinger Bands位置"""
        if len(close) < period:
            return {"bb_pos": None, "bb_lower": None, "bb_upper": None}

        window = close[-period:]
        sma = np.mean(window)
        std = np.std(window, ddof=1)

        lower = sma - nbdev * std
        upper = sma + nbdev * std

        if upper - lower < 0.0001:
            return {"bb_pos": None, "bb_lower": lower, "bb_upper": upper}

        bb_pos = (close[-1] - lower) / (upper - lower)
        return {"bb_pos": max(0, min(1, bb_pos)), "bb_lower": lower, "bb_upper": upper}

    @staticmethod
    def _calc_max_dd(high: np.ndarray, period: int) -> float:
        """计算N日内最高价回撤百分比"""
        if len(high) < period:
            period = len(high)
        window = high[-period:]
        peak = np.max(window)
        current = high[-1]
        if peak <= 0:
            return 0.0
        dd = (current - peak) / peak * 100
        return dd

    @staticmethod
    def _calc_atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                      period: int = 14) -> float:
        """计算ATR(14)占收盘价百分比"""
        if len(close) < period + 1:
            return 2.0

        tr_list = []
        for i in range(1, min(period + 1, len(close))):
            tr = max(
                high[-i] - low[-i],
                abs(high[-i] - close[-i-1]),
                abs(low[-i] - close[-i-1])
            )
            tr_list.append(tr)

        atr = np.mean(tr_list) if tr_list else 0.01
        if close[-1] > 0:
            return atr / close[-1] * 100
        return 2.0
