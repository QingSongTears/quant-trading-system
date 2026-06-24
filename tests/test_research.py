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


# ── Fixtures: 隔离 xgboost + sklearn 真实依赖 (修 2026-06-25) ──────────


class _FakeBooster:
    """Mock xgb.Booster, 暴露 .save_model() + .predict_proba(DMatrix)

    注意: AStockAlphaModel.predict_proba() 期望 _xgb_v4.predict_proba(X) — 但
    X 是 numpy 数组, 不是 DMatrix。这里 _FakeBooster.predict_proba 直接接
    numpy 数组, 跟 XgbV4Model.predict_proba 行为一致 (后者会自己建 DMatrix)。
    """
    def __init__(self, n_features):
        self.n_features = n_features
        self.saved_paths = []

    def save_model(self, path):
        self.saved_paths.append(path)
        # 真写文件 (修 2026-06-25: 让 AStockAlphaModel.save 断言 .exists() 成立)
        from pathlib import Path
        Path(path).write_text("fake_xgb_booster", encoding="utf-8")

    def predict(self, dmatrix):
        # 内部 xgb 接口
        return np.full(dmatrix.num_row(), 0.5, dtype=np.float32)

    def predict_proba(self, X):
        """AStockAlphaModel.predict_proba 委托, 接 numpy 数组"""
        if hasattr(X, "shape"):
            n = X.shape[0]
        else:
            n = 1
        # 0~1 之间的伪概率 (用 sigmoid-like)
        scores = np.random.RandomState(42).randn(n)
        return 1.0 / (1.0 + np.exp(-scores))


class _FakeDMatrix:
    def __init__(self, X, label=None, feature_names=None):
        self.X = X
        self.label = label
        self.feature_names = feature_names
    def num_row(self):
        return self.X.shape[0]


def _fake_train_xgb(X, y, xgb_params, feature_names):
    """Mock _train_xgb: 返 (FakeBooster, JsonScaler-like)"""
    from src.data.xgb_scaler import JsonScaler
    scaler = JsonScaler(
        mean=[0.0] * X.shape[1],
        scale=[1.0] * X.shape[1],
        n_features=X.shape[1],
        feature_names=feature_names or [f"f{i}" for i in range(X.shape[1])],
    )
    booster = _FakeBooster(X.shape[1])
    return booster, scaler


def _fake_load_xgb_v4(model_path, scaler_path, n_features=18):
    """Mock XgbV4Model.load: 返 fake XgbV4Model-like"""
    from src.data.xgb_scaler import JsonScaler
    fake = MagicMock()
    fake.booster = _FakeBooster(n_features=n_features)
    fake.scaler = JsonScaler(
        mean=[0.0] * n_features, scale=[1.0] * n_features,
        n_features=n_features, feature_names=[f"f{i}" for i in range(n_features)],
    )
    fake.feature_names = [f"f{i}" for i in range(n_features)]
    fake.n_features_in_ = n_features
    return fake


@pytest.fixture
def patched_xgb(monkeypatch):
    """隔离 xgboost + sklearn 依赖, 用 fake 实现"""
    monkeypatch.setattr("src.data.xgb_loader._train_xgb", _fake_train_xgb)
    # alpha_model.load() 内部 from import XgbV4Model 拿到的是 class 引用,
    # 改用 lazy import 模式: 改 import_module 拿到 fresh 引用
    # 最简方案: 直接 patch src.data.xgb_loader.XgbV4Model (alpha_model 通过 from import 也指向这个)
    import src.data.xgb_loader as xgb_loader_mod
    fake_class = MagicMock()
    fake_class.load = staticmethod(_fake_load_xgb_v4)
    # 修改 xgb_loader 模块的 XgbV4Model 名字
    monkeypatch.setattr(xgb_loader_mod, "XgbV4Model", fake_class)
    return monkeypatch


def test_a_stock_alpha_model_fit_marks_fitted(patched_xgb):
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randint(0, 2, 20).astype(np.float32)
    m.fit(X, y, feature_names=["a", "b", "c", "d", "e"])
    assert m.is_fitted is True
    assert m.n_features == 5


def test_a_stock_alpha_model_fit_rejects_empty(patched_xgb):
    m = AStockAlphaModel()
    with pytest.raises(ValueError, match="X / y"):
        m.fit(np.array([]), np.array([]))


def test_a_stock_alpha_model_predict_proba_shape(patched_xgb):
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randint(0, 2, 20).astype(np.float32)
    m.fit(X, y)
    probs = m.predict_proba(X)
    assert probs.shape == (20,)


def test_a_stock_alpha_model_predict_proba_in_range(patched_xgb):
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randint(0, 2, 20).astype(np.float32)
    m.fit(X, y)
    probs = m.predict_proba(X)
    assert (probs >= 0).all()
    assert (probs <= 1).all()


def test_a_stock_alpha_model_predict_proba_unfitted_raises():
    m = AStockAlphaModel()
    with pytest.raises(RuntimeError, match="未训练"):
        m.predict_proba(np.zeros((1, 5)))


def test_a_stock_alpha_model_predict_proba_wrong_features_raises(patched_xgb):
    m = AStockAlphaModel()
    X = np.random.randn(20, 5).astype(np.float32)
    y = np.random.randint(0, 2, 20).astype(np.float32)  # logistic 要 0/1
    m.fit(X, y)
    with pytest.raises(ValueError, match="X 列数"):
        m.predict_proba(np.zeros((1, 7)))


def test_a_stock_alpha_model_save_creates_file(tmp_path, patched_xgb):
    m = AStockAlphaModel()
    X = np.random.randn(10, 5).astype(np.float32)
    y = np.random.randint(0, 2, 10).astype(np.float32)
    m.fit(X, y)
    model_path = tmp_path / "xgb.json"
    scaler_path = tmp_path / "sc.json"
    m.save(model_path, scaler_path=scaler_path)
    assert model_path.exists()
    assert scaler_path.exists()


def test_a_stock_alpha_model_load_returns_fitted(patched_xgb, tmp_path):
    """修 2026-06-25: 用真 XgbV4Model.load 路径, 但 model/scaler 由 patched_xgb 提供"""
    model_path = tmp_path / "xgb.json"
    scaler_path = tmp_path / "sc.json"
    loaded = AStockAlphaModel.load(model_path, scaler_path=scaler_path)
    assert loaded.is_fitted is True
    assert loaded.n_features == 18  # fake load 默认 18


def test_a_stock_alpha_model_load_missing_file_raises(patched_xgb, tmp_path):
    """修 2026-06-25: XgbV4Model.load 缺文件抛 XgbV4LoadError

    注意: patched_xgb 替换了 XgbV4Model 为 MagicMock, 这里用一个特例:
    load 抛 XgbV4LoadError 来验我们的异常处理路径
    """
    from src.data.xgb_loader import XgbV4LoadError
    import src.data.xgb_loader as xgb_loader_mod
    from tests.test_research import _fake_load_xgb_v4

    # 临时改: 让 load 抛 XgbV4LoadError
    def boom_load(model_path, scaler_path, **kw):
        raise XgbV4LoadError(f"test: missing {model_path}")

    fake_class = MagicMock()
    fake_class.load = staticmethod(boom_load)
    patched_xgb.setattr(xgb_loader_mod, "XgbV4Model", fake_class)

    with pytest.raises(XgbV4LoadError, match="missing"):
        AStockAlphaModel.load(tmp_path / "missing.json", scaler_path=tmp_path / "missing_sc.json")


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


def test_lab_train_pipeline_runs(patched_xgb):
    lab = _make_lab()
    metrics = lab.train_pipeline("2024-01-01", "2024-01-31")
    assert "n_samples" in metrics
    assert "ic" in metrics
    assert metrics["n_samples"] > 0
    assert lab.is_trained is True
    assert lab.last_metrics == metrics


def test_lab_train_pipeline_saves_model(tmp_path, patched_xgb):
    lab = _make_lab()
    save_path = tmp_path / "lab_model.json"
    scaler_path = tmp_path / "lab_scaler.json"
    lab.train_pipeline(
        "2024-01-01", "2024-01-31",
        save_path=save_path, scaler_path=scaler_path,
    )
    assert save_path.exists()
    assert scaler_path.exists()


def test_lab_train_pipeline_empty_data_raises(patched_xgb):
    """dataset 返回空时, train_pipeline 应抛"""
    lab = _make_lab()
    with pytest.raises(ValueError, match="空数据"):
        lab.train_pipeline("2024-01-01", "2024-01-02")


def test_lab_predict_pipeline_returns_probs(patched_xgb):
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


def test_lab_save_load_roundtrip(tmp_path, patched_xgb):
    """训练 → 保存 → 加载 → 推理, 仍可用"""
    lab1 = _make_lab()
    lab1.train_pipeline("2024-01-01", "2024-01-31")

    model_path = tmp_path / "rt_model.json"
    scaler_path = tmp_path / "rt_scaler.json"
    lab1.save(model_path, scaler_path=scaler_path)

    # 用 fake load (默认 n_features=18), 测试 dataset 维度
    # 与 fake 对齐 (这里只验 roundtrip 流程)
    test_dataset = AStockDataset(lookback=3, horizon=2, n_features=5)
    inferred_n = 18  # AStockDataset.predict 实际返 3 stocks × 6 cols = 18

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


def test_lab_load_without_dataset_raises(tmp_path, patched_xgb):
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
