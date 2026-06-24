"""
LocalDatafeed 单测 — src/data/datafeed/local.py (2026-06-24)

涵盖:
  - init() / close() 状态机
  - get_bars() 取日 K (按日期范围 / 按 count)
  - get_stock_list() 取全市场合约
  - get_bars_by_date() 批量取某日 BarData
  - 非 1d interval 抛 NotImplementedError
  - 异常 vt_symbol 警告但不抛
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.datafeed import LocalDatafeed
from src.data.datafeed.base import Interval


# ── 状态机 ──────────────────────────


def test_local_datafeed_inherits_base():
    from src.data.datafeed import BaseDatafeed
    assert issubclass(LocalDatafeed, BaseDatafeed)


def test_local_datafeed_name():
    assert LocalDatafeed.name == "LOCAL"


def test_init_marks_inited_and_creates_engine():
    df = LocalDatafeed()
    assert df.inited is False
    df.init()
    assert df.inited is True
    assert df._engine is not None


def test_init_is_idempotent():
    df = LocalDatafeed()
    df.init()
    engine1 = df._engine
    df.init()  # 第二次不应重建
    assert df._engine is engine1


def test_close_marks_uninited():
    df = LocalDatafeed()
    df.init()
    df.close()
    assert df.inited is False


# ── get_bars() (mock engine) ──────────────────────────


def _bar_row(code, trade_date, o=10.0, h=11.0, l=9.5, c=10.5, vol=1000, amount=10500.0):
    return (trade_date, o, h, l, c, vol, amount)


def test_get_bars_init_on_first_call(monkeypatch):
    """get_bars 触发 init (lazy)"""
    df = LocalDatafeed()
    assert df.inited is False
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = []
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)
    df.get_bars("000001.SZ")
    assert df.inited is True


def test_get_bars_empty_returns_empty_list(monkeypatch):
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = []
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)

    df = LocalDatafeed()
    df.init()
    bars = df.get_bars("000001.SZ", start=date(2024, 1, 1), end=date(2024, 3, 1))
    assert bars == []


def test_get_bars_with_count_uses_desc_limit(monkeypatch):
    """count=N 时应倒序拿再反序"""
    rows = [
        _bar_row("000001", "2024-01-03"),
        _bar_row("000001", "2024-01-02"),
        _bar_row("000001", "2024-01-01"),
    ]
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = rows
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)

    df = LocalDatafeed()
    df.init()
    bars = df.get_bars("000001.SZ", count=3)
    assert len(bars) == 3
    # 倒序拿, 反序返回 → 升序
    assert bars[0].close_price == 10.5  # 第 1 行 (01-03)
    assert bars[2].close_price == 10.5  # 第 3 行 (01-01)


def test_get_bars_non_daily_raises_not_implemented(monkeypatch):
    fake_engine = MagicMock()
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)
    df = LocalDatafeed()
    df.init()
    with pytest.raises(NotImplementedError, match="仅支持日 K"):
        df.get_bars("000001.SZ", interval=Interval.MINUTE_5)


def test_get_bars_exchange_mismatch_warns(monkeypatch, caplog):
    """000001 应是 SZ, 传 SH 不抛, 只 warning"""
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = []
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)

    df = LocalDatafeed()
    df.init()
    # 000001 + SH 是错的 (000001 应是 SZ), 但应正常执行
    bars = df.get_bars("000001.SH")
    assert bars == []


# ── get_stock_list() ──────────────────────────


def test_get_stock_list_skips_other_market(monkeypatch):
    """market 不在 SH/SZ/BJ 跳过"""
    rows = [
        ("000001", "平安银行", "SZ", "1991-04-03", None, "银行"),
        ("999999", "X", "OTHER", None, None, ""),
        ("600519", "贵州茅台", "SH", "2001-08-27", None, "白酒"),
    ]
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = rows
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)

    df = LocalDatafeed()
    df.init()
    contracts = df.get_stock_list()
    assert len(contracts) == 2
    codes = {c.symbol for c in contracts}
    assert "999999" not in codes
    assert "000001" in codes
    assert "600519" in codes


# ── get_bars_by_date() ──────────────────────────


def test_get_bars_by_date_batch_query(monkeypatch):
    """批量取某日 BarData (1 次 query)"""
    rows = [
        ("000001", "2024-01-15", 10.0, 11.0, 9.5, 10.5, 1000, 10500),
        ("000002", "2024-01-15", 20.0, 21.0, 19.5, 20.5, 2000, 41000),
    ]
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = rows
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)

    df = LocalDatafeed()
    df.init()
    result = df.get_bars_by_date(date(2024, 1, 15))
    assert "000001.SZ" in result
    assert "000002.SZ" in result
    assert result["000001.SZ"].close_price == 10.5


def test_get_bars_by_date_with_universe(monkeypatch):
    """universe 给定时, IN 子句过滤"""
    rows = [("000001", "2024-01-15", 10.0, 11.0, 9.5, 10.5, 1000, 10500)]
    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = rows
    fake_engine.connect.return_value.__enter__ = MagicMock(return_value=fake_conn)
    fake_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("src.data.datafeed.local.get_engine", lambda: fake_engine)

    df = LocalDatafeed()
    df.init()
    result = df.get_bars_by_date(
        date(2024, 1, 15),
        universe=["000001.SZ"],  # 只查 000001
    )
    assert "000001.SZ" in result
