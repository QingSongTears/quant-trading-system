"""
LeaderFeatureBuilder — 实时算 5 维技术指标 (2026-06-25)

从 K 线 DataFrame 实时算 macd_hist / rsi14 / kdj_k / kdj_j / boll_pos
归一化到 0-1 区间 (与 train_xgb_v4.py 训练时维度严格一致):

  - macd_hist: 不归一化 (柱状图原值, 但用 /close 缩放)
  - rsi14: rsi / 100       (0-1)
  - kdj_k: kdj_k / 100     (0-1)
  - kdj_j: kdj_j / 100     (0-1)
  - boll_pos: (close-lower) / (upper-lower)  (0-1)

调用方:
  from src.research.features import LeaderFeatureBuilder
  builder = LeaderFeatureBuilder()
  features = builder.build(bars_df)  # bars_df: index=trade_date, columns=OHLCV

与 v_leader_features.py 的区别:
  - 旧: 从 DB 表 technical_indicators 查 (预计算, 可能与实时不一致)
  - 新: 实时算, 训练/推理完全同源 (用 IndicatorRegistry)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.indicator import IndicatorRegistry
from src.indicator.protocol import to_close_series, to_ohlc_dataframe


logger = logging.getLogger(__name__)


# 5 维特征列名 (与 v_leader_features.TECH_COLS 严格一致)
TECH_COLS: list[str] = ["macd_hist", "rsi14", "kdj_k", "kdj_j", "boll_pos"]

# 默认值 (查不到/数据不足时用, 训练分布一致)
TECH_DEFAULTS: dict[str, float] = {
    "macd_hist": 0.0,
    "rsi14": 0.5,  # 归一化到 0-1
    "kdj_k": 0.5,
    "kdj_j": 0.5,
    "boll_pos": 0.5,
}


class LeaderFeatureBuilder:
    """
    实时 5 维技术指标算子 (替代 v_leader_features._load_tech_features)

    用法:
        builder = LeaderFeatureBuilder()
        features = builder.build(bars_df)  # 单股
        # 或
        features_dict = builder.build_batch({"000001.SZ": df1, "000002.SZ": df2})
    """

    def __init__(self, indicator_version: str = "v1") -> None:
        self.indicator_version = indicator_version
        # 预创建算子 (避免重复构造)
        self._rsi = IndicatorRegistry.get("rsi", indicator_version)
        self._kdj = IndicatorRegistry.get("kdj", indicator_version)
        self._boll = IndicatorRegistry.get("boll", indicator_version)
        self._macd = IndicatorRegistry.get("macd", indicator_version)
        logger.debug(f"LeaderFeatureBuilder initialized, version={indicator_version}")

    def build(self, bars_df: pd.DataFrame, reset_kdj: bool = True) -> dict[str, float]:
        """
        从 K 线算 5 维技术指标 (单股)

        Args:
            bars_df: DataFrame, index=trade_date, columns=[open, high, low, close, volume]
            reset_kdj: 是否重置 KDJ 状态 (切换股票时设 True)

        Returns:
            dict[macd_hist, rsi14, kdj_k, kdj_j, boll_pos] — 0-1 归一化
        """
        if bars_df is None or len(bars_df) < 20:
            return dict(TECH_DEFAULTS)

        try:
            ohlc = to_ohlc_dataframe(bars_df)
            close = to_close_series(bars_df)
        except Exception as e:
            logger.warning(f"build: 转换 K 线失败: {e}, 用默认值")
            return dict(TECH_DEFAULTS)

        result = dict(TECH_DEFAULTS)

        # 1. MACD 柱状图
        try:
            r = self._macd.compute(close)
            if r.value is not None:
                _, _, macd_hist = r.value
                # 归一化: macd / close (相对值, XGBoost 友好)
                last_close = float(close.iloc[-1]) if len(close) > 0 else 1.0
                result["macd_hist"] = macd_hist / last_close if last_close > 0 else 0.0
        except Exception as e:
            logger.debug(f"build: macd 失败: {e}")

        # 2. RSI / 100
        try:
            r = self._rsi.compute(close, n=14)
            if r.value is not None:
                result["rsi14"] = float(r.value) / 100.0
        except Exception as e:
            logger.debug(f"build: rsi 失败: {e}")

        # 3. KDJ 持续状态 (kdj 算子 instance 维护 _k/_d)
        try:
            r = self._kdj.compute(ohlc, n=9, m1=3, m2=3, reset=reset_kdj)
            if r.value is not None:
                k, d, j = r.value
                result["kdj_k"] = k / 100.0
                result["kdj_j"] = j / 100.0
        except Exception as e:
            logger.debug(f"build: kdj 失败: {e}")

        # 4. Boll 位置
        try:
            r = self._boll.compute(close, n=20, dev=2.0)
            if r.value is not None:
                mid, upper, lower = r.value
                if upper - lower > 1e-4:
                    result["boll_pos"] = (float(close.iloc[-1]) - lower) / (upper - lower)
                    result["boll_pos"] = max(0.0, min(1.0, result["boll_pos"]))
        except Exception as e:
            logger.debug(f"build: boll 失败: {e}")

        return result

    def build_batch(
        self, bars_dict: dict[str, pd.DataFrame],
    ) -> dict[str, dict[str, float]]:
        """
        批量算多只股票的 5 维技术指标

        Args:
            bars_dict: {vt_symbol: bars_df}

        Returns:
            {vt_symbol: {macd_hist, rsi14, kdj_k, kdj_j, boll_pos}}
        """
        result = {}
        for vt_sym, df in bars_dict.items():
            result[vt_sym] = self.build(df, reset_kdj=True)
        return result


__all__ = ["LeaderFeatureBuilder", "TECH_COLS", "TECH_DEFAULTS"]
