"""
DataManager 统一门面单测 — src/data/manager.py (2026-06-24)

涵盖:
  - 单例语义
  - 5 类数据源 lazy 初始化
  - setter / setter_失效缓存 (model 路径切换)
  - 工厂方法 (get_datafeed)
  - 关闭 + 资源释放
  - 老 import 兼容性 (DataDownloader 等仍可直接 import)
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data import DataManager, get_data_manager, data_mgr
from src.data.manager import DataManager as DMDirect


# ── 单例语义 ──────────────────────────


def test_data_mgr_is_singleton():
    """data_mgr 是进程级单例"""
    assert get_data_manager() is data_mgr
    assert data_mgr is get_data_manager()


def test_get_data_manager_returns_same_instance():
    a = get_data_manager()
    b = get_data_manager()
    assert a is b


def test_data_mgr_repr_includes_status():
    r = repr(data_mgr)
    assert "DataManager" in r
    assert "downloader" in r
    assert "westock" in r
    assert "model" in r


# ── 5 类数据源 lazy ──────────────────────────


def test_downloader_is_lazy():
    """download 未访问时为 None"""
    mgr = DataManager()
    assert mgr._downloader is None


def test_downloader_creates_on_first_access(monkeypatch):
    """首次访问触发 DataDownloader 构造"""
    mgr = DataManager()
    fake = MagicMock()
    monkeypatch.setattr("src.data.manager.DataDownloader", lambda: fake)
    assert mgr.downloader is fake
    # 第二次访问不重建
    assert mgr.downloader is fake


def test_westock_is_module():
    """westock 是 module (一组函数), 不是类实例"""
    from src.data import westock
    assert mgr.westock is westock if False else data_mgr.westock is westock


def test_data_mgr_module_attr_is_lazy():
    """修 2026-06-25: 之前模块级 data_mgr = get_data_manager() 立即创建,
    现在用 __getattr__ 代理, 首次访问才创建。

    验证: 通过 src.data.manager.data_mgr 访问触发 lazy 创建,
    第二次返回同一实例, 内部 _lazy_data_mgr 已设置。
    """
    import importlib
    import sys
    # 强制重置 (如果之前 import 时已创建, 重新加载模块)
    if "src.data.manager" in sys.modules:
        mgr_mod = importlib.reload(sys.modules["src.data.manager"])
    else:
        mgr_mod = importlib.import_module("src.data.manager")
    # 重置 lazy 状态
    mgr_mod._lazy_data_mgr = None
    # 通过模块属性访问触发 __getattr__
    dm = mgr_mod.data_mgr
    assert dm is not None
    # 第二次访问应返回同一实例 (不再创建)
    dm2 = mgr_mod.data_mgr
    assert dm is dm2
    # 内部 _lazy_data_mgr 应已设置
    assert mgr_mod._lazy_data_mgr is dm


def test_westock_module_has_expected_functions():
    """westock module 暴露 get_kline / get_technical 等"""
    from src.data import westock
    assert hasattr(westock, "get_kline")
    assert hasattr(westock, "get_technical")
    assert hasattr(westock, "get_profile")
    assert callable(westock.get_kline)


def test_model_is_lazy():
    """model 未访问时为 None"""
    mgr = DataManager()
    assert mgr._model is None


def test_model_loads_with_default_paths(monkeypatch, tmp_path):
    """默认路径 model_path='data/xgb_model.json'"""
    # mock get_xgb_v4 避免真的去 load 模型
    fake_model = MagicMock()
    monkeypatch.setattr("src.data.manager.get_xgb_v4", lambda **kw: fake_model)

    mgr = DataManager()
    mgr._model_path = tmp_path / "xgb_model.json"
    mgr._scaler_path = tmp_path / "xgb_scaler.json"
    mgr._model = None

    assert mgr.model is fake_model


def test_model_cached_after_first_load(monkeypatch):
    """model 单例, 第二次访问不重建"""
    fake_model = MagicMock()
    call_count = [0]

    def fake_load(**kw):
        call_count[0] += 1
        return fake_model

    monkeypatch.setattr("src.data.manager.get_xgb_v4", fake_load)

    mgr = DataManager()
    mgr._model = None
    a = mgr.model
    b = mgr.model
    assert a is b
    assert call_count[0] == 1


def test_reload_model_clears_cache(monkeypatch):
    """reload_model 强制重载"""
    m1, m2 = MagicMock(), MagicMock()
    call_count = [0]

    def fake_load(**kw):
        call_count[0] += 1
        return m1 if call_count[0] == 1 else m2

    monkeypatch.setattr("src.data.manager.get_xgb_v4", fake_load)
    monkeypatch.setattr("src.data.manager.clear_xgb_v4_cache", lambda: None)

    mgr = DataManager()
    mgr._model = None
    a = mgr.model
    b = mgr.reload_model()
    assert a is m1
    assert b is m2


def test_set_model_paths_invalidates_cache(monkeypatch):
    """set_model_paths 后 model 失效"""
    monkeypatch.setattr("src.data.manager.get_xgb_v4", lambda **kw: MagicMock())
    mgr = DataManager()
    mgr._model = MagicMock()  # 假设已加载
    mgr.set_model_paths("a.json", "b.json")
    assert mgr._model is None
    assert str(mgr._model_path) == "a.json"
    assert str(mgr._scaler_path) == "b.json"


# ── scaler 工厂方法 ──────────────────────────


def test_load_scaler_file_not_found(tmp_path):
    """load_scaler 文件不存在时抛 FileNotFoundError"""
    mgr = DataManager()
    with pytest.raises(FileNotFoundError, match="不存在"):
        mgr.load_scaler(tmp_path / "missing.json")


def test_load_scaler_returns_dict_with_scaler_and_features(tmp_path):
    """load_scaler 成功返回 {'scaler': JsonScaler, 'feature_names': [...]}"""
    # 写一个合法 scaler json
    scaler_file = tmp_path / "sc.json"
    data = {
        "mean": [1.0, 2.0, 3.0],
        "scale": [0.5, 1.0, 1.5],
        "n_features": 3,
        "feature_names": ["f1", "f2", "f3"],
    }
    scaler_file.write_text(json.dumps(data), encoding="utf-8")

    mgr = DataManager()
    result = mgr.load_scaler(scaler_file)
    assert "scaler" in result
    assert "feature_names" in result
    assert result["feature_names"] == ["f1", "f2", "f3"]
    assert result["scaler"].n_features_in_ == 3


def test_load_scaler_invalid_json_raises(tmp_path):
    """load_scaler 缺字段抛 ValueError"""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps({"mean": [], "scale": []}), encoding="utf-8")
    mgr = DataManager()
    with pytest.raises(ValueError, match="缺少字段"):
        mgr.load_scaler(bad_file)


def test_save_scaler_creates_file(tmp_path):
    """save_scaler 写 JSON 文件"""
    from src.data import JsonScaler
    sc = JsonScaler(mean=[0.0, 0.0], scale=[1.0, 1.0], n_features=2, feature_names=["a", "b"])
    out = tmp_path / "out.json"
    mgr = DataManager()
    result = mgr.save_scaler(sc, ["a", "b"], out)
    assert result == out
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["n_features"] == 2
    assert data["feature_names"] == ["a", "b"]


# ── datafeed 工厂 ──────────────────────────


def test_datafeed_lazy_returns_local_by_default(monkeypatch):
    """data_mgr.datafeed 默认是 LocalDatafeed"""
    from src.data.datafeed import LocalDatafeed
    # 隔离: 重置全局 _instance
    import src.data.datafeed as df_mod
    df_mod._instance = None
    df_mod._kind = "local"

    monkeypatch.setattr("src.data.datafeed.LocalDatafeed", MagicMock(return_value=MagicMock(spec=LocalDatafeed)))
    # 跳过实际 init
    fake = MagicMock()
    fake.init = MagicMock()
    monkeypatch.setattr("src.data.datafeed.LocalDatafeed", lambda: fake)

    df = data_mgr.datafeed
    assert df is fake


# ── 关闭 / 释放 ──────────────────────────


def test_close_clears_all_caches(monkeypatch):
    """close() 清空 5 个缓存"""
    mgr = DataManager()
    mgr._downloader = MagicMock()
    mgr._westock_ready = True
    mgr._model = MagicMock()
    clear_calls = [0]
    monkeypatch.setattr(
        "src.data.manager.clear_xgb_v4_cache",
        lambda: clear_calls.__setitem__(0, clear_calls[0] + 1),
    )

    mgr.close()
    assert mgr._downloader is None
    assert mgr._westock_ready is False
    assert mgr._model is None
    assert clear_calls[0] == 1


def test_stats_returns_dict_with_expected_keys():
    """stats() 返回 5 个 key"""
    mgr = DataManager()
    s = mgr.stats()
    assert set(s.keys()) == {
        "downloader", "westock", "model", "model_path", "scaler_path",
    }
    assert s["downloader"] == "lazy"
    assert s["westock"] == "lazy"
    assert s["model"] == "lazy"


def test_stats_shows_initialized_state(monkeypatch):
    """stats() 反映 lazy → initialized 状态"""
    mgr = DataManager()
    mgr._downloader = MagicMock()
    mgr._westock_ready = True
    mgr._model = MagicMock()
    s = mgr.stats()
    assert s["downloader"] == "initialized"
    assert s["westock"] == "ready"
    assert s["model"] == "loaded"


# ── 老 import 兼容 ──────────────────────────


def test_old_import_paths_still_work():
    """老 import 路径 (DataDownloader / XgbV4Model / JsonScaler) 仍可用"""
    from src.data import DataDownloader, XgbV4Model, JsonScaler, load_scaler
    assert DataDownloader is not None
    assert XgbV4Model is not None
    assert JsonScaler is not None
    assert callable(load_scaler)


def test_data_manager_default_model_paths():
    """默认 model_path / scaler_path"""
    mgr = DataManager()
    assert "xgb_model.json" in str(mgr._model_path)
    assert "xgb_scaler.json" in str(mgr._scaler_path)


# ── 线程安全 (基本验证) ──────────────────────────


def test_data_mgr_lock_is_rlock():
    """DataManager 内部用 RLock 支持重入"""
    mgr = DataManager()
    import threading
    assert isinstance(mgr._lock, type(threading.RLock()))


def test_concurrent_downloader_access_is_safe(monkeypatch):
    """多线程首次访问 downloader 不会重复创建"""
    fake = MagicMock()
    call_count = [0]

    def factory():
        call_count[0] += 1
        return fake

    monkeypatch.setattr("src.data.manager.DataDownloader", factory)

    import threading
    mgr = DataManager()
    mgr._downloader = None

    results = []

    def worker():
        results.append(mgr.downloader)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 即使多次访问, 也只创建一次
    assert call_count[0] == 1
    assert all(r is fake for r in results)
