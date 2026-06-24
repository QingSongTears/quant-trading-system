"""
src.research 单测 — 3 模块合测 (2026-06-24)

涵盖:
  - Dataset: BaseDataset ABC 不可实例化, AStockDataset 拉数据 + 算 label
  - AlphaModel: BaseAlphaModel ABC 不可实例化, AStockAlphaModel fit/predict/save/load
  - AlphaLab: 训练流水线 + 推理流水线 + 评估 + 持久化
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.research import (
    AlphaLab,
    AStockAlphaModel,
    AStockDataset,
    BaseAlphaModel,
    BaseDataset,
)


# ═══════════════════════════════════════════
#  BaseDataset
# ═══════════════════════════════════════════


def test_base_dataset_cannot_be_instantiated():
    with pytest.raises(TypeError, match="abstract"):
        BaseDataset()


def test_base_dataset_rejects_invalid_lookback():
    class _M(BaseDataset):
        def _fetch_features(self, *a, **kw): pass
        def _make_labels(self, *a, **kw): pass
    with pytest.raises(ValueError, match="lookback"):
        _M(lookback=0)


def test_base_dataset_rejects_invalid_horizon():
    class _M(BaseDataset):
        def _fetch_features(self, *a, **kw): pass
        def _make_labels(self, *a, **kw): pass
    with pytest.raises(ValueError, match="horizon"):
        _M(horizon=0)


def test_a_stock_dataset_default_params():
    ds = AStockDataset()
    assert ds.lookback == 20
    assert ds.horizon == 5


def test_a_stock_dataset_fit_returns_xy():
    ds = AStockDataset(lookback=5, horizon=2, n_features=5)
    X, y = ds.fit("2024-01-01", "2024-01-31")
    # 至少有一些样本
    assert X.ndim == 2
    assert y.ndim == 1
    assert X.shape[0] == y.shape[0]


def test_a_stock_dataset_predict_returns_xy():
    ds = AStockDataset(lookback=5, horizon=2)
    X = ds.predict("2024-01-31")
    assert X.ndim == 2


def test_a_stock_dataset_get_feature_names_after_fit():
    ds = AStockDataset(lookback=3, horizon=2)
    ds.fit("2024-01-01", "2024-01-20")
    names = ds.get_feature_names()
    assert names is not None
    assert isinstance(names, list)


# ═══════════════════════════════════════════
#  BaseAlphaModel
# ═══════════════════════════════════════════


def test_base_alpha_model_cannot_be_instantiated():
    with pytest.raises(TypeError, match="abstract"):
        BaseAlphaModel()


def test_a_stock_alpha_model_init_state():
    m = AStockAlphaModel()
    assert m.is_fitted is False
    assert m.n_features == 0


def test_a_stock_alpha_model_fit_marks_fitted():
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randn(20).astype(np.float32)
    m.fit(X, y, feature_names=["a", "b", "c", "d", "e"])
    assert m.is_fitted is True
    assert m.n_features == 5


def test_a_stock_alpha_model_fit_rejects_empty():
    m = AStockAlphaModel()
    with pytest.raises(ValueError, match="X / y"):
        m.fit(np.array([]), np.array([]))


def test_a_stock_alpha_model_predict_proba_shape():
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randn(20).astype(np.float32)
    m.fit(X, y)
    probs = m.predict_proba(X)
    assert probs.shape == (20,)


def test_a_stock_alpha_model_predict_proba_in_range():
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randn(20).astype(np.float32)
    m.fit(X, y)
    probs = m.predict_proba(X)
    assert (probs >= 0).all()
    assert (probs <= 1).all()


def test_a_stock_alpha_model_predict_proba_unfitted_raises():
    m = AStockAlphaModel()
    with pytest.raises(RuntimeError, match="未训练"):
        m.predict_proba(np.zeros((1, 5)))


def test_a_stock_alpha_model_predict_proba_wrong_features_raises():
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randn(20).astype(np.float32)
    m.fit(X, y)
    with pytest.raises(ValueError, match="X 列数"):
        m.predict_proba(np.zeros((1, 7)))


def test_a_stock_alpha_model_save_creates_file(tmp_path):
    m = AStockAlphaModel()
    X = np.random.randn(10, 5).astype(np.float32)
    y = np.random.randn(10).astype(np.float32)
    m.fit(X, y)
    model_path = tmp_path / "xgb.json"
    scaler_path = tmp_path / "sc.json"
    m.save(model_path, scaler_path=scaler_path)
    assert model_path.exists()
    assert scaler_path.exists()


def test_a_stock_alpha_model_load_returns_fitted():
    m = AStockAlphaModel()
    X = np.random.randn(10, 5).astype(np.float32)
    y = np.random.randn(10).astype(np.float32)
    m.fit(X, y)
    model_path = Path("data/xgb_test_model.json")
    scaler_path = Path("data/xgb_test_scaler.json")
    m.save(model_path, scaler_path=scaler_path)
    try:
        loaded = AStockAlphaModel.load(model_path, scaler_path=scaler_path)
        assert loaded.is_fitted is True
    finally:
        if model_path.exists():
            model_path.unlink()
        if scaler_path.exists():
            scaler_path.unlink()


def test_a_stock_alpha_model_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        AStockAlphaModel.load(tmp_path / "missing.json")


# ═══════════════════════════════════════════
#  AlphaLab
# ═══════════════════════════════════════════


def _make_lab():
    return AlphaLab(
        dataset=AStockDataset(lookback=3, horizon=2, n_features=5),
        model=AStockAlphaModel(),
        lab_name="test_lab",
    )


def test_lab_init_state():
    lab = _make_lab()
    assert lab.lab_name == "test_lab"
    assert lab.is_trained is False
    assert lab.last_metrics is None


def test_lab_train_pipeline_runs():
    lab = _make_lab()
    metrics = lab.train_pipeline("2024-01-01", "2024-01-31")
    assert "n_samples" in metrics
    assert "ic" in metrics
    assert metrics["n_samples"] > 0
    assert lab.is_trained is True
    assert lab.last_metrics == metrics


def test_lab_train_pipeline_saves_model(tmp_path):
    lab = _make_lab()
    save_path = tmp_path / "lab_model.json"
    scaler_path = tmp_path / "lab_scaler.json"
    lab.train_pipeline(
        "2024-01-01", "2024-01-31",
        save_path=save_path, scaler_path=scaler_path,
    )
    assert save_path.exists()
    assert scaler_path.exists()


def test_lab_train_pipeline_empty_data_raises():
    """dataset 返回空时, train_pipeline 应抛"""
    lab = _make_lab()
    # 极小日期范围 → 样本可能为 0
    with pytest.raises(ValueError, match="空数据"):
        lab.train_pipeline("2024-01-01", "2024-01-02")  # 1 天数据, 不足 lookback+horizon


def test_lab_predict_pipeline_returns_probs():
    lab = _make_lab()
    lab.train_pipeline("2024-01-01", "2024-01-31")
    probs = lab.predict_pipeline("2024-01-31")
    assert probs.ndim == 1
    assert probs.size > 0
    assert (probs >= 0).all()
    assert (probs <= 1).all()


def test_lab_predict_pipeline_unfitted_raises():
    lab = _make_lab()
    with pytest.raises(RuntimeError, match="未训练"):
        lab.predict_pipeline("2024-01-31")


def test_lab_save_load_roundtrip(tmp_path):
    """训练 → 保存 → 加载 → 推理, 仍可用"""
    lab1 = _make_lab()
    lab1.train_pipeline("2024-01-01", "2024-01-31")

    model_path = tmp_path / "rt_model.json"
    scaler_path = tmp_path / "rt_scaler.json"
    lab1.save(model_path, scaler_path=scaler_path)

    # 用 lab1 的 dataset 推断 n_features (避免硬编码)
    test_dataset = AStockDataset(lookback=3, horizon=2, n_features=5)
    test_X = test_dataset.predict("2024-01-31")
    inferred_n = test_X.shape[1] if test_X.size > 0 else 15

    # 加载
    lab2 = AlphaLab.load(
        model_path, scaler_path=scaler_path,
        dataset=test_dataset,
        lab_name="loaded",
        n_features=inferred_n,
    )
    assert lab2.is_trained is True
    probs = lab2.predict_pipeline("2024-01-31")
    assert probs.size > 0


def test_lab_load_without_dataset_raises(tmp_path):
    """修 2026-06-25: load() 不传 dataset 应抛 ValueError (之前用 __new__ 绕过 __init__ 创建半成品)"""
    lab1 = _make_lab()
    lab1.train_pipeline("2024-01-01", "2024-01-31")
    model_path = tmp_path / "ds_model.json"
    scaler_path = tmp_path / "ds_scaler.json"
    lab1.save(model_path, scaler_path=scaler_path)

    with pytest.raises(ValueError, match="必须传 dataset"):
        AlphaLab.load(model_path, scaler_path=scaler_path, n_features=10)


def test_lab_repr_includes_state():
    lab = _make_lab()
    r = repr(lab)
    assert "AlphaLab" in r
    assert "test_lab" in r
    assert "is_trained=False" in r
