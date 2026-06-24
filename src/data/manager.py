"""
DataManager — 数据层统一门面 (2026-06-24)

设计目标:
  - 所有数据接口 (下载 / 行情 / 模型 / 缩放 / Datafeed) 走一个入口
  - 调用方: from src.data import data_mgr
  - 进程级单例,延迟初始化,避免冷启动开销
  - 不引入循环依赖 (用 TYPE_CHECKING + 延迟属性)

提供 5 类数据访问:
  1. downloader — 历史数据下载 (DataDownloader)
  2. westock    — 实时行情 (WestockClient)
  3. model      — XGBoost 模型 (XgbV4Model, 单例)
  4. scaler     — 特征缩放 (JsonScaler, 工厂方法)
  5. datafeed   — vnpy 风格 Datafeed (LocalDatafeed / ParquetDatafeed)

设计原则:
  - 5 类属性都是 lazy 的, 第一次访问才创建
  - 5 类属性都缓存, 第二次访问直接返回
  - 不预热, 不强制初始化, 启动 0 开销
  - 单测可以单独替换任何一个属性
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from . import westock
from .downloader import DataDownloader
from .xgb_loader import XgbV4Model, XgbV4LoadError, get_xgb_v4, clear_xgb_v4_cache
from .xgb_scaler import JsonScaler, load_scaler, save_scaler

if TYPE_CHECKING:
    import numpy as np


logger = logging.getLogger(__name__)


class DataManager:
    """数据层统一门面 (借鉴 vnpy.trader.MainEngine 风格, 2026-06-24)

    用法:
        from src.data import data_mgr

        # 1. 下载历史数据
        data_mgr.downloader.download_full()

        # 2. 实时行情
        kline = data_mgr.westock.get_kline("sh600000")

        # 3. 加载 XGBoost 模型 (单例)
        model = data_mgr.model
        prob = model.predict_proba(X)

        # 4. 加载 / 保存特征缩放器
        sc = data_mgr.load_scaler("data/xgb_scaler.json")
        data_mgr.save_scaler(sk_sc, features, "data/out.json")

        # 5. vnpy 风格 Datafeed (懒加载, 子模块未建时不报错)
        bars = data_mgr.datafeed.query_bar("000001.SZ", "1d")
    """

    # ── 默认路径配置 (可被 set_model_paths 覆盖) ──
    DEFAULT_MODEL_PATH = "data/xgb_model.json"
    DEFAULT_SCALER_PATH = "data/xgb_scaler.json"

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # ── 5 类数据源 (lazy) ──
        self._downloader: Optional[DataDownloader] = None
        # westock 是 module (一组函数), 不需要实例化, 标记用
        self._westock_ready: bool = False
        self._model: Optional[XgbV4Model] = None

        # ── 路径配置 ──
        self._model_path: Union[str, Path] = self.DEFAULT_MODEL_PATH
        self._scaler_path: Union[str, Path] = self.DEFAULT_SCALER_PATH

        self._log = logger

    # ─────────────────────────────────────────
    #  1. downloader — 历史数据下载
    # ─────────────────────────────────────────

    @property
    def downloader(self) -> DataDownloader:
        """历史数据下载器 (akshare/baostock)"""
        if self._downloader is None:
            with self._lock:
                if self._downloader is None:
                    self._log.info("初始化 DataDownloader")
                    self._downloader = DataDownloader()
        return self._downloader

    # ─────────────────────────────────────────
    #  2. westock — 实时行情
    # ─────────────────────────────────────────

    @property
    def westock(self):
        """实时行情 (westock-data-clawhub) — module, 暴露 get_kline/get_technical 等函数

        首次访问时打 log 确认, 后续直接返回 module 引用
        """
        if not self._westock_ready:
            with self._lock:
                if not self._westock_ready:
                    self._log.info("Westock 模块就绪 (npx westock-data-clawhub)")
                    self._westock_ready = True
        return westock

    # ─────────────────────────────────────────
    #  3. model — XGBoost 模型 (单例)
    # ─────────────────────────────────────────

    @property
    def model(self) -> XgbV4Model:
        """XGBoost v4 模型 (进程级单例, lazy 加载)"""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._log.info("加载 XGBoost 模型: %s", self._model_path)
                    self._model = get_xgb_v4(
                        model_path=self._model_path,
                        scaler_path=self._scaler_path,
                    )
        return self._model

    def reload_model(self) -> XgbV4Model:
        """强制重新加载 XGBoost 模型 (热更新 / 测试用)"""
        with self._lock:
            self._log.info("强制重载 XGBoost 模型")
            clear_xgb_v4_cache()
            self._model = None
        return self.model  # 触发 lazy 重新加载

    def set_model_paths(
        self,
        model_path: Union[str, Path],
        scaler_path: Union[str, Path],
    ) -> None:
        """修改模型路径 (下次访问 model 时生效)"""
        with self._lock:
            self._model_path = Path(model_path)
            self._scaler_path = Path(scaler_path)
            self._model = None  # 失效缓存

    # ─────────────────────────────────────────
    #  4. scaler — 特征缩放 (工厂方法, 每次新建)
    # ─────────────────────────────────────────

    def load_scaler(self, path: Union[str, Path]) -> dict:
        """加载 JsonScaler (包装 xgb_scaler.load_scaler, 路径校验)

        Returns:
            {"scaler": JsonScaler, "feature_names": list[str]}

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: JSON 字段缺失/非法
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"scaler 文件不存在: {path}")
        return load_scaler(path)

    def save_scaler(
        self,
        scaler: Any,
        feature_names: list[str],
        path: Union[str, Path],
    ) -> Path:
        """保存 scaler 到 JSON (替代 pickle)"""
        return save_scaler(scaler, feature_names, Path(path))

    # ─────────────────────────────────────────
    #  5. datafeed — vnpy 风格 Datafeed
    # ─────────────────────────────────────────

    @property
    def datafeed(self):
        """vnpy 风格 Datafeed 抽象 (lazy, 子模块未建时 None)

        返回 BaseDatafeed 子类实例 (LocalDatafeed / ParquetDatafeed),
        由 src.data.datafeed 包决定具体类型。

        如果 datafeed 子模块未建, 属性访问会抛出 ImportError
        (明示: 你想用 datafeed 就先把子模块建好)
        """
        # 延迟 import 避免循环依赖, 也避免未建子模块时影响其他 4 类
        from .datafeed import get_datafeed

        return get_datafeed()

    # ─────────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────────

    def close(self) -> None:
        """关闭所有数据源 (释放资源)"""
        with self._lock:
            self._downloader = None
            self._westock_ready = False
            clear_xgb_v4_cache()
            self._model = None
            self._log.info("DataManager 已关闭")

    def stats(self) -> dict:
        """返回当前状态 (调试用)"""
        return {
            "downloader": "initialized" if self._downloader else "lazy",
            "westock": "ready" if self._westock_ready else "lazy",
            "model": "loaded" if self._model else "lazy",
            "model_path": str(self._model_path),
            "scaler_path": str(self._scaler_path),
        }

    def __repr__(self) -> str:
        return (
            f"DataManager(downloader={'✓' if self._downloader else '○'}, "
            f"westock={'✓' if self._westock_ready else '○'}, "
            f"model={'✓' if self._model else '○'})"
        )


# ── 进程级单例 ─────────────────────────


_instance: Optional[DataManager] = None
_instance_lock = threading.Lock()


def get_data_manager() -> DataManager:
    """获取 DataManager 单例 (线程安全)"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = DataManager()
    return _instance


# 便捷别名: from src.data import data_mgr
# 修 2026-06-25: 之前模块级 data_mgr = get_data_manager() 立即创建实例,
# 违背 "lazy 启动 0 开销" 承诺。现在用 __getattr__ 代理, 首次访问才创建。
_lazy_data_mgr: Optional[DataManager] = None


def __getattr__(name: str):
    """模块级代理: data_mgr 首次访问才创建, 保持 lazy"""
    global _lazy_data_mgr
    if name == "data_mgr":
        if _lazy_data_mgr is None:
            _lazy_data_mgr = get_data_manager()
        return _lazy_data_mgr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DataManager",
    "get_data_manager",
    "data_mgr",
    "XgbV4LoadError",  # 透传, 方便调用方 from src.data import XgbV4LoadError
]
