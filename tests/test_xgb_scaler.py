"""
xgb_scaler 单测 — JSON 格式 scaler 的 roundtrip 和校验逻辑

覆盖:
- JsonScaler.transform() 数学正确性
- save_scaler/load_scaler roundtrip
- 字段缺失、长度不符、n_features 非法 等校验失败路径
- 与 sklearn StandardScaler 的接口兼容(只要对象有 mean_/scale_/n_features_in_)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.xgb_scaler import (
    JsonScaler,
    load_scaler,
    save_scaler,
)


# ============================================================
# JsonScaler
# ============================================================


class TestJsonScaler:
    def test_transform_math_correct(self):
        """transform 应该返回 (X - mean) / scale"""
        s = JsonScaler(
            mean=[1.0, 2.0, 3.0],
            scale=[0.5, 1.0, 1.5],
            n_features=3,
        )
        X = np.array([[1.0, 2.0, 3.0], [2.0, 4.0, 6.0]])
        out = s.transform(X)
        expected = (X - np.array([1.0, 2.0, 3.0])) / np.array([0.5, 1.0, 1.5])
        assert np.allclose(out, expected)

    def test_transform_accepts_list(self):
        """transform 应支持 list 输入(不只 np.ndarray)"""
        s = JsonScaler(mean=[0.0, 0.0], scale=[1.0, 1.0], n_features=2)
        out = s.transform([[1.0, 2.0]])
        assert np.allclose(out, [[1.0, 2.0]])

    def test_feature_names_preserved(self):
        s = JsonScaler(
            mean=[0.0, 0.0],
            scale=[1.0, 1.0],
            n_features=2,
            feature_names=["foo", "bar"],
        )
        assert list(s.feature_names_in_) == ["foo", "bar"]

    def test_mean_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="mean 长度"):
            JsonScaler(mean=[1.0, 2.0, 3.0], scale=[1.0, 1.0], n_features=2)

    def test_scale_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="scale 长度"):
            JsonScaler(mean=[1.0, 2.0], scale=[1.0, 1.0, 1.0], n_features=2)

    def test_to_dict_roundtrip(self):
        s = JsonScaler(
            mean=[1.5, -2.0, 3.14],
            scale=[0.5, 1.0, 2.0],
            n_features=3,
            feature_names=["a", "b", "c"],
        )
        d = s.to_dict()
        assert d["mean"] == [1.5, -2.0, 3.14]
        assert d["scale"] == [0.5, 1.0, 2.0]
        assert d["n_features"] == 3
        assert d["feature_names"] == ["a", "b", "c"]


# ============================================================
# save_scaler / load_scaler
# ============================================================


class TestSaveLoadRoundtrip:
    def _make_mock_scaler(self):
        """模拟 sklearn StandardScaler 接口(只关心 mean_/scale_/n_features_in_)"""
        class MockScaler:
            mean_ = np.array([1.0, 2.0, 3.0])
            scale_ = np.array([0.5, 1.0, 1.5])
            n_features_in_ = 3
        return MockScaler()

    def test_roundtrip_preserves_data(self, tmp_path):
        scaler = self._make_mock_scaler()
        feature_names = ["rsi14", "bb_position", "max_dd_60"]
        path = tmp_path / "xgb_scaler.json"

        result = save_scaler(scaler, feature_names, path)
        assert result == path
        assert path.exists()

        loaded = load_scaler(path)
        assert np.allclose(loaded["scaler"].mean_, scaler.mean_)
        assert np.allclose(loaded["scaler"].scale_, scaler.scale_)
        assert loaded["scaler"].n_features_in_ == 3
        assert loaded["feature_names"] == feature_names

    def test_creates_parent_dir(self, tmp_path):
        scaler = self._make_mock_scaler()
        path = tmp_path / "deeply" / "nested" / "scaler.json"
        save_scaler(scaler, ["a", "b", "c"], path)
        assert path.exists()

    def test_load_missing_file_raises(self, tmp_path):
        path = tmp_path / "nope.json"
        with pytest.raises(FileNotFoundError, match="不存在"):
            load_scaler(path)

    def test_load_missing_field_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        # 缺 mean
        path.write_text(json.dumps({
            "scale": [1.0, 1.0],
            "n_features": 2,
            "feature_names": ["a", "b"],
        }))
        with pytest.raises(ValueError, match="缺少字段"):
            load_scaler(path)

    def test_load_n_features_zero_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text(json.dumps({
            "mean": [1.0],
            "scale": [1.0],
            "n_features": 0,
            "feature_names": ["a"],
        }))
        with pytest.raises(ValueError, match="n_features 非法"):
            load_scaler(path)

    def test_load_n_features_string_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text(json.dumps({
            "mean": [1.0],
            "scale": [1.0],
            "n_features": "not an int",
            "feature_names": ["a"],
        }))
        with pytest.raises(ValueError, match="n_features 非法"):
            load_scaler(path)

    def test_json_format_human_readable(self, tmp_path):
        """JSON 应该是可读的,不应该包含 pickle 的二进制"""
        path = tmp_path / "xgb_scaler.json"
        save_scaler(self._make_mock_scaler(), ["a", "b", "c"], path)
        content = path.read_text(encoding="utf-8")
        assert "pickle" not in content
        # 应该含中文 / JSON 格式化
        assert "\n" in content  # indented

    def test_accepts_pathlib_and_str(self, tmp_path):
        """save_scaler 应同时支持 str 和 Path"""
        scaler = self._make_mock_scaler()
        features = ["a", "b", "c"]

        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        save_scaler(scaler, features, str(p1))  # str
        save_scaler(scaler, features, p2)        # Path
        assert p1.exists() and p2.exists()


# ============================================================
# 回归保护:防止未来有人手贱改回 pickle
# ============================================================


class TestNoPickleRegression:
    """防止有人回退到 pickle 持久化方案(已知 RCE 风险)"""

    def test_param_server_does_not_load_pickle(self):
        """scripts/param_server.py 不应该 import pickle 或调用 pickle.load"""
        path = _PROJECT_ROOT / "scripts" / "param_server.py"
        src = path.read_text(encoding="utf-8")
        assert "import pickle" not in src, "param_server 不应再 import pickle"
        assert "pickle.load" not in src, "param_server 不应再调用 pickle.load"
        assert "_pickle.load" not in src, "param_server 不应再调用 _pickle.load"
        # 应该改用 load_scaler 从 .json 读
        assert "load_scaler" in src
        assert "xgb_scaler.json" in src

    def test_training_scripts_write_json(self):
        """训练脚本应该写 .json 而非 .pkl"""
        # train_xgb_model.py 已被 train_xgb_v4.py 取代 (E2 清理, 2026-06-22)
        for script in ["train_xgb_v4.py"]:
            path = _PROJECT_ROOT / "scripts" / script
            if not path.exists():
                continue
            src = path.read_text(encoding="utf-8")
            assert "pickle.dump" not in src, f"{script} 不应再 pickle.dump"
            assert "save_scaler" in src, f"{script} 应使用 save_scaler"
            assert "xgb_scaler.json" in src, f"{script} 应写 .json"
