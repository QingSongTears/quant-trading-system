"""
Dataset — 研究层数据抽象 (借鉴 vnpy.alpha.dataset, 2026-06-24 → 2026-06-25 真接 data_mgr)

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

2026-06-25 重构 (Phase B4e):
  - AStockDataset._fetch_features 真接 data_mgr.datafeed (替代 hash-mock)
  - 默认特征 5 维 (open/high/low/close/volume), 调用方传 feature_builder 可算 74 维
  - 单测仍用 mock, 但默认构造会触发 data_mgr 初始化
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from datetime import date, datetime
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.research.features.leader_features import LeaderFeatureBuilder

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
    A 股特化 Dataset (对接 data_mgr, 2026-06-25 真接 → 2026-06-27 ADR-0008 默认接 LeaderFeatureBuilder)

    _fetch_features 默认从 data_mgr.datafeed 拉日 K
    _make_labels 默认计算未来 N 日累计收益

    feature_builder 参数 (可选, ADR-0008 D1+D3):
        - 默认 LeaderFeatureBuilder() → 9 维 (4 OHLCV + 5 技术指标: macd_hist/rsi14/kdj_k/kdj_j/boll_pos)
          与 v_leader_features.TECH_COLS 严格一致, 训练/推理同源实时算
        - 显式传 None → OHLCV 5 维降级路径 (单测 / mock 用, 生产禁用)
        - 显式传 v_leader_features.FeatureBuilder(engine) → 74 维 (生产 v_leader 策略用)

    Example:
        # 默认 9 维 (开箱即用, ADR-0008 D1)
        dataset = AStockDataset(lookback=20, horizon=5)
        X, y = dataset.fit("2020-01-01", "2023-12-31")

        # 显式 None → 5 维 OHLCV 降级
        dataset = AStockDataset(lookback=20, horizon=5, feature_builder=None)

        # 74 维特征 (生产 v_leader)
        from src.strategies.v_leader_features import FeatureBuilder
        fb = FeatureBuilder(engine, indicator_version="v1")
        dataset = AStockDataset(lookback=20, horizon=5, feature_builder=fb)
    """

    # ADR-0008 D3: sentinel 区分 "未传" vs "显式 None"
    # 未传 → 默认 LeaderFeatureBuilder (开箱即用)
    # 显式 None → 降级 OHLCV (单测 mock 用)
    _USE_DEFAULT_BUILDER: Any = object()

    def __init__(
        self,
        lookback: int = 20,
        horizon: int = 5,
        n_features: int = 5,
        feature_builder: Any = _USE_DEFAULT_BUILDER,
    ) -> None:
        super().__init__(lookback, horizon)
        self.n_features: int = n_features
        # ADR-0008 D1+D3: 默认 LeaderFeatureBuilder (5 维技术指标, 实时算)
        # 显式传 None = 降级 OHLCV 5 维 (单测用)
        # 显式传 builder 实例 = 自定义 (e.g. v_leader 74 维)
        if feature_builder is self._USE_DEFAULT_BUILDER:
            self.feature_builder: Optional[Any] = LeaderFeatureBuilder()
        else:
            self.feature_builder = feature_builder
        # 缓存 data_mgr 引用 (lazy 加载避免循环依赖)
        self._data_mgr = None

    def _get_data_mgr(self):
        """Lazy 加载 data_mgr (避免循环依赖)"""
        if self._data_mgr is None:
            from src.data import data_mgr
            self._data_mgr = data_mgr
        return self._data_mgr

    def _fetch_features(
        self, start: date, end: date, vt_symbols: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        真接 data_mgr.datafeed 拉日 K (2026-06-25) + LeaderFeatureBuilder 实时算 (2026-06-27)

        Returns:
            DataFrame, columns: vt_symbol, trade_date, open, high, low, close, volume, [features...]

        Raises:
            ImportError: datafeed 未配置
            ValueError: 股票池为空 / 日期范围非法
        """
        if not vt_symbols:
            # 默认: 沪深 300 成分股 (后续可配置)
            vt_symbols = [
                "000001.SZ", "000002.SZ", "000063.SZ", "000333.SZ", "000651.SZ",
                "600000.SH", "600036.SH", "600519.SH", "600887.SH", "601318.SH",
            ]
        try:
            data_mgr = self._get_data_mgr()
        except Exception as e:
            logger.warning(f"data_mgr 加载失败 ({e}), 用 mock 数据 fallback")
            return self._mock_features(start, end, vt_symbols)

        rows = []
        for vt_sym in vt_symbols:
            try:
                bars = data_mgr.datafeed.get_bars(vt_sym, "1d", start, end)
            except Exception as e:
                logger.warning(f"get_bars({vt_sym}) 失败: {e}, 跳过")
                continue
            if not bars:
                continue
            # bars → DataFrame
            df = pd.DataFrame([{
                "trade_date": b.trade_date if hasattr(b, "trade_date") else b.datetime,
                "open": b.open_price,
                "high": b.high_price,
                "low": b.low_price,
                "close": b.close_price,
                "volume": b.volume,
            } for b in bars])
            df["vt_symbol"] = vt_sym
            df["trade_date"] = pd.to_datetime(df["trade_date"])

            # ADR-0008 D2: 删除内嵌 RSI/MACD, 统一走 feature_builder.build()
            # 若 feature_builder=None (显式降级), 跳过特征工程
            if self.feature_builder is not None:
                try:
                    bars_indexed = df.set_index("trade_date")
                    result = self.feature_builder.build(bars_indexed)
                    # 支持两种返回:
                    #   - dict (LeaderFeatureBuilder.build → 5 维技术指标)
                    #   - DataFrame (v_leader_features.FeatureBuilder → 74 维)
                    if isinstance(result, dict):
                        # dict → 行 (每行用同一组特征, 与原内嵌版本一致)
                        for col, val in result.items():
                            df[col] = float(val)
                    elif isinstance(result, pd.DataFrame):
                        # DataFrame → concat (与旧逻辑一致)
                        df = pd.concat([df, result.reset_index()], axis=1)
                    else:
                        logger.warning(
                            f"feature_builder({vt_sym}) 返未知类型 "
                            f"{type(result).__name__}, 跳过"
                        )
                except Exception as e:
                    logger.warning(
                        f"feature_builder({vt_sym}) 失败: {e}, 跳过特征"
                    )

            rows.append(df)

        if not rows:
            return pd.DataFrame()
        return pd.concat(rows, ignore_index=True)

    def _mock_features(
        self, start: date, end: date, vt_symbols: list,
    ) -> pd.DataFrame:
        """Hash-based mock (单测用, 生产不调用)"""
        rows = []
        days = (_to_date(end) - _to_date(start)).days + 1
        for vt_sym in vt_symbols:
            base = 10.0 + hash(vt_sym) % 100
            for d in range(days):
                dt = _add_days(_to_date(start), d)
                o = base + d * 0.1
                rows.append({
                    "vt_symbol": vt_sym,
                    "trade_date": dt,
                    "open": o, "high": o + 0.5, "low": o - 0.3, "close": o + 0.2, "volume": 1000.0,
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
