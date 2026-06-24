"""
src.data — 数据层统一入口 (2026-06-24)

设计目标:
  - 所有数据接口都来自一个 data_mgr (DataManager 门面)
  - 调用方: from src.data import data_mgr
  - 不再需要 from src.data.xxx import Yyy 散落式调用

模块划分:
  - downloader.py  :  DataDownloader — 历史数据下载 (akshare/baostock)
  - westock.py     :  westock 行情函数 (get_kline/get_technical/...) — npx westock-data-clawhub
  - xgb_loader.py  :  XgbV4Model — XGBoost 模型加载
  - xgb_scaler.py  :  JsonScaler / load_scaler — 特征缩放 (替代 pickle)
  - manager.py     :  DataManager — 统一门面 (单例 data_mgr)

子模块:
  - datafeed/      :  vnpy 风格 Datafeed 抽象 (BaseDatafeed/LocalDatafeed/ParquetDatafeed)
"""
from __future__ import annotations

# ── 公共 API: 调用方只需要 from src.data import <Name> ──
from . import westock  # module (一组函数)
from .downloader import DataDownloader
from .xgb_loader import XgbV4Model, XgbV4LoadError
from .xgb_scaler import JsonScaler, load_scaler

# ── 统一门面 (推荐入口) ──
from .manager import DataManager, get_data_manager, data_mgr

__all__ = [
    # 基础类 / 模块
    "DataDownloader",
    "westock",  # module (含 get_kline/get_technical/...)
    "XgbV4Model",
    "XgbV4LoadError",
    "JsonScaler",
    "load_scaler",
    # 统一门面 (新代码首选)
    "DataManager",
    "get_data_manager",
    "data_mgr",
]
