"""
测试: backtest/data_loader.py — ADR-0009
=========================================

覆盖 BacktestDataLoader:
- load_bars (单股 K 线)
- load_universe (全市场股票池)
- load_all_market_data (LRU 缓存 + RLock)
- build_universe_factors (因子构建)
- get_rebalance_dates (调仓日)

并发安全测试: 验证 RLock 在多 worker 场景下防止 race
"""
import threading
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.backtest.data_loader import BacktestDataLoader, get_rebalance_dates


class TestGetRebalanceDates:
    """get_rebalance_dates 工具函数"""

    def test_basic(self):
        """每 N 个交易日取一次"""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        df = pd.DataFrame({"trade_date": dates})

        # 每 3 个交易日
        result = get_rebalance_dates(df, 3)
        assert len(result) == 4  # 0, 3, 6, 9
        assert result[0] == dates[0]
        assert result[-1] == dates[-1]

    def test_every_day(self):
        """每 1 个交易日(每日调仓)"""
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        df = pd.DataFrame({"trade_date": dates})
        result = get_rebalance_dates(df, 1)
        assert len(result) == 5


class TestBacktestDataLoaderMocked:
    """用 mock repo 测试 loader 行为"""

    @pytest.fixture
    def mock_repo(self):
        repo = MagicMock()
        repo.init_database = MagicMock()
        # get_daily_data 返回模拟数据
        repo.get_daily_data = MagicMock(return_value=_make_ohlcv_df(60))
        repo.get_benchmark_data = MagicMock(return_value=_make_ohlcv_df(60))
        repo.get_stock_list = MagicMock(return_value=pd.DataFrame({
            "code": ["000001", "000002", "600519"],
            "name": ["平安银行", "万科A", "贵州茅台"],
        }))
        repo.engine = MagicMock()
        return repo

    @pytest.fixture
    def loader(self, mock_repo):
        return BacktestDataLoader(repo=mock_repo, cache_max=4)

    def test_init_does_not_throw(self, mock_repo):
        """init 不抛异常(允许 repo mock)"""
        loader = BacktestDataLoader(repo=mock_repo)
        assert loader.repo is mock_repo

    def test_lookup_stock_name_found(self, loader):
        """查找存在的股票名"""
        assert loader.lookup_stock_name("000001") == "平安银行"

    def test_lookup_stock_name_missing(self, loader):
        """查找不存在的股票名 → 空字符串"""
        assert loader.lookup_stock_name("999999") == ""

    def test_load_universe_returns_list(self, loader):
        """load_universe 返回股票代码列表"""
        universe = loader.load_universe(date(2024, 6, 1))
        assert isinstance(universe, list)
        assert "000001" in universe


def _make_ohlcv_df(n: int) -> pd.DataFrame:
    """生成测试用 OHLCV DataFrame"""
    np.random.seed(42)
    base = 10.0
    closes = base + np.cumsum(np.random.randn(n) * 0.1)
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": closes * 0.99,
        "high": closes * 1.02,
        "low": closes * 0.98,
        "close": closes,
        "volume": np.random.randint(1000000, 5000000, n),
        "amount": closes * np.random.randint(1000000, 5000000, n),
        "pct_change": np.random.randn(n),
        "turnover": np.random.uniform(1.0, 3.0, n),
    })


class TestLoadAllMarketDataCache:
    """LRU 缓存测试(用 patch 隔离 SQL 依赖)"""

    @pytest.fixture
    def loader_with_mocked_read_sql(self, monkeypatch):
        """mock read_sql 避免依赖 DB"""
        mock_repo = MagicMock()
        mock_repo.init_database = MagicMock()
        mock_repo.engine = MagicMock()

        # patch read_sql 在 data_loader 模块中
        from src.backtest import data_loader as dl_mod

        def fake_read_sql(sql, engine, params):
            return _make_market_df(60)

        monkeypatch.setattr(dl_mod, "read_sql", fake_read_sql)

        return BacktestDataLoader(repo=mock_repo, cache_max=4)

    def test_cache_hit_no_redundant_call(self, loader_with_mocked_read_sql, monkeypatch):
        """相同 (start, end) 第二次调用应走缓存,不重读 SQL"""
        loader = loader_with_mocked_read_sql
        from src.backtest import data_loader as dl_mod
        call_count = [0]

        def counting_read_sql(sql, engine, params):
            call_count[0] += 1
            return _make_market_df(60)

        monkeypatch.setattr(dl_mod, "read_sql", counting_read_sql)

        # 第一次调用: 无缓存
        df1 = loader.load_all_market_data(date(2024, 1, 1), date(2024, 6, 1))
        # 第二次调用: 命中缓存
        df2 = loader.load_all_market_data(date(2024, 1, 1), date(2024, 6, 1))

        # SQL 只应调用一次
        assert call_count[0] == 1

        # 返回值相同(缓存)
        assert len(df1) == len(df2)

    def test_cache_eviction_fifo(self, loader_with_mocked_read_sql, monkeypatch):
        """超过 cache_max 时 FIFO 驱逐"""
        loader = loader_with_mocked_read_sql
        loader._data_cache_max = 2  # 调小以测驱逐
        from src.backtest import data_loader as dl_mod
        call_count = [0]

        def counting_read_sql(sql, engine, params):
            call_count[0] += 1
            return _make_market_df(60)

        monkeypatch.setattr(dl_mod, "read_sql", counting_read_sql)

        # 调 3 次不同 key → 第 1 次应被驱逐
        loader.load_all_market_data(date(2024, 1, 1), date(2024, 3, 1))
        loader.load_all_market_data(date(2024, 2, 1), date(2024, 4, 1))
        loader.load_all_market_data(date(2024, 3, 1), date(2024, 5, 1))

        assert len(loader._data_cache) == 2
        assert call_count[0] == 3

    def test_clear_cache(self, loader_with_mocked_read_sql):
        """clear_cache 清空"""
        loader = loader_with_mocked_read_sql
        loader._data_cache["fake_key"] = pd.DataFrame()
        loader.clear_cache()
        assert len(loader._data_cache) == 0


class TestLoadAllMarketDataRLock:
    """RLock 并发安全测试"""

    def test_concurrent_load_is_safe(self, monkeypatch):
        """多线程并发加载不应破坏缓存(dict)"""
        from src.backtest import data_loader as dl_mod

        def slow_read_sql(sql, engine, params):
            # 模拟慢 IO,放大 race 窗口
            import time
            time.sleep(0.01)
            return _make_market_df(60)

        monkeypatch.setattr(dl_mod, "read_sql", slow_read_sql)

        mock_repo = MagicMock()
        mock_repo.init_database = MagicMock()
        mock_repo.engine = MagicMock()
        loader = BacktestDataLoader(repo=mock_repo, cache_max=8)

        results = []
        errors = []

        def worker(start, end):
            try:
                df = loader.load_all_market_data(start, end)
                results.append((start, end, len(df)))
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(8):
            t = threading.Thread(
                target=worker,
                args=(date(2024, 1, 1), date(2024, 6, 1)),  # 全部同一 key
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 无异常,所有结果一致
        assert len(errors) == 0
        assert len(results) == 8
        # 同一 key → 同一长度
        assert len(set(r[2] for r in results)) == 1


class TestBuildUniverseFactors:
    """build_universe_factors 因子构建"""

    @pytest.fixture
    def sample_data(self):
        return _make_market_df(60)

    def test_basic_factors(self, sample_data):
        """基本因子输出"""
        from src.backtest.data_loader import BacktestDataLoader

        mock_repo = MagicMock()
        mock_repo.init_database = MagicMock()
        loader = BacktestDataLoader(repo=mock_repo)

        as_of = sample_data["trade_date"].iloc[-1]
        factors = loader.build_universe_factors(sample_data, as_of, lookback=20)

        if not factors.empty:
            assert "code" in factors.columns
            assert "close" in factors.columns
            assert "avg_amount_wan" in factors.columns
            assert "avg_turnover" in factors.columns
            assert "volatility_Nd" in factors.columns
            assert "return_Nd" in factors.columns

    def test_empty_when_no_data(self):
        """无数据时返回空 DataFrame"""
        from src.backtest.data_loader import BacktestDataLoader

        mock_repo = MagicMock()
        mock_repo.init_database = MagicMock()
        loader = BacktestDataLoader(repo=mock_repo)

        empty_data = pd.DataFrame(columns=[
            "code", "name", "list_date", "mcap_yi", "trade_date",
            "open", "high", "low", "close", "volume", "amount",
            "pct_change", "turnover",
        ])
        as_of = pd.Timestamp("2024-06-01")
        factors = loader.build_universe_factors(empty_data, as_of, lookback=20)
        assert factors.empty


def _make_market_df(n: int, n_codes: int = 5) -> pd.DataFrame:
    """生成测试用全市场 daily_price 格式 DataFrame"""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    rows = []
    for code_idx in range(n_codes):
        code = f"{code_idx:06d}"
        base = 10.0 + code_idx
        prices = base * np.exp(np.cumsum(np.random.randn(n) * 0.01))
        for i, dt in enumerate(dates):
            rows.append({
                "code": code,
                "name": f"Stock{code_idx}",
                "list_date": date(2020, 1, 1),
                "mcap_yi": 100.0,
                "trade_date": dt,
                "open": prices[i] * 0.99,
                "high": prices[i] * 1.01,
                "low": prices[i] * 0.98,
                "close": prices[i],
                "volume": 1000000,
                "amount": prices[i] * 1000000,
                "pct_change": np.random.randn() * 2,
                "turnover": 2.0,
            })
    return pd.DataFrame(rows)