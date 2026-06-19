"""
SQLAlchemy 数据模型定义
所有表结构在此定义，通过 alembic 或 create_all 创建
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Float,
    BigInteger, Text, UniqueConstraint, Index, ForeignKey,
    create_engine
)
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column


class Base(DeclarativeBase):
    """模型基类"""
    pass


class StockBasic(Base):
    """
    股票基本信息表
    数据来源: AKShare stock_info_a_code_name() → 东方财富/交易所公开数据
    """
    __tablename__ = "stock_basic"

    code: Mapped[str] = mapped_column(String(10), primary_key=True, comment="股票代码")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="股票名称")
    market: Mapped[str] = mapped_column(String(2), nullable=False, comment="市场: SH/SZ/BJ")
    list_date: Mapped[Optional[date]] = mapped_column(Date, comment="上市日期")
    delist_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="退市日期")
    industry: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="所属行业")

    # 关系
    daily_prices = relationship("DailyPrice", back_populates="stock", lazy="dynamic")

    def __repr__(self):
        return f"<StockBasic(code={self.code}, name={self.name})>"


class DailyPrice(Base):
    """
    日线行情表
    数据来源: AKShare stock_zh_a_hist() → 东方财富历史行情接口
    所有数值为交易所原始数据，未经任何修改。
    """
    __tablename__ = "daily_price"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_code_date"),
        Index("idx_daily_date", "trade_date"),
        Index("idx_daily_code_date", "code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), ForeignKey("stock_basic.code"), nullable=False, comment="股票代码"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    open: Mapped[float] = mapped_column(Float, nullable=False, comment="开盘价")
    high: Mapped[float] = mapped_column(Float, nullable=False, comment="最高价")
    low: Mapped[float] = mapped_column(Float, nullable=False, comment="最低价")
    close: Mapped[float] = mapped_column(Float, nullable=False, comment="收盘价")
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="成交量(股)")
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="成交额(元)")
    pct_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")
    turnover: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="换手率(%)")

    # 关系
    stock = relationship("StockBasic", back_populates="daily_prices")

    def __repr__(self):
        return f"<DailyPrice(code={self.code}, date={self.trade_date}, close={self.close})>"


class BenchmarkData(Base):
    """
    基准指数数据（沪深300）
    数据来源: AKShare stock_zh_index_daily(symbol="sh000300")
    """
    __tablename__ = "benchmark_data"
    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_benchmark_date"),
        Index("idx_benchmark_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="指数代码")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    close: Mapped[float] = mapped_column(Float, nullable=False, comment="收盘价")
    pct_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")


class StrategyConfig(Base):
    """
    策略配置表
    存储已注册策略的配置快照
    """
    __tablename__ = "strategy_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, comment="策略名称")
    class_path: Mapped[str] = mapped_column(String(200), nullable=False, comment="Python类路径")
    params: Mapped[str] = mapped_column(Text, nullable=False, comment="JSON格式参数")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="策略描述")
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="策略来源文献")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="创建时间"
    )

    # 关系
    backtest_results = relationship("BacktestResult", back_populates="strategy")


class BacktestResult(Base):
    """
    回测结果表
    每次回测的结果持久化存储
    """
    __tablename__ = "backtest_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("strategy_config.id"), nullable=False, comment="策略ID"
    )
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="回测标的")
    stock_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="标的名称")
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="回测开始日期")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="回测结束日期")
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False, comment="初始资金")
    final_equity: Mapped[float] = mapped_column(Float, nullable=False, comment="最终权益")

    # 核心指标
    total_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="总收益率(%)")
    annual_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="年化收益率(%)")
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="夏普比率")
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最大回撤(%)")
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="胜率(%)")
    profit_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="盈亏比")
    total_trades: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="总交易次数")
    annual_volatility: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="年化波动率(%)")
    calmar_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="卡玛比率")

    # 基准对比
    benchmark_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="基准收益率(%)")
    excess_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="超额收益(%)")

    # 序列化数据
    equity_curve: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="净值曲线 JSON: [{date, equity}, ...]"
    )
    trades_detail: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="交易明细 JSON: [{entry_date, exit_date, ...}, ...]"
    )
    monthly_returns: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="月度收益率 JSON"
    )

    # 回测配置快照
    cost_config: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="交易成本配置 JSON"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="回测时间"
    )

    # 关系
    strategy = relationship("StrategyConfig", back_populates="backtest_results")

    def __repr__(self):
        return f"<BacktestResult(id={self.id}, strategy={self.stock_code}, return={self.total_return})>"


class DataSourceMeta(Base):
    """
    数据源元信息表
    记录每次数据下载的来源和时间
    """
    __tablename__ = "data_source_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="数据源名称")
    download_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="下载时间"
    )
    date_start: Mapped[Optional[date]] = mapped_column(Date, comment="数据起始日期")
    date_end: Mapped[Optional[date]] = mapped_column(Date, comment="数据结束日期")
    stock_count: Mapped[Optional[int]] = mapped_column(Integer, comment="股票数量")
    record_count: Mapped[Optional[int]] = mapped_column(Integer, comment="记录总数")
    status: Mapped[str] = mapped_column(
        String(20), default="completed", comment="状态: downloading/completed/failed"
    )
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="错误日志")


class TechnicalIndicator(Base):
    """
    技术指标预计算表
    数据来源: A股全市场数据/technical_indicators.csv — 预计算技术指标
    避免每次回测重复计算相同指标，加速回测速度。

    注意: MACD/RSI/KDJ 等指标需要一定的数据长度才能准确，
    数据起始部分（前 20~60 天）的指标值为空或为初始值。
    """
    __tablename__ = "technical_indicators"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_ti_code_date"),
        Index("idx_ti_code", "code"),
        Index("idx_ti_date", "trade_date"),
        Index("idx_ti_code_date", "code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, comment="股票代码")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")

    # MACD (12, 26, 9)
    macd_dif: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="MACD DIF线")
    macd_dea: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="MACD DEA线")
    macd_hist: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="MACD 柱状线")

    # RSI
    rsi14: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="RSI(14)")

    # KDJ (9, 3, 3)
    kdj_k: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="KDJ_K值")
    kdj_d: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="KDJ_D值")
    kdj_j: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="KDJ_J值")

    # Bollinger Bands (20, 2)
    boll_mid: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="布林带中轨(MA20)")
    boll_upper: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="布林带上轨")
    boll_lower: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="布林带下轨")

    def __repr__(self):
        return f"<TechnicalIndicator(code={self.code}, date={self.trade_date})>"


class FinanceSummary(Base):
    """
    季度财务摘要表（真实财报数据）
    数据来源: A股全市场数据/finance_summary.csv — 东方财富季度财报
    替代旧的 finance_snapshot_v2，提供 100+ 字段的专业财报数据。
    当前保留 34 个量化核心字段，均为最新季度数据。
    """
    __tablename__ = "finance_summary"
    __table_args__ = (
        UniqueConstraint("code", "_date", name="uq_fs_code_date"),
        Index("idx_fs_code", "code"),
        Index("idx_fs_roe", "ROETTM"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, comment="股票代码")
    _date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="数据日期")
    EndDate: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="报告期截止日")

    # 盈利能力
    ROE: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="ROE(%)")
    ROETTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="ROE TTM(%)")
    ROEWeighted: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="加权ROE(%)")
    EPS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每股收益")
    EPSTTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每股收益 TTM")
    BasicEPS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="基本每股收益")
    DilutedEPS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="稀释每股收益")
    NAPS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每股净资产")
    NetProfitRatio: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="净利率(%)")
    NetProfitRatioTTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="净利率 TTM(%)")

    # 负债与结构
    DebtAssetsRatio: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="资产负债率(%)")
    DebtEquityRatio: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="权益乘数")

    # 营收与利润
    OperatingRevenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="营业收入")
    OperatingRevenueTTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="营业收入 TTM")
    OperatingRevenueGrowRate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="营收增长率(%)")
    OperatingProfit: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="营业利润")
    OperatingProfitTTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="营业利润 TTM")
    TotalOperatingRevenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="营业总收入")
    NPParentCompanyOwners: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="归母净利润")
    NPParentCompanyOwnersTTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="归母净利润 TTM")
    NPParentCompanyYOY: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="归母净利润同比(%)")

    # 现金流
    NetOperateCashFlow: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="经营活动现金流净额")
    NetOperateCashFlowTTM: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="经营活动现金流 TTM")

    # 资产与股东权益
    TotalAssets: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="总资产")
    TotalShareholderEquity: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="股东权益合计")
    TotalLiability: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="总负债")

    # 增长率
    NetAssetGrowRate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="净资产增长率(%)")
    TotalAssetGrowRate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="总资产增长率(%)")

    # 每股现金流
    CashFlowPS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每股现金流")
    OperCashFlowPS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每股经营现金流")
    MainIncomePS: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="每股主营收入")

    def __repr__(self):
        return f"<FinanceSummary(code={self.code}, date={self._date})>"


class StockProfile(Base):
    """
    股票概况表
    数据来源: A股全市场数据/stock_profile.csv — 包含行业/板块/上市日期/注册资本等
    """
    __tablename__ = "stock_profile"
    __table_args__ = (
        Index("idx_sp_code", "code"),
        Index("idx_sp_industry", "industry"),
        Index("idx_sp_sector", "sector"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, comment="股票代码（纯数字）")
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="股票名称")
    listed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="上市日期")
    industry: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="所属行业")
    sector: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="所属板块")
    issue_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="发行价")
    reg_capital: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="注册资本(万元)")
    chairman: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="董事长")
    establish_date: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="成立日期")
    website: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="公司网站")
    business: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="主营业务")
    reg_address: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="注册地址")

    def __repr__(self):
        return f"<StockProfile(code={self.code}, name={self.name}, industry={self.industry})>"
