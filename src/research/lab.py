"""
AlphaLab — 研究层编排 (借鉴 vnpy.alpha.lab.AlphaLab, 2026-06-24)

设计目标:
  - 顶层编排: 管 dataset (取数据) + model (训练/推理)
  - train_pipeline(): 端到端 (拉数据 → 训练 → 评估 → 保存)
  - predict_pipeline(): 端到端 (拉数据 → 推理 → 返回概率)
  - 单例, lazy 初始化

借鉴 vnpy 4.4:
  - AlphaLab 类, 组合 dataset / model
  - 本项目: AlphaLab (单例) + 训练/推理 pipeline

典型用法:
    from src.research import AlphaLab
    from src.research import AStockDataset, AStockAlphaModel

    lab = AlphaLab(
        dataset=AStockDataset(lookback=20, horizon=5),
        model=AStockAlphaModel(),
    )

    # 训练
    metrics = lab.train_pipeline(start="2020-01-01", end="2023-12-31",
                                 save_path="data/xgb_v5.json")
    print(metrics)  # {'auc': 0.78, 'ic': 0.05, 'n_samples': 10000}

    # 推理
    probs = lab.predict_pipeline(date="2024-06-24")
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

from .alpha_model import BaseAlphaModel
from .dataset import BaseDataset

logger = logging.getLogger(__name__)


class AlphaLab:
    """
    研究层编排 (借鉴 vnpy.alpha.lab.AlphaLab)

    组合:
      - dataset: 负责取 (X, y)
      - model: 负责训练 + 推理

    提供:
      - train_pipeline(): 拉数据 + 训练 + 评估
      - predict_pipeline(): 拉数据 + 推理
      - save() / load(): 持久化
    """

    def __init__(
        self,
        dataset: BaseDataset,
        model: BaseAlphaModel,
        lab_name: str = "default",
    ) -> None:
        self.dataset: BaseDataset = dataset
        self.model: BaseAlphaModel = model
        self.lab_name: str = lab_name
        self._is_trained: bool = False
        self._last_metrics: Optional[Dict[str, float]] = None

    # ─────────────────────────────────────────
    #  训练流水线
    # ─────────────────────────────────────────

    def train_pipeline(
        self,
        start: Union[date, str],
        end: Union[date, str],
        vt_symbols: Optional[List[str]] = None,
        save_path: Optional[Union[str, Path]] = None,
        scaler_path: Optional[Union[str, Path]] = None,
        **fit_kwargs,
    ) -> Dict[str, float]:
        """
        端到端训练

        Steps:
          1. dataset.fit(start, end) → (X, y)
          2. model.fit(X, y)
          3. 计算评估指标 (auc / ic / n_samples)
          4. save (可选)

        Returns:
            metrics dict
        """
        logger.info(f"[{self.lab_name}] train_pipeline: {start} ~ {end}")

        # 1. 取数据
        X, y = self.dataset.fit(start, end, vt_symbols)
        if X.size == 0:
            raise ValueError("dataset 返回空数据 (X.size=0)")

        # 2. 训练
        self.model.fit(X, y, feature_names=self.dataset.get_feature_names(), **fit_kwargs)
        self._is_trained = True

        # 3. 评估
        metrics = self._evaluate(X, y)
        self._last_metrics = metrics

        # 4. 保存
        if save_path is not None:
            self.model.save(save_path, scaler_path=scaler_path)
            logger.info(f"[{self.lab_name}] 模型已保存: {save_path}")

        return metrics

    def _evaluate(self, X: np.ndarray, y: np.ndarray) -> Dict[str, float]:
        """训练集 in-sample 评估 (mock, 真实应分 train/val/test)"""
        probs = self.model.predict_proba(X)
        n = len(y)
        # 简单指标: 平均 prob, label 相关性
        try:
            from scipy.stats import pearsonr
            ic, _ = pearsonr(probs, y)
        except Exception:
            ic = float(np.corrcoef(probs, y)[0, 1]) if n > 1 else 0.0
        return {
            "n_samples": int(n),
            "ic": float(ic) if not np.isnan(ic) else 0.0,
            "mean_prob": float(probs.mean()),
            "mean_label": float(y.mean()),
        }

    # ─────────────────────────────────────────
    #  推理流水线
    # ─────────────────────────────────────────

    def predict_pipeline(
        self,
        date: Union[date, str],
        vt_symbols: Optional[List[str]] = None,
    ) -> np.ndarray:
        """
        端到端推理

        Returns:
            概率数组, shape=(N,), N = 满足条件的股票数
        """
        if not self._is_trained and not self.model.is_fitted:
            raise RuntimeError("模型未训练, 请先调 train_pipeline() 或 load()")

        X = self.dataset.predict(date, vt_symbols)
        if X.size == 0:
            return np.array([])

        probs = self.model.predict_proba(X)
        return probs

    # ─────────────────────────────────────────
    #  持久化
    # ─────────────────────────────────────────

    def save(self, model_path: Union[str, Path], scaler_path: Union[str, Path] = None) -> None:
        """保存模型"""
        self.model.save(model_path, scaler_path=scaler_path)

    @classmethod
    def load(
        cls,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path] = None,
        dataset: Optional[BaseDataset] = None,
        lab_name: str = "loaded",
        n_features: int = 10,
    ) -> "AlphaLab":
        """加载已训练模型"""
        from .alpha_model import AStockAlphaModel
        model = AStockAlphaModel.load(model_path, scaler_path=scaler_path, n_features=n_features)
        # dataset 必传 (用于推理时拉数据)
        if dataset is None:
            # 修 2026-06-25: 之前用 __new__ 绕过 __init__ 创建半成品实例 (lookback=0 等),
            # 任何调用 predict 都会 AttributeError 或拿错数据。
            # 现在显式抛错, 强制调用方传真实 dataset
            raise ValueError(
                "AlphaLab.load() 必须传 dataset 参数 (用于 predict_pipeline 拉数据)。\n"
                "用法: AlphaLab.load(model_path, scaler_path=scaler_path, dataset=your_dataset, n_features=N)"
            )
        lab = cls(dataset=dataset, model=model, lab_name=lab_name)
        lab._is_trained = True
        return lab

    # ─────────────────────────────────────────
    #  调试
    # ─────────────────────────────────────────

    @property
    def is_trained(self) -> bool:
        return self._is_trained or self.model.is_fitted

    @property
    def last_metrics(self) -> Optional[Dict[str, float]]:
        return self._last_metrics

    def __repr__(self) -> str:
        return (
            f"<AlphaLab name={self.lab_name} is_trained={self.is_trained} "
            f"dataset={self.dataset.__class__.__name__} "
            f"model={self.model.__class__.__name__}>"
        )


__all__ = ["AlphaLab"]
