"""
Datafeed 包 — 数据源抽象层 (借鉴 vnpy.alpha.dataset 设计)

vnpy 4.4 设计:
  - BaseDatafeed: 抽象基类 (get_bars / get_stock_list / ...)
  - 本项目:
      BaseDatafeed (ABC)
        ├── LocalDatafeed   (本地 SQLite, 主用)
        └── BaoStockDatafeed (兜底, 首次补全)

回测/模拟盘 流程:
  LocalDatafeed.get_bars_by_date(date, universe)
    → Dict[vt_symbol, BarData]
    → 给 PortfolioEngine / AlphaStrategy 直接用
"""
from .base import BaseDatafeed, Interval
from .local import LocalDatafeed
from .parquet import ParquetDatafeed

__all__ = ["BaseDatafeed", "Interval", "LocalDatafeed", "ParquetDatafeed"]