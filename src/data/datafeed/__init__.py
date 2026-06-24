"""
src.data.datafeed — Datafeed 子包 (2026-06-24)

原位置: src/datafeed/ (顶层)
新位置: src/data/datafeed/ (归到 data/ 下, 配合 DataManager 统一门面)

vnpy 4.4 设计:
  - BaseDatafeed: 抽象基类 (get_bars / get_stock_list / ...)
  - 本项目:
      BaseDatafeed (ABC)
        ├── LocalDatafeed    (本地 SQLite, 主用, 小 universe 快)
        └── ParquetDatafeed  (本地 Parquet, 全市场 scan 快)

回测/模拟盘 流程:
  datafeed = get_datafeed("local")
  bars = datafeed.get_bars("000001.SZ", "1d", start=date(2024,1,1))

DataManager 入口:
  from src.data import data_mgr
  bars = data_mgr.datafeed.get_bars("000001.SZ", "1d")
"""
from .base import BaseDatafeed, Interval
from .local import LocalDatafeed
from .parquet import ParquetDatafeed

__all__ = [
    "BaseDatafeed",
    "Interval",
    "LocalDatafeed",
    "ParquetDatafeed",
    "get_datafeed",
    "set_datafeed_kind",
]


# ── Datafeed 工厂 (单例 + 可切换) ────────────────────
# 默认 kind: "local" (SQLite, 小 universe 回测)
# 切换:     set_datafeed_kind("parquet")
#
# 切换是全局的, 用于一次性脚本 (如特征工程用 Parquet, 回测用 Local)

_kind: str = "local"
_instance = None


def get_datafeed(kind: str = None):
    """
    获取 Datafeed 单例 (工厂方法)

    Args:
        kind: "local" (默认) / "parquet" / None (用上次 set 的)

    Returns:
        BaseDatafeed 子类实例 (LocalDatafeed / ParquetDatafeed)
    """
    global _instance, _kind

    # 显式传 kind 覆盖
    if kind is not None:
        _kind = kind
        _instance = None  # 强制重建

    if _instance is None:
        if _kind == "local":
            _instance = LocalDatafeed()
        elif _kind == "parquet":
            _instance = ParquetDatafeed()
        else:
            raise ValueError(
                f"未知 datafeed kind: {_kind!r} (期望 'local' / 'parquet')"
            )
        _instance.init()
    return _instance


def set_datafeed_kind(kind: str) -> None:
    """切换 datafeed 类型 (下次 get_datafeed() 生效)"""
    global _kind, _instance
    if kind not in ("local", "parquet"):
        raise ValueError(
            f"未知 datafeed kind: {kind!r} (期望 'local' / 'parquet')"
        )
    if kind != _kind:
        _kind = kind
        _instance = None  # 失效旧实例
