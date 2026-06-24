"""
BaseDatafeed — 数据源抽象基类 (借鉴 vnpy.alpha.dataset.BaseDataset)
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from datetime import date, datetime
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from ...gateway import BarData, ContractData


logger = logging.getLogger(__name__)


# ── Interval 常量 (借鉴 vnpy Interval) ──────────────


class Interval:
    """K 线周期常量 (vnpy 风格)"""
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    DAY_1 = "1d"
    WEEK_1 = "1w"
    MONTH_1 = "1M"

    SUPPORTED = (MINUTE_1, MINUTE_5, MINUTE_15, MINUTE_30, HOUR_1, DAY_1, WEEK_1, MONTH_1)


# ── A 股市场代码推断 (与 downloader.py 一致) ────────────


def code_to_market(code: str) -> str:
    """
    根据股票代码推断市场

    6xxxxx → SH (上海主板/科创板)
    0xxxxx / 3xxxxx → SZ (深圳主板/创业板)
    4xxxxx / 8xxxxx → BJ (北交所)
    """
    if code.startswith("6"):
        return "SH"
    elif code.startswith(("0", "3")):
        return "SZ"
    elif code.startswith(("4", "8")):
        return "BJ"
    return "OTHER"


def code_to_vt_symbol(code: str) -> str:
    """000001.SZ / 600519.SH"""
    return f"{code}.{code_to_market(code)}"


def vt_symbol_to_code(vt_symbol: str) -> str:
    """'000001.SZ' → '000001'"""
    return vt_symbol.split(".")[0]


def vt_symbol_to_exchange(vt_symbol: str) -> str:
    """'000001.SZ' → 'SZ'"""
    return vt_symbol.split(".")[1]


# ── BaseDatafeed 抽象基类 ─────────────────────────────


class BaseDatafeed(metaclass=ABCMeta):
    """
    数据源抽象基类 (借鉴 vnpy.alpha.dataset.BaseDataset)

    子类必须实现:
      - get_bars(): 取历史 K 线
      - get_stock_list(): 取全市场合约列表

    默认实现 (子类可覆盖):
      - get_bars_by_date(): 给定日期 + 股票池, 拉一批 BarData (回测用)
      - get_trading_calendar(): 取交易日历
    """

    name: str = "BASE"

    def __init__(self) -> None:
        self.inited: bool = False

    def init(self) -> None:
        """初始化 (子类可覆盖, e.g. 建连接)"""
        self.inited = True

    def close(self) -> None:
        """关闭资源 (子类可覆盖)"""
        self.inited = False

    # ── 必须实现的抽象方法 ───────────────────────

    @abstractmethod
    def get_bars(
        self,
        vt_symbol: str,
        interval: str = Interval.DAY_1,
        start: Optional[date] = None,
        end: Optional[date] = None,
        count: Optional[int] = None,
    ) -> List["BarData"]:
        """
        取历史 K 线

        Args:
            vt_symbol: vt 格式代码 "000001.SZ"
            interval: K 线周期 (默认 1d)
            start: 起始日期 (含)
            end: 结束日期 (含)
            count: 取最近 N 条 (与 start/end 互斥)

        Returns:
            List[BarData], 按 datetime 升序
        """

    @abstractmethod
    def get_stock_list(self) -> List["ContractData"]:
        """
        取全市场合约列表

        Returns:
            List[ContractData], 含 vt_symbol / symbol / exchange / name
        """

    # ── 默认实现 ─────────────────────────────────

    def get_bars_by_date(
        self,
        query_date: date,
        universe: Optional[List[str]] = None,
        interval: str = Interval.DAY_1,
    ) -> Dict[str, "BarData"]:
        """
        给定日期, 拉一批 BarData (回测常用)

        Args:
            query_date: 切片日期
            universe: vt_symbol 列表 (None = 全市场)
            interval: K 线周期

        Returns:
            Dict[vt_symbol, BarData], 仅含当日有数据的股票
        """
        if universe is None:
            universe = [c.vt_symbol for c in self.get_stock_list()]

        result: Dict[str, "BarData"] = {}
        for vt_symbol in universe:
            bars = self.get_bars(vt_symbol, interval, start=query_date, end=query_date)
            if bars:
                result[vt_symbol] = bars[0]
        return result

    def get_trading_calendar(
        self,
        start: date,
        end: date,
    ) -> List[date]:
        """
        取交易日历 (默认从 daily_price 推断)

        子类可覆盖 (BaoStock 走 bs.query_trade_dates 更准)
        """
        # 走 DataManager 统一入口 (2026-06-24, 避免循环 + 路径硬编码)
        from ...models.repository import DataRepository
        repo = DataRepository()
        with repo.engine.connect() as conn:
            from sqlalchemy import text
            rows = conn.execute(text(
                "SELECT DISTINCT trade_date FROM daily_price "
                "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
            ), {"s": start, "e": end}).fetchall()
        return [r[0] for r in rows if isinstance(r[0], date)]

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} inited={self.inited}>"