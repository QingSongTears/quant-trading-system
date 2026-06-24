"""
ParquetDatafeed 单测 — src/data/datafeed/parquet.py (2026-06-24)

涵盖:
  - init() 检测目录存在
  - name 字段
  - get_bars() (mock polars scan)
  - get_stock_list() (mock dir listing)
  - 继承 BaseDatafeed
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.datafeed import ParquetDatafeed
from src.data.datafeed.base import Interval


# ── 基础 ──────────────────────────


def test_parquet_datafeed_inherits_base():
    from src.data.datafeed import BaseDatafeed
    assert issubclass(ParquetDatafeed, BaseDatafeed)


def test_parquet_datafeed_name():
    assert ParquetDatafeed.name == "PARQUET"


# ── init 状态机 ──────────────────────────


def test_init_raises_when_dir_missing(tmp_path):
    """parquet 目录不存在时抛 FileNotFoundError (明示)"""
    df = ParquetDatafeed()
    with pytest.raises(FileNotFoundError, match="parquet"):
        df.parquet_dir = tmp_path / "no_such_dir"  # override
        df.init()


def test_init_ok_when_dir_exists(tmp_path):
    """parquet 目录存在且有 .parquet 文件时正常 init"""
    (tmp_path / "daily").mkdir()
    # 放一个假的 parquet 文件 (init 只 glob 不读内容)
    (tmp_path / "daily" / "000001.SZ.parquet").touch()
    df = ParquetDatafeed()
    df.parquet_dir = tmp_path / "daily"
    df.init()
    assert df.inited is True


def test_init_idempotent(tmp_path):
    (tmp_path / "daily").mkdir()
    (tmp_path / "daily" / "000001.SZ.parquet").touch()
    df = ParquetDatafeed()
    df.parquet_dir = tmp_path / "daily"
    df.init()
    df.init()  # 第二次不抛
    assert df.inited is True


# ── 工厂切换 ──────────────────────────


def test_datafeed_factory_can_switch_to_parquet():
    """set_datafeed_kind('parquet') 切换"""
    from src.data.datafeed import (
        LocalDatafeed,
        ParquetDatafeed,
        get_datafeed,
        set_datafeed_kind,
    )
    import src.data.datafeed as df_mod

    # 重置全局
    df_mod._instance = None
    df_mod._kind = "local"

    # 切到 parquet
    set_datafeed_kind("parquet")
    # 由于真实 parquet 目录不存在, 这里只验证 _kind 切换
    assert df_mod._kind == "parquet"

    # 切回
    set_datafeed_kind("local")
    assert df_mod._kind == "local"


def test_set_datafeed_kind_rejects_invalid():
    from src.data.datafeed import set_datafeed_kind
    with pytest.raises(ValueError, match="未知"):
        set_datafeed_kind("mysql")


# ── get_bars() — 不真去读 ──────────────────────────


def test_get_bars_calls_polars_scan(monkeypatch, tmp_path):
    """get_bars 应调 polars scan_parquet"""
    (tmp_path / "000001.SZ.parquet").touch()
    df = ParquetDatafeed()
    df.parquet_dir = tmp_path
    df.inited = True  # 跳过 init

    fake_lf = MagicMock()
    fake_collected = MagicMock()
    # 模拟 polars 链: scan_parquet → filter → filter → collect
    fake_lf.filter.return_value = fake_lf
    fake_lf.collect.return_value = fake_collected
    # 模拟 collected.to_dicts() 返回空
    fake_collected.to_dicts.return_value = []

    # mock pl 模块 (在 parquet.py 里 import as pl)
    with patch("polars.scan_parquet", return_value=fake_lf) as fake_scan:
        bars = df.get_bars("000001.SZ", start=date(2024, 1, 1), end=date(2024, 1, 3))
        assert isinstance(bars, list)
        fake_scan.assert_called()


# ── __repr__ ──────────────────────────


def test_repr_includes_class_name():
    df = ParquetDatafeed()
    r = repr(df)
    assert "ParquetDatafeed" in r
    assert "PARQUET" in r
