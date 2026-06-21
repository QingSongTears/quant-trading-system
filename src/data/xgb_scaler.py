"""
XGBoost 特征缩放器 — JSON 持久化
================================

替代 sklearn.preprocessing.StandardScaler 的 pickle 持久化:

安全
----
- 不使用 pickle 协议,避免反序列化 RCE (CVE 模式)
- 数据为纯 JSON 数值数组,无任何可执行内容
- 加载时做字段校验和长度校验

兼容
----
- 任意具备 .mean_ / .scale_ / .n_features_in_ 属性的对象
  (sklearn StandardScaler、numpy 自实现等) 都可以 save
- 加载后返回的 JsonScaler 与 sklearn StandardScaler 共享
  核心接口: .transform(X) = (X - mean) / scale
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Union

import numpy as np


class JsonScaler:
    """与 sklearn StandardScaler 兼容的 JSON 序列化实现

    仅实现 .transform() 方法,如需 .fit() / .fit_transform()
    请用 sklearn StandardScaler 训练后调用 save_scaler() 导出。
    """

    def __init__(
        self,
        mean: Union[list[float], np.ndarray],
        scale: Union[list[float], np.ndarray],
        n_features: int,
        feature_names: list[str] | None = None,
    ):
        self.mean_ = np.asarray(mean, dtype=float)
        self.scale_ = np.asarray(scale, dtype=float)
        self.n_features_in_ = int(n_features)
        self.feature_names_in_ = (
            np.asarray(feature_names) if feature_names is not None else None
        )
        if self.mean_.shape != (n_features,):
            raise ValueError(
                f"mean 长度 {self.mean_.shape[0]} != n_features {n_features}"
            )
        if self.scale_.shape != (n_features,):
            raise ValueError(
                f"scale 长度 {self.scale_.shape[0]} != n_features {n_features}"
            )

    def transform(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        return (X - self.mean_) / self.scale_

    def to_dict(self) -> dict:
        return {
            "mean": self.mean_.tolist(),
            "scale": self.scale_.tolist(),
            "n_features": self.n_features_in_,
            "feature_names": (
                self.feature_names_in_.tolist()
                if self.feature_names_in_ is not None
                else None
            ),
        }


def save_scaler(scaler, feature_names: list[str], path: Union[str, Path]) -> Path:
    """把 scaler 对象 + feature_names 写入 JSON

    Args:
        scaler: 任何具备 .mean_ / .scale_ / .n_features_in_ 的对象
                (sklearn StandardScaler、JsonScaler 等)
        feature_names: 特征名列表
        path: 输出 JSON 路径
    """
    path = Path(path)
    data = {
        "mean": np.asarray(scaler.mean_).tolist(),
        "scale": np.asarray(scaler.scale_).tolist(),
        "n_features": int(scaler.n_features_in_),
        "feature_names": list(feature_names),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_scaler(path: Union[str, Path]) -> dict:
    """从 JSON 加载 scaler

    Returns:
        {"scaler": JsonScaler, "feature_names": list[str]}
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"scaler 文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required = {"mean", "scale", "n_features", "feature_names"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"scaler JSON 缺少字段: {missing}")
    if not isinstance(data["n_features"], int) or data["n_features"] <= 0:
        raise ValueError(f"n_features 非法: {data['n_features']!r}")

    scaler = JsonScaler(
        mean=data["mean"],
        scale=data["scale"],
        n_features=data["n_features"],
        feature_names=data["feature_names"],
    )
    return {"scaler": scaler, "feature_names": data["feature_names"]}
