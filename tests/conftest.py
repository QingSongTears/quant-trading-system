"""
pytest 共享 fixtures — 数据库、测试数据、回测引擎
"""
import sys
import tempfile
import os
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Generator

import pytest
import pandas as pd
import numpy as np

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.models.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


# ============================================================
# 数据库 fixtures
# ============================================================

@pytest.fixture
def db_path(tmp_path) -> Path:
    """临时数据库文件路径"""
    return tmp_path / "test_quant.db"


@pytest.fixture
def db_engine(db_path):
    """SQLAlchemy 内存引擎，指向临时文件"""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    """数据库会话（自动回滚）"""
    session = Session(db_engine)
    yield session
    session.rollback()
    session.close()


# ============================================================
# 测试数据 fixtures
# ============================================================

@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """生成 250 个交易日的模拟 OHLCV 数据（含趋势+震荡）"""
    np.random.seed(42)
    n = 250
    dates = pd.date_range(end=date.today(), periods=n, freq="B")

    # 模拟价格：带趋势的随机游走
    base = 10.0
    trend = np.linspace(0, 5, n)  # 整体上涨
    noise = np.random.randn(n).cumsum() * 0.5
    close_prices = base + trend + noise
    close_prices = np.maximum(close_prices, 1.0)  # 不低于 1 元

    # 构造 OHLCV
    daily_volatility = close_prices * 0.02
    high = close_prices + np.abs(np.random.randn(n) * daily_volatility)
    low = close_prices - np.abs(np.random.randn(n) * daily_volatility)
    open_prices = close_prices + np.random.randn(n) * daily_volatility * 0.5

    # 确保 high >= max(open, close) 且 low <= min(open, close)
    for i in range(n):
        high[i] = max(high[i], open_prices[i], close_prices[i])
        low[i] = min(low[i], open_prices[i], close_prices[i])

    volume = np.random.randint(1000000, 50000000, n)

    df = pd.DataFrame({
        "trade_date": dates,
        "open": np.round(open_prices, 2),
        "high": np.round(high, 2),
        "low": np.round(low, 2),
        "close": np.round(close_prices, 2),
        "volume": volume,
        "amount": np.round(close_prices * volume, 0),
        "pct_change": np.round(np.diff(np.append([close_prices[0] * 0.99], close_prices)) / close_prices * 100, 2),
        "turnover": np.random.uniform(0.5, 5.0, n),
    })
    return df


@pytest.fixture
def sample_stock_list() -> pd.DataFrame:
    """模拟 A 股股票列表"""
    stocks = [
        {"code": "000001", "name": "平安银行", "market": "SZ"},
        {"code": "000002", "name": "万科A", "market": "SZ"},
        {"code": "600519", "name": "贵州茅台", "market": "SH"},
        {"code": "600036", "name": "招商银行", "market": "SH"},
        {"code": "300750", "name": "宁德时代", "market": "SZ"},
        {"code": "000858", "name": "五粮液", "market": "SZ"},
        {"code": "601318", "name": "中国平安", "market": "SH"},
        {"code": "600900", "name": "长江电力", "market": "SH"},
        {"code": "002415", "name": "海康威视", "market": "SZ"},
        {"code": "000333", "name": "美的集团", "market": "SZ"},
    ]
    return pd.DataFrame(stocks)


@pytest.fixture
def sample_quotes_data() -> pd.DataFrame:
    """模拟全市场行情数据（tencent_quotes.csv 格式）"""
    np.random.seed(123)
    n = 100
    codes = [f"{c:06d}" for c in range(1, n + 1)]
    names = [f"测试股{i}" for i in range(1, n + 1)]

    return pd.DataFrame({
        "code": codes,
        "name": names,
        "price": np.round(np.random.uniform(5, 200, n), 2),
        "last_close": np.round(np.random.uniform(5, 200, n), 2),
        "open": np.round(np.random.uniform(5, 200, n), 2),
        "high": np.round(np.random.uniform(5, 200, n), 2),
        "low": np.round(np.random.uniform(5, 200, n), 2),
        "amount_wan": np.round(np.random.uniform(100, 50000, n), 2),
        "turnover_pct": np.round(np.random.uniform(0.1, 15, n), 2),
        "pe_ttm": np.round(np.random.uniform(5, 100, n), 2),
        "amplitude_pct": np.round(np.random.uniform(1, 10, n), 2),
        "mcap_yi": np.round(np.random.uniform(50, 2000, n), 2),
        "float_mcap_yi": np.round(np.random.uniform(30, 1500, n), 2),
        "pb": np.round(np.random.uniform(0.5, 10, n), 2),
        "vol_ratio": np.round(np.random.uniform(0.5, 3, n), 2),
        "pe_static": np.round(np.random.uniform(5, 100, n), 2),
    })


@pytest.fixture
def sample_kline_data() -> pd.DataFrame:
    """模拟日K线数据（kline_daily.csv 格式）"""
    np.random.seed(456)
    codes = ["000001", "000002", "600519", "600036", "300750"]
    rows = []
    for code in codes:
        n = 250
        dates = pd.date_range(end=date.today(), periods=n, freq="B")
        base = np.random.uniform(8, 200)
        trend = np.linspace(0, np.random.uniform(-2, 8), n)
        noise = np.random.randn(n).cumsum() * 0.3
        close = np.maximum(base + trend + noise, 1)
        for i, d in enumerate(dates):
            rows.append({
                "code": code,
                "market": "SH" if code.startswith("6") else "SZ",
                "name": f"测试_{code}",
                "date": d,
                "open": round(close[i] * np.random.uniform(0.98, 1.02), 2),
                "high": round(close[i] * np.random.uniform(1.00, 1.04), 2),
                "low": round(close[i] * np.random.uniform(0.96, 1.00), 2),
                "close": round(close[i], 2),
                "volume": float(np.random.randint(1000000, 50000000)),
                "amount": float(np.random.randint(5000000, 500000000)),
            })
    return pd.DataFrame(rows)


# ============================================================
# stock_screener 测试数据目录 fixtures
# ============================================================

@pytest.fixture
def screener_data_dir(tmp_path, sample_kline_data, sample_quotes_data):
    """为 stock_screener 模块创建临时数据目录"""
    data_dir = tmp_path / "screener_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 写入测试数据文件
    sample_kline_data.to_csv(data_dir / "kline_daily.csv", index=False, header=False)
    sample_quotes_data.to_csv(data_dir / "tencent_quotes.csv", index=False)

    return data_dir


# ============================================================
# Faster tests: 跳过需要网络/大型数据的标记
# ============================================================

def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 慢速测试（需网络或大数据）")
    config.addinivalue_line("markers", "network: 需要网络连接")
    config.addinivalue_line("markers", "integration: 集成测试")
