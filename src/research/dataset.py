"""
Dataset — 研究层数据抽象 (借鉴 vnpy.alpha.dataset, 2026-06-24)

设计目标:
  - 训练 / 推理 所需 (X, y) 数据的统一接口
  - 离线训练: fit(X, y) → 训练样本
  - 在线推理: predict(X) → 模型输入
  - A 股适配: 接现有 v_leader_features.py (74 维特征) + westock 数据源

借鉴 vnpy 4.4:
  - BaseDataset (ABC): 定义 fit / predict 协议
  - 本项目: BaseDataset (ABC) + AStockDataset (本项目特化)

典型用法:
    from src.research import AStockDataset

    # 训练
    dataset = AStockDataset(lookback=20, horizon=5)
    X_train, y_train = dataset.fit(start="2020-01-01", end="2023-12-31")
    model.fit(X_train, y_train)

    # 推理
    X_live = dataset.predict(date="2024-06-24")
    prob = model.predict_proba(X_live)

子类实现:
  - _fetch_features(date_range)  → 拉特征矩阵
  - _make_labels(close_series, horizon)  → 计算 label (e.g. 未来 N 日收益)
  - _build_X(date, lookback)  → 构造某日特征
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from datetime import date, datetime
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class BaseDataset(metaclass=ABCMeta):
    """
    研究层数据集抽象 (借鉴 vnpy.alpha.dataset.BaseDataset)

    子类必须实现:
      - _fetch_features(): 拉取特征矩阵
      - _make_labels(): 计算 label

    默认 API:
      - fit(date_range): 返回 (X, y) 训练样本
      - predict(date): 返回某日 X
      - get_feature_names(): 特征名列表
    """

    def __init__(self, lookback: int = 20, horizon: int = 5) -> None:
        if lookback < 1:
            raise ValueError(f"lookback 必须 >= 1, 当前 {lookback}")
        if horizon < 1:
            raise ValueError(f"horizon 必须 >= 1, 当前 {horizon}")
        self.lookback: int = lookback
        self.horizon: int = horizon
        self._feature_names: Optional[List[str]] = None

    # ── 子类必须实现 ──────────────────────────

    @abstractmethod
    def _fetch_features(
        self, start: date, end: date, vt_symbols: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        拉取特征矩阵

        Returns:
            DataFrame, columns 含 vt_symbol, trade_date, feature_*, close (label 所需)
        """

    @abstractmethod
    def _make_labels(self, close_df: pd.DataFrame) -> pd.Series:
        """
        根据 close 计算 label

        Args:
            close_df: 单只股票或全市场的 close 序列 (index=trade_date)
        Returns:
            Series, index 与 close_df 对齐, 值为未来 N 日收益 (label)
        """

    # ── 默认实现 ──────────────────────────

    def fit(
        self,
        start: date | str,
        end: date | str,
        vt_symbols: Optional[List[str]] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        拉训练样本 (X, y)

        Args:
            start: 起始日期
            end: 结束日期
            vt_symbols: 股票池 (None=全市场)

        Returns:
            (X, y) — X.shape=(N, n_features), y.shape=(N,)
        """
        start_d = _to_date(start)
        end_d = _to_date(end)
        logger.info(f"拟合 dataset: {start_d} ~ {end_d}, lookback={self.lookback}, horizon={self.horizon}")

        # 1. 拉特征 + close
        df = self._fetch_features(start_d, end_d, vt_symbols)
        if df.empty:
            return np.array([]), np.array([])

        # 2. 计算 label
        if "close" not in df.columns:
            raise ValueError("df 必须含 'close' 列 (用于算 label)")
        close_df = df.pivot(index="trade_date", columns="vt_symbol", values="close")
        labels = self._make_labels(close_df)  # MultiIndex: (trade_date, vt_symbol)

        # 3. 构造 X (每只股票每天一行为一条样本, lookback 天的特征堆叠)
        feature_cols = [c for c in df.columns if c not in ("vt_symbol", "trade_date", "close")]
        self._feature_names = feature_cols
        X_rows, y_rows = [], []

        for vt_sym, group in df.groupby("vt_symbol"):
            group = group.sort_values("trade_date").reset_index(drop=True)
            for i in range(self.lookback, len(group) - self.horizon + 1):
                # 特征: 过去 lookback 天的所有特征
                x = group.iloc[i - self.lookback : i][feature_cols].values.flatten()
                # label: 第 i 天的
                date_i = group.iloc[i]["trade_date"]
                if (date_i, vt_sym) in labels.index:
                    y_val = labels.loc[(date_i, vt_sym)] if isinstance(labels.index, pd.MultiIndex) else labels.loc[date_i]
                    X_rows.append(x)
                    y_rows.append(y_val)

        X = np.array(X_rows, dtype=np.float32) if X_rows else np.empty((0, 0), dtype=np.float32)
        y = np.array(y_rows, dtype=np.float32) if y_rows else np.array([], dtype=np.float32)
        logger.info(f"拟合完成: X.shape={X.shape}, y.shape={y.shape}")
        return X, y

    def predict(
        self, query_date: date | str, vt_symbols: Optional[List[str]] = None,
    ) -> np.ndarray:
        """
        拉某日推理 X (单日, 多股票)

        Returns:
            X.shape=(N, n_features)
        """
        query_d = _to_date(query_date)
        df = self._fetch_features(
            _add_days(query_d, -self.lookback), query_d, vt_symbols,
        )
        if df.empty:
            return np.empty((0, 0), dtype=np.float32)
        feature_cols = [c for c in df.columns if c not in ("vt_symbol", "trade_date", "close")]

        # 取 query_date 当天 (或最近一天) 的特征
        X_rows = []
        for vt_sym, group in df.groupby("vt_symbol"):
            group = group.sort_values("trade_date").reset_index(drop=True)
            if len(group) < self.lookback:
                continue
            x = group.iloc[-self.lookback:][feature_cols].values.flatten()
            X_rows.append(x)
        return np.array(X_rows, dtype=np.float32) if X_rows else np.empty((0, 0), dtype=np.float32)

    def get_feature_names(self) -> Optional[List[str]]:
        """获取特征名列表 (fit 后才有)"""
        return self._feature_names

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} lookback={self.lookback} "
            f"horizon={self.horizon}>"
        )


# ── 便捷函数 ──────────────────────────


def _to_date(d: date | str) -> date:
    """date 或 'YYYY-MM-DD' → date"""
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def _add_days(d: date, n: int) -> date:
    """date + n days"""
    from datetime import timedelta
    return d + timedelta(days=n)


# ── A 股特化 (示例) ──────────────────────────


class AStockDataset(BaseDataset):
    """
    A 股特化 Dataset (对接 data_mgr)

    _fetch_features 默认从 data_mgr.datafeed 拉日 K + 简单技术指标
    _make_labels 默认计算未来 N 日累计收益

    Example:
        dataset = AStockDataset(lookback=20, horizon=5)
        X, y = dataset.fit("2020-01-01", "2023-12-31")
    """

    def __init__(
        self,
        lookback: int = 20,
        horizon: int = 5,
        n_features: int = 5,
    ) -> None:
        super().__init__(lookback, horizon)
        # 默认特征列: open/high/low/close/volume (5 维)
        self.n_features: int = n_features
        # 真实项目中: 接 v_leader_features.FeatureBuilder 拿 74 维
        # 这里用 mock 方便单测, 实际部署替换

    def _fetch_features(
        self, start: date, end: date, vt_symbols: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        Mock: 返回 close + 几个特征 (不真接 data_mgr, 留给子类覆盖)

        生产实现 (示例):
            from src.data import data_mgr
            bars_5m = data_mgr.datafeed.get_bars(vt_symbol, "1d", start, end)
            ...
        """
        # 生成 mock 数据
        symbols = vt_symbols or ["000001.SZ", "000002.SZ", "600519.SH"]
        rows = []
        days = (_to_date(end) - _to_date(start)).days + 1
        for vt_sym in symbols:
            base = 10.0 + hash(vt_sym) % 100
            for d in range(days):
                dt = _add_days(_to_date(start), d)
                # 模拟价格
                o = base + d * 0.1
                h = o + 0.5
                l = o - 0.3
                c = o + 0.2
                v = 1000.0
                rows.append({
                    "vt_symbol": vt_sym,
                    "trade_date": dt,
                    "open": o, "high": h, "low": l, "close": c, "volume": v,
                    # 加几个额外特征 (mock)
                    "rsi14": 50.0 + (d % 30),
                    "macd": 0.1 * (d % 10 - 5),
                })
        return pd.DataFrame(rows)

    def _make_labels(self, close_df: pd.DataFrame) -> pd.Series:
        """
        未来 N 日累计收益: close[t+N] / close[t] - 1
        """
        # close_df: index=trade_date, columns=vt_symbol
        # 返回 MultiIndex (trade_date, vt_symbol) → label
        result = []
        for vt_sym in close_df.columns:
            series = close_df[vt_sym]
            for i in range(len(series) - self.horizon):
                curr = series.iloc[i]
                future = series.iloc[i + self.horizon]
                if curr > 0 and not np.isnan(curr) and not np.isnan(future):
                    label = (future / curr) - 1
                    result.append((series.index[i], vt_sym, label))
        if not result:
            return pd.Series(dtype=np.float64)
        idx = pd.MultiIndex.from_tuples([(d, s) for d, s, _ in result], names=["trade_date", "vt_symbol"])
        return pd.Series([v for _, _, v in result], index=idx, dtype=np.float32)


__all__ = ["BaseDataset", "AStockDataset"]
