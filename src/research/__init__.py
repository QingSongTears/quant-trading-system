"""
src.research — 研究层 (借鉴 vnpy.alpha, 2026-06-24)

模块划分:
  - dataset.py     :  BaseDataset + AStockDataset (数据接口)
  - alpha_model.py :  BaseAlphaModel + AStockAlphaModel (模型包装)
  - lab.py         :  AlphaLab (顶层编排: 训练 + 推理)

设计目标:
  - 离线训练: AlphaLab.train_pipeline(start, end, save_path)
  - 在线推理: AlphaLab.predict_pipeline(date)
  - A 股适配: 现有 v_leader_features.py + XgbV4Model
  - 子类化: 用户可继承 BaseDataset / BaseAlphaModel 替换默认实现

典型用法:
    from src.research import AlphaLab, AStockDataset, AStockAlphaModel

    # 1. 训练
    lab = AlphaLab(
        dataset=AStockDataset(lookback=20, horizon=5),
        model=AStockAlphaModel(),
    )
    metrics = lab.train_pipeline("2020-01-01", "2023-12-31",
                                 save_path="data/xgb_v5.json")
    print(metrics)  # {'n_samples': 10000, 'ic': 0.05, ...}

    # 2. 推理
    probs = lab.predict_pipeline("2024-06-24")
    # probs.shape = (N, ), N = 当日有数据的股票数
"""
from __future__ import annotations

from .alpha_model import AStockAlphaModel, BaseAlphaModel
from .dataset import AStockDataset, BaseDataset
from .lab import AlphaLab

__all__ = [
    "BaseDataset",
    "AStockDataset",
    "BaseAlphaModel",
    "AStockAlphaModel",
    "AlphaLab",
]
