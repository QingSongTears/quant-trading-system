"""build_db._schema — SQLite DDL

按表分块, 每个表 CREATE TABLE IF NOT EXISTS + 索引.
集中管理, 避免每个 importer 重复声明.
"""
from __future__ import annotations


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    list_date DATE,
    delist_date DATE,
    industry TEXT,
    sector TEXT,
    listed_date_alt DATE,
    issue_price REAL,
    reg_capital REAL,
    establish_date DATE,
    chairman TEXT,
    website TEXT,
    business TEXT,
    reg_address TEXT
);

CREATE TABLE IF NOT EXISTS daily_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL,
    pct_change REAL,
    turnover REAL,
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_dp_code_date ON daily_price(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_price(trade_date);
CREATE INDEX IF NOT EXISTS idx_dp_date_code ON daily_price(trade_date, code);

CREATE TABLE IF NOT EXISTS benchmark_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    close REAL NOT NULL,
    pct_change REAL,
    UNIQUE(index_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_bd_code_date ON benchmark_data(index_code, trade_date);

CREATE TABLE IF NOT EXISTS fund_flow_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    main_net REAL,
    super_large_net REAL,
    large_net REAL,
    medium_net REAL,
    small_net REAL,
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ff_code_date ON fund_flow_data(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_ff_date ON fund_flow_data(trade_date);

CREATE TABLE IF NOT EXISTS technical_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    macd_dif REAL, macd_dea REAL, macd_hist REAL,
    rsi14 REAL,
    kdj_k REAL, kdj_d REAL, kdj_j REAL,
    boll_mid REAL, boll_upper REAL, boll_lower REAL,
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ti_code_date ON technical_indicators(code, trade_date);

CREATE TABLE IF NOT EXISTS finance_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    _date DATE,
    ROE REAL, ROETTM REAL, EPS REAL, NAPS REAL,
    OperatingRevenue REAL, NPParentCompanyOwnersTTM REAL,
    TotalShareholderEquity REAL, DebtAssetsRatio REAL,
    -- 其他字段按需扩展
    UNIQUE(code, _date)
);
CREATE INDEX IF NOT EXISTS idx_fs_code ON finance_summary(code);
CREATE INDEX IF NOT EXISTS idx_fs_code_date ON finance_summary(code, _date);
"""
