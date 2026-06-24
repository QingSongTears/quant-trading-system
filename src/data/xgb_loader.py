"""
XGBoost v4 模型加载器 — vnpy.alpha.model 角色
=========================================

设计目标:
  - 把 XGBoost 模型加载逻辑从策略类中抽离 (策略不感知模型细节)
  - 单一职责: load 一次, predict_proba 多次
  - 进程级单例,避免重复 IO
  - 严格校验 (feature_names 与 scaler.n_features_in_ 一致, industry_columns 命名规范)

对应 vnpy.alpha.model.template.AlphaModel:
  - fit()  →  已离线完成 (scripts/train_xgb_v4.py)
  - predict() → 本类的 predict_proba()

参考:
  - scripts/train_xgb_v4.py 训练脚本 (模型产物格式)
  - scripts/param_server.py:_predict_with_xgb 在线预测 (行为参照)
  - src/data/xgb_scaler.py JsonScaler 安全持久化
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Union

import numpy as np
import xgboost as xgb

from .xgb_scaler import JsonScaler, load_scaler


class XgbV4LoadError(RuntimeError):
    """XGBoost v4 模型加载失败 (文件缺失/损坏/校验失败)"""


@dataclass
class XgbV4Model:
    """
    XGBoost v4 模型封装 — load 一次, predict 多次

    字段:
        booster:           XGBoost Booster (从 xgb_model.json 加载)
        scaler:            JsonScaler (从 xgb_scaler.json 加载)
        feature_names:     训练时的特征顺序 (从 scaler JSON 读)
        industry_columns:  ind_* 列名列表 (用于 one-hot 行业)
        loaded_at:         加载时间戳
    """

    booster: xgb.Booster
    scaler: JsonScaler
    feature_names: list[str]
    industry_columns: list[str] = field(default_factory=list)
    loaded_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def load(
        cls,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path],
    ) -> "XgbV4Model":
        """
        从 .json 加载 XGBoost 模型 + Scaler, 严格校验一致性

        Args:
            model_path:  XGBoost 模型文件 (xgb.Booster.save_model 产物)
            scaler_path: JsonScaler 文件 (save_scaler 产物)

        Returns:
            XgbV4Model 实例

        Raises:
            XgbV4LoadError: 文件缺失 / 解析失败 / 校验失败
        """
        model_path = Path(model_path)
        scaler_path = Path(scaler_path)

        # ── 1. 文件存在性检查 ──
        if not model_path.exists():
            raise XgbV4LoadError(f"XGBoost 模型文件不存在: {model_path}")
        if not scaler_path.exists():
            raise XgbV4LoadError(f"Scaler 文件不存在: {scaler_path}")

        # ── 2. 加载 Scaler (从 JSON) ──
        try:
            sc_loaded = load_scaler(scaler_path)
        except (FileNotFoundError, ValueError) as e:
            raise XgbV4LoadError(f"Scaler 加载失败 {scaler_path}: {e}") from e

        scaler: JsonScaler = sc_loaded["scaler"]
        feature_names: list[str] = sc_loaded["feature_names"]
        if not feature_names:
            raise XgbV4LoadError(
                f"Scaler {scaler_path} 缺少 feature_names 字段或为空"
            )

        # ── 3. 加载 XGBoost Booster ──
        try:
            booster = xgb.Booster()
            booster.load_model(str(model_path))
        except (xgb.core.XGBoostError, IOError) as e:
            raise XgbV4LoadError(
                f"XGBoost 模型加载失败 {model_path}: {e}"
            ) from e

        # ── 4. 校验 feature_names 与 scaler.n_features_in_ 一致 ──
        if len(feature_names) != scaler.n_features_in_:
            raise XgbV4LoadError(
                f"feature_names 数量 ({len(feature_names)}) "
                f"!= scaler.n_features_in_ ({scaler.n_features_in_})"
            )

        # ── 5. 提取行业 one-hot 列 (以 ind_ 开头) ──
        industry_columns = [f for f in feature_names if f.startswith("ind_")]
        if not industry_columns:
            # 不强制要求,但提示
            import warnings
            warnings.warn(
                f"模型中无 ind_* 行业 one-hot 列, 行业特征将被忽略",
                stacklevel=2,
            )

        return cls(
            booster=booster,
            scaler=scaler,
            feature_names=feature_names,
            industry_columns=industry_columns,
        )

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        批量预测 60 日大涨概率

        Args:
            X: 特征矩阵 (n_samples, n_features), 顺序必须与 feature_names 一致

        Returns:
            np.ndarray, shape (n_samples,), 值为 0~1 的概率
        """
        X = np.asarray(X, dtype=np.float32)
        if X.ndim == 1:
            X = X.reshape(1, -1)

        if X.shape[1] != self.scaler.n_features_in_:
            raise ValueError(
                f"X 列数 ({X.shape[1]}) "
                f"!= 期望特征数 ({self.scaler.n_features_in_})"
            )

        # 树模型对缩放不敏感,但保持与训练一致 (scaler 已 fit)
        X_scaled = self.scaler.transform(X)
        dmatrix = xgb.DMatrix(X_scaled, feature_names=self.feature_names)
        raw = self.booster.predict(dmatrix)
        return raw.astype(np.float32)

    def __repr__(self) -> str:
        return (
            f"XgbV4Model(loaded_at={self.loaded_at.isoformat()}, "
            f"n_features={len(self.feature_names)}, "
            f"n_industries={len(self.industry_columns)})"
        )


# ── 进程级单例 (避免重复加载) ─────────────────────
_instance: XgbV4Model | None = None


def get_xgb_v4(
    model_path: Union[str, Path] = "data/xgb_model.json",
    scaler_path: Union[str, Path] = "data/xgb_scaler.json",
    reload: bool = False,
) -> XgbV4Model:
    """
    获取 XGBoost v4 模型单例

    Args:
        model_path: 模型文件路径
        scaler_path: Scaler 文件路径
        reload: True 强制重新加载 (测试/模型热更新用)

    Returns:
        XgbV4Model 实例
    """
    global _instance
    if _instance is not None and not reload:
        return _instance

    _instance = XgbV4Model.load(model_path, scaler_path)
    return _instance


def clear_xgb_v4_cache() -> None:
    """清除单例缓存 (测试/重载)"""
    global _instance
    _instance = None


__all__ = ["XgbV4Model", "XgbV4LoadError", "get_xgb_v4", "clear_xgb_v4_cache"]
