"""
AlphaModel — 研究层模型抽象 (借鉴 vnpy.alpha.model, 2026-06-24)

设计目标:
  - 训练 (fit) + 推理 (predict) 统一接口
  - 离线训练: fit(X, y) → 模型权重
  - 在线推理: predict_proba(X) → 概率/信号
  - A 股适配: 包装现有 XgbV4Model (src/data/xgb_loader.py)

借鉴 vnpy 4.4:
  - BaseAlphaModel (ABC): fit / predict 协议
  - 本项目: BaseAlphaModel (ABC) + AStockAlphaModel (包装 XGBoost)

典型用法:
    from src.research import AStockAlphaModel

    model = AStockAlphaModel()
    model.fit(X_train, y_train)
    model.save("data/xgb_v5.json", "data/xgb_v5_scaler.json")

    # 推理
    model2 = AStockAlphaModel.load("data/xgb_v5.json", "data/xgb_v5_scaler.json")
    probs = model2.predict_proba(X_live)
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import Any, Union

import numpy as np

logger = logging.getLogger(__name__)


class BaseAlphaModel(metaclass=ABCMeta):
    """
    研究层模型抽象 (借鉴 vnpy.alpha.model.template.AlphaModel)

    子类必须实现:
      - fit(X, y) → 训练
      - predict_proba(X) → 推理
      - save / load → 持久化
    """

    def __init__(self) -> None:
        self.is_fitted: bool = False
        self._n_features: int = 0

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> "BaseAlphaModel":
        """
        训练

        Args:
            X: 特征矩阵 (n_samples, n_features)
            y: 标签 (n_samples,)

        Returns:
            self
        """

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        推理 — 输出概率 / 信号

        Returns:
            概率数组, shape=(n_samples,), 0~1
        """

    @abstractmethod
    def save(self, model_path: Union[str, Path], **kwargs) -> None:
        """保存模型到磁盘"""

    @classmethod
    @abstractmethod
    def load(cls, model_path: Union[str, Path], **kwargs) -> "BaseAlphaModel":
        """从磁盘加载模型"""

    @property
    def n_features(self) -> int:
        return self._n_features

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} is_fitted={self.is_fitted} "
            f"n_features={self._n_features}>"
        )


# ── A 股特化 (包装 XGBoost) ──────────────────────────


class AStockAlphaModel(BaseAlphaModel):
    """
    A 股 XGBoost 模型包装 (对接 src/data/xgb_loader.py:XgbV4Model)

    - fit: 训练新模型 (需要 xgboost + json_scaler 写入能力)
    - predict_proba: 推理 (单例 XgbV4Model.predict_proba)
    - save / load: 委托给 XgbV4Model

    生产用法:
        model = AStockAlphaModel()
        model.fit(X_train, y_train)
        model.save("data/xgb_v5.json", scaler_path="data/xgb_v5_scaler.json")

        # 推理 (单例)
        loaded = AStockAlphaModel.load("data/xgb_v5.json", "data/xgb_v5_scaler.json")
        probs = loaded.predict_proba(X_live)

    ⚠️ 占位实现 (2026-06-25): fit/predict/save/load 用 _MockXgbModel
    ─────────────────────────────────────────────────────
    真实 XGBoost 训练未实现, save() 只写字符串 'mock_xgb_model'。
    生产替换:
        from src.data import data_mgr
        from src.data.xgb_loader import XgbV4Model
        def fit(self, X, y, ...):
            import xgboost as xgb
            booster = xgb.train(...)
            self._xgb_model = booster
            self._scaler = data_mgr.load_scaler(...)
    TODO: 接入 XgbV4Model.load() / save_model() (见 src/data/xgb_loader.py)
    """

    def __init__(self) -> None:
        super().__init__()
        # 延迟 import (避免 xgboost 强制依赖)
        self._xgb_model: Any = None
        self._scaler: Any = None
        self._feature_names: list = []

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list = None,
        **kwargs,
    ) -> "AStockAlphaModel":
        """
        训练 XGBoost 模型 (offline 训练, 单测用 mock)

        生产实现: 调 xgboost.train, 写 booster + scaler
        单测: 用 mock
        """
        if X.size == 0 or y.size == 0:
            raise ValueError("X / y 不能为空")

        # 真实训练 (这里 mock, 单测覆盖)
        # from xgboost import XGBClassifier
        # clf = XGBClassifier(...)
        # clf.fit(X, y)
        # self._xgb_model = clf.booster
        # self._scaler = ...

        # 简化: 用 np 模拟
        self._xgb_model = _MockXgbModel(X.shape[1])
        self._scaler = _MockScaler()
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        self._n_features = X.shape[1]
        self.is_fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """推理 (委托给 _xgb_model.predict)"""
        if not self.is_fitted:
            raise RuntimeError("模型未训练, 请先调 fit()")
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[1] != self._n_features:
            raise ValueError(
                f"X 列数 ({X.shape[1]}) != 期望 ({self._n_features})"
            )
        return self._xgb_model.predict(X)

    def save(
        self,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path] = None,
        **kwargs,
    ) -> None:
        """保存到磁盘 (单测 mock 即可)"""
        model_path = Path(model_path)
        # 真实: booster.save_model(str(model_path))
        #      save_scaler(scaler, feature_names, scaler_path)
        model_path.write_text("mock_xgb_model", encoding="utf-8")
        if scaler_path is not None:
            Path(scaler_path).write_text("mock_scaler", encoding="utf-8")

    @classmethod
    def load(
        cls,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path] = None,
        n_features: int = 10,
        **kwargs,
    ) -> "AStockAlphaModel":
        """从磁盘加载 (单测 mock)

        Args:
            model_path: 模型文件路径
            scaler_path: scaler 文件路径 (可选)
            n_features: 特征数 (真实项目从 scaler 读, mock 需传)
        """
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {model_path}")
        inst = cls()
        inst._xgb_model = _MockXgbModel(n_features=n_features)
        inst._scaler = _MockScaler()
        inst._n_features = n_features
        inst.is_fitted = True
        return inst


# ── Mock 实现 (单测用) ──────────────────────────


class _MockXgbModel:
    """Mock XGBoost 模型, predict 返回 sigmoid(score)"""
    def __init__(self, n_features: int = 10):
        self.n_features = n_features
        # 固定 weights (单测稳定)
        np.random.seed(42)
        self.weights = np.random.randn(n_features) * 0.1

    def predict(self, X: np.ndarray) -> np.ndarray:
        # 简单线性 + sigmoid
        scores = X @ self.weights
        return 1.0 / (1.0 + np.exp(-scores))


class _MockScaler:
    def transform(self, X: np.ndarray) -> np.ndarray:
        return X  # mock: 不变换


__all__ = ["BaseAlphaModel", "AStockAlphaModel"]
