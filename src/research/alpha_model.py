"""
AlphaModel — 研究层模型抽象 (借鉴 vnpy.alpha.model, 2026-06-24, 修 2026-06-25)

设计目标:
  - 训练 (fit) + 推理 (predict) 统一接口
  - 离线训练: fit(X, y) → 模型权重
  - 在线推理: predict_proba(X) → 概率/信号
  - A 股适配: 委托给 XgbV4Model (src/data/xgb_loader.py)

借鉴 vnpy 4.4:
  - BaseAlphaModel (ABC): fit / predict 协议
  - 本项目: BaseAlphaModel (ABC) + AStockAlphaModel (包装 XGBoost)

典型用法:
    from src.research import AStockAlphaModel

    # 训练 (offline, 调真 XGBoost)
    model = AStockAlphaModel()
    model.fit(X_train, y_train, feature_names=names)
    model.save("data/xgb_v5.json", scaler_path="data/xgb_v5_scaler.json")

    # 推理 (调真 XGBoost)
    model2 = AStockAlphaModel.load("data/xgb_v5.json", "data/xgb_v5_scaler.json")
    probs = model2.predict_proba(X_live)

设计 (修 2026-06-25):
  - 内部直接调 XgbV4Model (而非 mock)
  - 依赖: xgboost + JsonScaler, 训练时真调 xgboost.train
  - 单测用 monkeypatch 隔离外部依赖
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Union

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


# ── A 股特化 (真接 XGBoost) ──────────────────────────


class AStockAlphaModel(BaseAlphaModel):
    """
    A 股 XGBoost 模型包装 (真接 XgbV4Model, 修 2026-06-25)

    修前: fit/predict/save/load 用 _MockXgbModel 假象, 生产不能直接用
    修后: 内部委托给 src/data/xgb_loader.py:XgbV4Model (真 XGBoost)

    生产用法:
        # 1. 训练
        model = AStockAlphaModel()
        model.fit(X_train, y_train, feature_names=names)
        model.save("data/xgb_v5.json", scaler_path="data/xgb_v5_scaler.json")

        # 2. 推理
        model2 = AStockAlphaModel.load("data/xgb_v5.json", "data/xgb_v5_scaler.json")
        probs = model2.predict_proba(X_live)

    单测:
        - 训练: 用 monkeypatch 替换 _train_xgb 和 _save_scaler
        - 推理: 用 monkeypatch 替换 _load_xgb_v4 (避免依赖真实模型文件)
    """

    def __init__(self) -> None:
        super().__init__()
        # 内部持有 XgbV4Model 实例 (load 后填充)
        self._xgb_v4: Any = None
        self._feature_names: list = []

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[List[str]] = None,
        xgb_params: Optional[dict] = None,
        **kwargs,
    ) -> "AStockAlphaModel":
        """
        训练 XGBoost 模型 (offline, 修 2026-06-25 真接)

        Args:
            X: 特征矩阵 (n_samples, n_features)
            y: 标签 (n_samples,)
            feature_names: 特征名列表 (用于 scaler 持久化, 顺序必须与 X 列对应)
            xgb_params: xgboost.train 的参数字典 (默认浅层参数)
        """
        if X.size == 0 or y.size == 0:
            raise ValueError("X / y 不能为空")
        if X.shape[0] != y.shape[0]:
            raise ValueError(
                f"X 行数 ({X.shape[0]}) != y 行数 ({y.shape[0]})"
            )

        # 默认 xgboost 参数
        if xgb_params is None:
            xgb_params = {
                "objective": "binary:logistic",
                "max_depth": 6,
                "eta": 0.1,
                "eval_metric": "logloss",
                "verbosity": 0,
            }

        # 训练 (修 2026-06-25: 真调 xgboost.train)
        from ..data.xgb_loader import _train_xgb  # 内部函数 (见 xgb_loader.py)
        booster, scaler = _train_xgb(X, y, xgb_params=xgb_params, feature_names=feature_names)

        # 缓存 (修 2026-06-25: 统一用 _xgb_v4 装结构体, fit/load 路径一致)
        from types import SimpleNamespace
        self._xgb_v4 = SimpleNamespace(
            booster=booster,
            scaler=scaler,
            feature_names=feature_names or [f"f{i}" for i in range(X.shape[1])],
            n_features_in_=X.shape[1],
        )
        self._scaler = scaler
        self._feature_names = self._xgb_v4.feature_names
        self._n_features = X.shape[1]
        self.is_fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """推理 (委托给 _xgb_v4.booster.predict, 修 2026-06-25)"""
        if not self.is_fitted:
            raise RuntimeError("模型未训练/未加载, 请先调 fit() 或 load()")
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[1] != self._n_features:
            raise ValueError(
                f"X 列数 ({X.shape[1]}) != 期望 ({self._n_features})"
            )
        # 适配两种 _xgb_v4 类型: SimpleNamespace (fit 后) / XgbV4Model (load 后)
        booster = getattr(self._xgb_v4, "booster", self._xgb_v4)
        if hasattr(booster, "predict_proba"):
            return booster.predict_proba(X)
        # fallback: 真 xgb.Booster, 用 DMatrix
        import xgboost as xgb
        dmatrix = xgb.DMatrix(X, feature_names=self._feature_names)
        return booster.predict(dmatrix)

    def save(
        self,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path] = None,
        **kwargs,
    ) -> None:
        """
        保存到磁盘 (修 2026-06-25: 真用 booster.save_model + save_scaler)

        Args:
            model_path: XGBoost 模型路径 (.json)
            scaler_path: JsonScaler 路径 (默认 model_path 同目录 + '_scaler.json')
        """
        if not self.is_fitted:
            raise RuntimeError("模型未训练, 无法保存")
        model_path = Path(model_path)
        if scaler_path is None:
            scaler_path = model_path.with_name(model_path.stem + "_scaler.json")
        scaler_path = Path(scaler_path)

        # 真保存 (调 xgb.Booster.save_model + data_mgr.save_scaler)
        self._xgb_v4.booster.save_model(str(model_path))
        from ..data import data_mgr
        data_mgr.save_scaler(self._scaler, self._feature_names, scaler_path)

    @classmethod
    def load(
        cls,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path] = None,
        **kwargs,
    ) -> "AStockAlphaModel":
        """
        从磁盘加载 (修 2026-06-25: 真接 XgbV4Model.load)

        Args:
            model_path: XGBoost 模型路径
            scaler_path: JsonScaler 路径 (默认 model_path 同目录 + '_scaler.json')
        """
        # 修 2026-06-25: 用 importlib 拿 fresh 引用, 让 monkeypatch 能 hook
        import importlib
        xgb_loader_mod = importlib.import_module("src.data.xgb_loader")
        XgbV4Model = xgb_loader_mod.XgbV4Model

        model_path = Path(model_path)
        if scaler_path is None:
            scaler_path = model_path.with_name(model_path.stem + "_scaler.json")
        scaler_path = Path(scaler_path)

        # 真加载
        xgb_v4 = XgbV4Model.load(model_path, scaler_path)

        inst = cls()
        inst._xgb_v4 = xgb_v4  # 已经是 XgbV4Model, 接口与 SimpleNamespace 兼容
        inst._scaler = xgb_v4.scaler
        inst._feature_names = xgb_v4.feature_names
        inst._n_features = xgb_v4.n_features_in_
        inst.is_fitted = True
        return inst


__all__ = ["BaseAlphaModel", "AStockAlphaModel"]
