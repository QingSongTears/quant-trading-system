"""
BaseDatafeed 抽象基类单测 — src/data/datafeed/base.py (2026-06-24)

涵盖:
  - 抽象方法 (get_bars / get_stock_list) 不可直接实例化
  - Interval 常量
  - code_to_market / code_to_vt_symbol / vt_symbol_to_code / vt_symbol_to_exchange
  - get_bars_by_date 默认实现 (子类可覆盖)
  - __repr__
"""
import sys
from datetime import date
from pathlib import Path

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.datafeed import BaseDatafeed, Interval
from src.data.datafeed.base import (
    code_to_market,
    code_to_vt_symbol,
    vt_symbol_to_code,
    vt_symbol_to_exchange,
)


# ── 抽象类不能直接实例化 ──────────────────────────


def test_base_datafeed_cannot_be_instantiated():
    """BaseDatafeed 是 ABC, 直接实例化抛 TypeError"""
    with pytest.raises(TypeError, match="abstract"):
        BaseDatafeed()


# ── Interval 常量 ──────────────────────────


def test_interval_constants():
    assert Interval.MINUTE_1 == "1m"
    assert Interval.MINUTE_5 == "5m"
    assert Interval.MINUTE_15 == "15m"
    assert Interval.MINUTE_30 == "30m"
    assert Interval.HOUR_1 == "1h"
    assert Interval.DAY_1 == "1d"
    assert Interval.WEEK_1 == "1w"
    assert Interval.MONTH_1 == "1M"


def test_interval_supported_tuple():
    assert "1d" in Interval.SUPPORTED
    assert "1m" in Interval.SUPPORTED
    assert "1M" in Interval.SUPPORTED


# ── code <-> market 转换 ──────────────────────────


def test_code_to_market_sh():
    assert code_to_market("600519") == "SH"
    assert code_to_market("688981") == "SH"  # 科创板


def test_code_to_market_sz():
    assert code_to_market("000001") == "SZ"
    assert code_to_market("300750") == "SZ"  # 创业板


def test_code_to_market_bj():
    assert code_to_market("830799") == "BJ"
    assert code_to_market("430047") == "BJ"


def test_code_to_market_other():
    """非 6/0/3/4/8 开头归 OTHER"""
    assert code_to_market("999999") == "OTHER"
    assert code_to_market("") == "OTHER"


def test_code_to_vt_symbol():
    assert code_to_vt_symbol("000001") == "000001.SZ"
    assert code_to_vt_symbol("600519") == "600519.SH"
    assert code_to_vt_symbol("830799") == "830799.BJ"


def test_vt_symbol_to_code():
    assert vt_symbol_to_code("000001.SZ") == "000001"
    assert vt_symbol_to_code("600519.SH") == "600519"


def test_vt_symbol_to_exchange():
    assert vt_symbol_to_exchange("000001.SZ") == "SZ"
    assert vt_symbol_to_exchange("600519.SH") == "SH"


# ── 最小可继承的 Mock 子类 ──────────────────────────


class _MockDatafeed(BaseDatafeed):
    """满足 ABC 的最小实现 (ADR-0010 新增 get_industry_map / get_news_events)"""
    name = "MOCK"

    def __init__(self, fixed_bars=None, fixed_stocks=None):
        super().__init__()
        self._fixed_bars = fixed_bars or []
        self._fixed_stocks = fixed_stocks or []

    def get_bars(self, vt_symbol, interval="1d", start=None, end=None, count=None):
        return self._fixed_bars

    def get_stock_list(self):
        return self._fixed_stocks

    def get_industry_map(self, codes):
        return {c: "未知" for c in codes}

    def get_news_events(self, codes, start, end):
        return []

    def get_finance_snapshot(self, codes):
        return {}


def test_concrete_subclass_can_be_instantiated():
    df = _MockDatafeed()
    assert df.inited is False
    assert df.name == "MOCK"


def test_init_marks_inited():
    df = _MockDatafeed()
    df.init()
    assert df.inited is True


def test_close_marks_uninited():
    df = _MockDatafeed()
    df.init()
    df.close()
    assert df.inited is False


def test_repr_includes_class_name_and_inited():
    df = _MockDatafeed()
    r = repr(df)
    assert "_MockDatafeed" in r
    assert "MOCK" in r
    assert "inited=False" in r


# ── get_bars_by_date 默认实现 ──────────────────────────


def test_get_bars_by_date_uses_universe():
    """universe 给定时, 只取这些"""
    from src.gateway import BarData
    bar = BarData(symbol="000001", exchange="SZ", datetime=date(2024, 1, 1))
    df = _MockDatafeed(fixed_bars=[bar])
    result = df.get_bars_by_date(date(2024, 1, 1), universe=["000001.SZ"])
    assert "000001.SZ" in result
    assert result["000001.SZ"] is bar


def test_get_bars_by_date_uses_get_stock_list_when_universe_none():
    """universe=None 时, 从 get_stock_list() 拉"""
    from src.gateway import ContractData, BarData
    contract = ContractData(symbol="000001", exchange="SZ")
    bar = BarData(symbol="000001", exchange="SZ", datetime=date(2024, 1, 1))
    df = _MockDatafeed(fixed_bars=[bar], fixed_stocks=[contract])
    result = df.get_bars_by_date(date(2024, 1, 1))
    assert "000001.SZ" in result
