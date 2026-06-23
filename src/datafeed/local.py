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
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from sqlalchemy import text

from ..db.engine import get_engine
from ..gateway import BarData, ContractData
from .base import (
    BaseDatafeed,
    Interval,
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
    """SQL 片段常量"""
    SELECT_BARS = (
        "SELECT trade_date, open, high, low, close, volume, amount "
        "FROM daily_price "
        "WHERE code = :code "
        "{date_clause} "
        "ORDER BY trade_date ASC"
    )
    SELECT_STOCK_LIST = (
        "SELECT code, name, market, list_date, delist_date, industry "
        "FROM stock_basic "
        "ORDER BY code"
    )
    SELECT_DATE_BATCH = (
        "SELECT code, trade_date, open, high, low, close, volume, amount "
        "FROM daily_price "
        "WHERE trade_date = :trade_date "
        "{code_clause}"
    )


def _bar_from_row(code: str, row) -> BarData:
    """row = (trade_date, open, high, low, close, volume, amount)"""
    trade_date, o, h, l, c, vol, amount = row
    if isinstance(trade_date, str):
        trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    elif isinstance(trade_date, datetime):
        trade_date = trade_date.date()

    exchange = code_to_market(code)
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

        # 拼 date_clause
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
        else:
            params = {"code": code}
            date_clause_parts = []
            if start is not None:
                date_clause_parts.append("AND trade_date >= :start")
                params["start"] = start
            if end is not None:
                date_clause_parts.append("AND trade_date <= :end")
                params["end"] = end
            date_clause = " ".join(date_clause_parts)
            sql = _DB.SELECT_BARS.format(date_clause=date_clause)
            with self._engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()

        return [_bar_from_row(code, r) for r in rows]

    # ── 取全市场合约列表 ──────────────────────────────

    def get_stock_list(self) -> List[ContractData]:
        if not self.inited:
            self.init()
        from ..gateway import ContractData  # 局部避免循环

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
        if universe:
            codes = [vt_symbol_to_code(s) for s in universe]
            placeholders = ",".join(f":c{i}" for i in range(len(codes)))
            code_clause = f"AND code IN ({placeholders})"
            params.update({f"c{i}": c for i, c in enumerate(codes)})
        else:
            code_clause = ""

        sql = _DB.SELECT_DATE_BATCH.format(code_clause=code_clause)
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