"""
BaseDatafeed — 数据源抽象基类 (借鉴 vnpy.alpha.dataset.BaseDataset)

ADR-0010 (2026-06-27): 修 base.py 自己绕过自己的反模式
- get_trading_calendar 默认实现不再 import DataRepository,改为从
  self.get_bars("000001.SH", interval="1d", start, end) 推断交易日历
- 子类可注入 _calendar_provider 覆盖默认行为 (如 BaoStockDatafeed)
- 新增 get_industry_map / get_news_events 抽象方法,子类必须实现
  (datafeed 是基础数据统一入口,业务宽表仍走 DataRepository 保留)
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

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


# ── 基础数据 dataclass ──────────────────────────


@dataclass(frozen=True)
class NewsEvent:
    """研报 / 新闻事件 (ADR-0010 基础数据抽象,2026-06-27)

    数据源: research_report / em_global_news / announcements 表
    调用方: scoring.news_event_scorer / scoring.sentiment_scorer
    """
    code: str
    date: date
    title: str = ""
    rating: Optional[str] = None        # 研报评级 (买入/增持/中性/...)
    rating_change: Optional[str] = None  # 评级变化 (上调/维持/下调)
    author: Optional[str] = None        # 作者 / 发布机构
    institution: Optional[str] = None
    url: Optional[str] = None
    source: str = "research_report"     # 来源表名


# ── BaseDatafeed 抽象基类 ─────────────────────────────


class BaseDatafeed(metaclass=ABCMeta):
    """
    数据源抽象基类 (借鉴 vnpy.alpha.dataset.BaseDataset)

    子类必须实现:
      - get_bars(): 取历史 K 线
      - get_stock_list(): 取全市场合约列表
      - get_industry_map(): 取股票-行业映射 (ADR-0010 新增)
      - get_news_events(): 取研报 / 新闻事件 (ADR-0010 新增)

    默认实现 (子类可覆盖):
      - get_bars_by_date(): 给定日期 + 股票池, 拉一批 BarData (回测用)
      - get_trading_calendar(): 取交易日历 (默认从 000001.SH 1d 推断)
    """

    name: str = "BASE"

    # ADR-0010 (2026-06-27): 推断交易日历用的基准合约 (上证指数)
    _CALENDAR_PROBE_SYMBOL: str = "000001.SH"

    def __init__(self) -> None:
        self.inited: bool = False
        # ADR-0010: 可注入的日历 provider (签名: (start, end) -> List[date])
        # 默认 None → 走 get_bars 推断
        self._calendar_provider: Optional[Callable[[date, date], List[date]]] = None

    def init(self) -> None:
        """初始化 (子类可覆盖, e.g. 建连接)"""
        self.inited = True

    def close(self) -> None:
        """关闭资源 (子类可覆盖)"""
        self.inited = False

    def set_calendar_provider(
        self, provider: Callable[[date, date], List[date]]
    ) -> None:
        """注入自定义日历 provider (ADR-0010)

        用法 (BaoStockDatafeed):
            self.set_calendar_provider(lambda s, e: bs.query_trade_dates(start=s, end=e))

        Args:
            provider: callable(start: date, end: date) -> List[date]
        """
        self._calendar_provider = provider

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

    @abstractmethod
    def get_industry_map(self, codes: List[str]) -> Dict[str, str]:
        """
        取股票-行业映射 (ADR-0010 基础数据抽象)

        Args:
            codes: 股票代码列表 (6 位不带 .SH/.SZ)

        Returns:
            {code: industry}, 缺失为 "" (调用方需自行 fallback "未知")
        """

    @abstractmethod
    def get_news_events(
        self,
        codes: List[str],
        start: date,
        end: date,
    ) -> List[NewsEvent]:
        """
        取研报 / 新闻事件 (ADR-0010 基础数据抽象)

        Args:
            codes: 股票代码列表 (空 = 全市场)
            start: 起始日期 (含)
            end: 结束日期 (含)

        Returns:
            List[NewsEvent]
        """

    @abstractmethod
    def get_finance_snapshot(self, codes: List[str]) -> Dict[str, Dict[str, float]]:
        """
        取财务快照 (ADR-0010 基础数据抽象)

        用于 PE/ROE/net_profit 等财务指标过滤。
        与"业务宽表"区分:finance_summary 是只读快照,不算业务状态。

        Args:
            codes: 股票代码列表 (6 位不带 .SH/.SZ,空 → 空 dict)

        Returns:
            {code: {net_profit, pe_ttm, roe, ...}}, 缺失字段为 None
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
        取交易日历 (ADR-0010 重写,不再 import DataRepository)

        优先级:
          1. 子类注入的 _calendar_provider (BaoStockDatafeed 等第三方)
          2. 默认: 从 self.get_bars("000001.SH", interval="1d", ...) 推断
             (上证指数全市场存在,1d 频率天然是交易日历)

        子类可覆盖此方法 (LocalDatafeed / ParquetDatafeed 已各自实现)。
        """
        if self._calendar_provider is not None:
            return self._calendar_provider(start, end)

        # 默认: 从上证指数 1d K 线推断交易日历
        bars = self.get_bars(
            self._CALENDAR_PROBE_SYMBOL,
            interval=Interval.DAY_1,
            start=start,
            end=end,
        )
        cal: List[date] = []
        for b in bars:
            dt = b.datetime
            if isinstance(dt, datetime):
                cal.append(dt.date())
            elif isinstance(dt, date):
                cal.append(dt)
        return cal

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} inited={self.inited}>"