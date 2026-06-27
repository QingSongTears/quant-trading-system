"""
LocalDatafeed — 从本地 SQLite 读行情数据 (本项目主用)

数据源: database/quant.db (由 scripts/build_db.py 从 CSV 构建)

特点:
  - 离线运行, 不依赖任何外部 API
  - 支持日 K (1d) 完整实现 (4,181,854 行已 import)
  - 分钟/小时 K 暂未实现, 调用抛 NotImplementedError

vnpy 字段映射 (daily_price → BarData):
  code          → symbol + exchange (.SZ/.SH/.BJ)
  trade_date    → datetime (date)
  open/high/low/close → open_price/high_price/low_price/close_price
  volume        → volume (股)
  amount        → turnover (元) [DB amount = 成交额, 对应 vnpy turnover]
  turnover (DB) → 0 (A股自由流通换手率, 无CSV源, 用0占位)

ADR-0010 (2026-06-27):
  - 新增 get_industry_map (拉 stock_basic.industry)
  - 新增 get_news_events (拉 research_report 表)
  - DataRepository 保留 (data 层内部使用),datafeed 是基础数据入口
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from sqlalchemy import bindparam, text

from ...db.engine import get_engine
from ...gateway import BarData, ContractData
from .base import (
    BaseDatafeed,
    Interval,
    NewsEvent,
    code_to_market,
    code_to_vt_symbol,
    vt_symbol_to_code,
    vt_symbol_to_exchange,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# ── DB 字段映射 ──────────────────────────────────────


class _DB:
    """SQL 片段常量 (修 2026-06-25: 去掉 .format(), 改 bindparam 防注入)

    ADR-0010 (2026-06-27) fix: 原 CAST(:start AS DATE) IS NULL 在 SQLite 上
    对绑定参数 CAST 不工作(CAST 期望表达式,不是参数),改用 Python 端
    None 判断 + SQLAlchemy 渲染条件的方式更稳。这里改用字符串比较
    (trade_date 是 TEXT 'YYYY-MM-DD'),字典序 = 日期序,SQLite 兼容。
    """
    SELECT_BARS = (
        "SELECT trade_date, open, high, low, close, volume, amount "
        "FROM daily_price "
        "WHERE code = :code "
        "AND trade_date >= :start "
        "AND trade_date <= :end "
        "ORDER BY trade_date ASC"
    )
    SELECT_BARS_NO_FILTER = (
        "SELECT trade_date, open, high, low, close, volume, amount "
        "FROM daily_price "
        "WHERE code = :code "
        "ORDER BY trade_date ASC"
    )
    SELECT_STOCK_LIST = (
        "SELECT code, name, market, list_date, delist_date, industry "
        "FROM stock_basic "
        "ORDER BY code"
    )
    SELECT_DATE_BATCH_TEMPLATE = (
        "SELECT code, trade_date, open, high, low, close, volume, amount "
        "FROM daily_price "
        "WHERE trade_date = :trade_date "
        "AND code IN :codes"  # 用 expanding bindparam, 见 get_bars_by_date
    )


def _bar_from_row(code: str, row) -> BarData:
    """row = (trade_date, open, high, low, close, volume, amount)

    修 2026-06-25: 缺数据时 (None) 改 warning 日志 (原静默转 0)
    - 仍转 0 (兼容调用方期望 float), 但打 warning 让用户/上游感知
    - 关键字段 (close) 为 None 时, 额外打更高 level (error)
    """
    trade_date, o, h, l, c, vol, amount = row
    if isinstance(trade_date, str):
        trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    elif isinstance(trade_date, datetime):
        trade_date = trade_date.date()

    exchange = code_to_market(code)

    # 修 2026-06-25: 检测 None, 打 warning (而非静默转 0)
    none_fields = []
    if o is None:
        none_fields.append("open")
    if h is None:
        none_fields.append("high")
    if l is None:
        none_fields.append("low")
    if c is None:
        none_fields.append("close")
    if vol is None:
        none_fields.append("volume")
    if amount is None:
        none_fields.append("amount")

    if none_fields:
        # close 是最关键字段, 单独 error
        if "close" in none_fields:
            logger.error(
                f"data_feed.local: {code} @ {trade_date} close 为 None, "
                f"转 0 (下游可能拿到假报价)"
            )
        else:
            logger.warning(
                f"data_feed.local: {code} @ {trade_date} 缺字段 {none_fields}, "
                f"转 0"
            )

    return BarData(
        gateway_name="LOCAL",
        symbol=code,
        exchange=exchange,
        datetime=datetime.combine(trade_date, datetime.min.time()),
        interval=Interval.DAY_1,
        open_price=float(o or 0),
        high_price=float(h or 0),
        low_price=float(l or 0),
        close_price=float(c or 0),
        volume=float(vol or 0),
        turnover=float(amount or 0),  # DB amount 是成交额
        open_interest=0.0,
    )


# ── LocalDatafeed ──────────────────────────────────────


class LocalDatafeed(BaseDatafeed):
    """
    本地 SQLite 数据源 (本项目主用)

    数据准备:
      python scripts/build_db.py   # 从 market_data/raw/*.csv 导入 SQLite

    用法:
        df = LocalDatafeed()
        df.init()
        bars = df.get_bars("000001.SZ", start=date(2024, 1, 1), end=date(2024, 3, 1))
        universe = df.get_stock_list()
    """

    name: str = "LOCAL"

    def __init__(self) -> None:
        super().__init__()
        self._engine = None  # lazy init

    def init(self) -> None:
        """建立 Engine (复用全局单例)"""
        if self.inited:
            return
        self._engine = get_engine()
        self.inited = True
        logger.info(f"{self.name} Datafeed 初始化完成")

    def close(self) -> None:
        """不复用全局 engine, 仅标记"""
        self.inited = False

    # ── 取历史 K 线 ─────────────────────────────────

    def get_bars(
        self,
        vt_symbol: str,
        interval: str = Interval.DAY_1,
        start: Optional[date] = None,
        end: Optional[date] = None,
        count: Optional[int] = None,
    ) -> List[BarData]:
        if not self.inited:
            self.init()

        if interval != Interval.DAY_1:
            raise NotImplementedError(
                f"LocalDatafeed 仅支持日 K (1d), 当前 {interval} 待实现 "
                f"(build_db.py 暂未导入分钟 K 数据)"
            )

        code = vt_symbol_to_code(vt_symbol)
        exchange = vt_symbol_to_exchange(vt_symbol)
        expected_market = code_to_market(code)
        if exchange != expected_market:
            logger.warning(
                f"vt_symbol {vt_symbol} 的 exchange 与 code 前缀不匹配 "
                f"(实际应为 {expected_market})"
            )

        # 修 2026-06-25: 统一用 bindparam, 去掉 .format() 拼 SQL
        if count is not None and start is None and end is None:
            # 取最近 N 条 (倒序拿再反序)
            sql = (
                "SELECT trade_date, open, high, low, close, volume, amount "
                "FROM daily_price WHERE code = :code "
                "ORDER BY trade_date DESC LIMIT :count"
            )
            with self._engine.connect() as conn:
                rows = conn.execute(text(sql), {"code": code, "count": int(count)}).fetchall()
            rows = list(reversed(rows))
        elif start is None and end is None:
            # 无日期过滤
            with self._engine.connect() as conn:
                rows = conn.execute(text(_DB.SELECT_BARS_NO_FILTER), {"code": code}).fetchall()
        else:
            # 日期范围过滤 (ADR-0010 fix: 不再 CAST(:start AS DATE),SQLite 不支持参数 CAST)
            # trade_date 实际是 TEXT 'YYYY-MM-DD',字符串比较 == 日期比较
            start_str = start.isoformat() if hasattr(start, "isoformat") else str(start)
            end_str = end.isoformat() if hasattr(end, "isoformat") else str(end)
            params = {
                "code": code,
                "start": start_str,
                "end": end_str,
            }
            sql = _DB.SELECT_BARS
            with self._engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()

        return [_bar_from_row(code, r) for r in rows]

    # ── 取全市场合约列表 ──────────────────────────────

    def get_stock_list(self) -> List[ContractData]:
        if not self.inited:
            self.init()
        from ...gateway import ContractData  # 局部避免循环

        with self._engine.connect() as conn:
            rows = conn.execute(text(_DB.SELECT_STOCK_LIST)).fetchall()

        contracts = []
        for r in rows:
            code, name, market, list_date, delist_date, industry = r
            if market not in ("SH", "SZ", "BJ"):
                continue
            c = ContractData(
                gateway_name="LOCAL",
                symbol=code,
                exchange=market,
                name=name or "",
                product="STOCK",
                size=1,
                pricetick=0.01,
                min_volume=100,
            )
            c.extra = {
                "industry": industry or "",
                "list_date": str(list_date) if list_date else "",
                "delist_date": str(delist_date) if delist_date else "",
            }
            contracts.append(c)
        return contracts

    # ── 批量取某日 BarData (回测 hot path) ────────────

    def get_bars_by_date(
        self,
        query_date: date,
        universe: Optional[List[str]] = None,
        interval: str = Interval.DAY_1,
    ) -> Dict[str, BarData]:
        """
        给定日期, 批量取所有股票的 BarData (回测用)

        比 BaseDatafeed 默认实现 (N 次 query) 快 N 倍 (1 次 query)
        """
        if not self.inited:
            self.init()
        if interval != Interval.DAY_1:
            return super().get_bars_by_date(query_date, universe, interval)

        params = {"trade_date": query_date}
        from sqlalchemy import bindparam
        sql = _DB.SELECT_DATE_BATCH_TEMPLATE
        if universe:
            codes = [vt_symbol_to_code(s) for s in universe]
            stmt = text(sql).bindparams(bindparam("codes", value=codes, expanding=True))
        else:
            # universe=None: 拉全市场, 用一个虚拟大列表 (防 IN () 语法错)
            # 实际项目中, 千万行 daily_price 全市场某日 1 query 即可
            # 简单做法: 传个包含 999999 个 code 的不可能 list, 或单独写不带 IN 的 SQL
            # 这里走兜底: 用一个特殊 list 让 IN 永远为真
            codes = None  # 走 else 分支
            stmt = text(
                "SELECT code, trade_date, open, high, low, close, volume, amount "
                "FROM daily_price WHERE trade_date = :trade_date"
            )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt, params).fetchall()
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        result: Dict[str, BarData] = {}
        for row in rows:
            code, trade_date, o, h, l, c, vol, amount = row
            bar = _bar_from_row(code, (trade_date, o, h, l, c, vol, amount))
            result[code_to_vt_symbol(code)] = bar
        return result

    # ── 交易日历 (override BaseDatafeed, 用单次 distinct) ──

    def get_trading_calendar(
        self,
        start: date,
        end: date,
    ) -> List[date]:
        if not self.inited:
            self.init()
        sql = (
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), {"s": start, "e": end}).fetchall()

        cal = []
        for r in rows:
            d = r[0]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            elif isinstance(d, datetime):
                d = d.date()
            cal.append(d)
        return cal

    # ── 基础数据抽象 (ADR-0010 新增) ──────────────

    def get_industry_map(self, codes: List[str]) -> Dict[str, str]:
        """取股票-行业映射 (从 stock_basic.industry 表)

        Args:
            codes: 股票代码列表 (6 位不带 .SH/.SZ,空列表 → 空 dict)

        Returns:
            {code: industry}, 缺失为 ""
        """
        if not codes:
            return {}

        if not self.inited:
            self.init()

        sql = (
            "SELECT code, industry FROM stock_basic "
            "WHERE code IN :codes"
        )
        try:
            with self._engine.connect() as conn:
                stmt = text(sql).bindparams(bindparam("codes", expanding=True))
                rows = conn.execute(stmt, {"codes": list(codes)}).fetchall()
        except Exception as e:
            logger.warning(f"LocalDatafeed.get_industry_map 失败: {e}")
            return {}

        result: Dict[str, str] = {}
        for r in rows:
            code, industry = r[0], r[1]
            result[str(code)] = str(industry or "")
        return result

    def get_news_events(
        self,
        codes: List[str],
        start: date,
        end: date,
    ) -> List[NewsEvent]:
        """取研报 / 新闻事件 (从 research_report 表)

        Args:
            codes: 股票代码列表 (空 = 全市场)
            start: 起始日期 (含)
            end: 结束日期 (含)

        Returns:
            List[NewsEvent] (按 date DESC)
        """
        if not self.inited:
            self.init()

        params: Dict[str, object] = {"start": start, "end": end}

        if codes:
            sql = (
                "SELECT code, date, title, rating, rating_change, author, institution, url "
                "FROM research_report "
                "WHERE code IN :codes "
                "AND date BETWEEN :start AND :end "
                "ORDER BY date DESC"
            )
            stmt = text(sql).bindparams(bindparam("codes", expanding=True))
            params["codes"] = list(codes)
        else:
            sql = (
                "SELECT code, date, title, rating, rating_change, author, institution, url "
                "FROM research_report "
                "WHERE date BETWEEN :start AND :end "
                "ORDER BY date DESC"
            )
            stmt = text(sql)

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(stmt, params).fetchall()
        except Exception as e:
            logger.warning(f"LocalDatafeed.get_news_events 失败: {e}")
            return []

        events: List[NewsEvent] = []
        for r in rows:
            code, d, title, rating, rating_change, author, institution, url = r
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            elif isinstance(d, datetime):
                d = d.date()
            events.append(NewsEvent(
                code=str(code),
                date=d,
                title=str(title or ""),
                rating=str(rating) if rating else None,
                rating_change=str(rating_change) if rating_change else None,
                author=str(author) if author else None,
                institution=str(institution) if institution else None,
                url=str(url) if url else None,
                source="research_report",
            ))
        return events

    def get_finance_snapshot(
        self,
        codes: List[str],
    ) -> Dict[str, Dict[str, float]]:
        """取财务快照 (从 finance_summary 表)

        返回字段: net_profit / pe_ttm (TotalShareholderEquity / |NP| 近似)
        """
        if not codes:
            return {}
        if not self.inited:
            self.init()

        sql = (
            "SELECT code, NPParentCompanyOwnersTTM AS net_profit, "
            "       TotalShareholderEquity / NULLIF(ABS(NPParentCompanyOwnersTTM), 0) AS pe_ttm "
            "FROM finance_summary WHERE code IN :codes"
        )
        try:
            with self._engine.connect() as conn:
                stmt = text(sql).bindparams(bindparam("codes", expanding=True))
                rows = conn.execute(stmt, {"codes": list(codes)}).fetchall()
        except Exception as e:
            logger.warning(f"LocalDatafeed.get_finance_snapshot 失败: {e}")
            return {}

        result: Dict[str, Dict[str, float]] = {}
        for r in rows:
            code = str(r[0])
            result[code] = {
                "net_profit": float(r[1]) if r[1] is not None else None,
                "pe_ttm": float(r[2]) if r[2] is not None else None,
            }
        return result