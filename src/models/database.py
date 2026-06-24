"""
SQLAlchemy 数据模型定义
所有表结构在此定义，通过 alembic 或 create_all 创建
"""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal


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
    list_date: Mapped[date | None] = mapped_column(Date, comment="上市日期")
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="退市日期")
    industry: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="所属行业")

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
    amount: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交额(元)")
    pct_change: Mapped[float | None] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True, comment="换手率(%)")

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
    pct_change: Mapped[float | None] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")


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
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="策略描述")
    source: Mapped[str | None] = mapped_column(Text, nullable=True, comment="策略来源文献")
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
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="标的名称")
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="回测开始日期")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="回测结束日期")
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False, comment="初始资金")
    final_equity: Mapped[float] = mapped_column(Float, nullable=False, comment="最终权益")

    # 核心指标
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总收益率(%)")
    annual_return: Mapped[float | None] = mapped_column(Float, nullable=True, comment="年化收益率(%)")
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="夏普比率")
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最大回撤(%)")
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="胜率(%)")
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True, comment="盈亏比")
    total_trades: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="总交易次数")
    annual_volatility: Mapped[float | None] = mapped_column(Float, nullable=True, comment="年化波动率(%)")
    calmar_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="卡玛比率")

    # 基准对比
    benchmark_return: Mapped[float | None] = mapped_column(Float, nullable=True, comment="基准收益率(%)")
    excess_return: Mapped[float | None] = mapped_column(Float, nullable=True, comment="超额收益(%)")

    # 序列化数据
    equity_curve: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="净值曲线 JSON: [{date, equity}, ...]"
    )
    trades_detail: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="交易明细 JSON: [{entry_date, exit_date, ...}, ...]"
    )
    monthly_returns: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="月度收益率 JSON"
    )

    # 回测配置快照
    cost_config: Mapped[str | None] = mapped_column(
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
    date_start: Mapped[date | None] = mapped_column(Date, comment="数据起始日期")
    date_end: Mapped[date | None] = mapped_column(Date, comment="数据结束日期")
    stock_count: Mapped[int | None] = mapped_column(Integer, comment="股票数量")
    record_count: Mapped[int | None] = mapped_column(Integer, comment="记录总数")
    status: Mapped[str] = mapped_column(
        String(20), default="completed", comment="状态: downloading/completed/failed"
    )
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误日志")


class WalkForwardRun(Base):
    """
    Walk-Forward 滚动验证运行记录 (LIVE_TRADING_ROADMAP 阶段 1 门禁依据)

    一次 walk_forward 验证 = 一个 run,包含多个 train/test 窗口的 OOS 表现。
    关键指标:
    - oos_sharpe_mean / oos_sharpe_std: OOS 夏普均值/标准差 (稳健性核心)
    - worst_max_drawdown: 最差窗口回撤 (门禁 -25%)
    - passes_gate: 是否通过 LIVE 门禁 (mean>=0.5, std<0.3, dd>=-25)
    """
    __tablename__ = "walk_forward_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="策略类名")
    start_date: Mapped[date] = mapped_column(Date, nullable=False, comment="数据起始")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, comment="数据结束")
    train_months: Mapped[int] = mapped_column(Integer, nullable=False, comment="训练窗口月数")
    test_months: Mapped[int] = mapped_column(Integer, nullable=False, comment="测试窗口月数")
    step_months: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="滚动步长月数")
    n_optimize_samples: Mapped[int] = mapped_column(Integer, default=20, comment="每窗口参数采样数")

    # 汇总指标 (PR-fix 2026-06-24 walk_forward web 可视化)
    n_windows: Mapped[int] = mapped_column(Integer, default=0, comment="窗口总数")
    oos_sharpe_mean: Mapped[float | None] = mapped_column(Float, nullable=True, comment="OOS 夏普均值")
    oos_sharpe_std: Mapped[float | None] = mapped_column(Float, nullable=True, comment="OOS 夏普标准差")
    worst_max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最差窗口回撤")
    avg_oos_annual_return: Mapped[float | None] = mapped_column(Float, nullable=True, comment="OOS 年化收益均值")
    avg_oos_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="OOS 胜率均值")
    passes_gate: Mapped[int] = mapped_column(Integer, default=0, comment="是否通过门禁 0/1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="创建时间"
    )

    # 关系
    windows = relationship(
        "WalkForwardWindow", back_populates="run",
        cascade="all, delete-orphan",
        order_by="WalkForwardWindow.window_id",
    )

    def __repr__(self):
        return (
            f"<WalkForwardRun(id={self.id}, strategy={self.strategy_name}, "
            f"oos_sharpe={self.oos_sharpe_mean}, gate={self.passes_gate})>"
        )


class WalkForwardWindow(Base):
    """
    Walk-Forward 单窗口 OOS 表现
    每个 run 含 N 个窗口,每窗口独立记录训练段/测试段/最优参数/OOS 指标。
    """
    __tablename__ = "walk_forward_window"
    __table_args__ = (
        UniqueConstraint("run_id", "window_id", name="uq_wf_run_window"),
        Index("idx_wf_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("walk_forward_run.id"), nullable=False, comment="所属 run"
    )
    window_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="窗口序号 (0-based)")

    # 训练段
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)

    # 测试段 (OOS)
    test_start: Mapped[date] = mapped_column(Date, nullable=False)
    test_end: Mapped[date] = mapped_column(Date, nullable=False)

    # 最优参数 (JSON 字符串,SQLite 无 JSONB)
    best_params_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="JSON 格式最优参数")

    # IS 指标 (训练段)
    in_sample_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)

    # OOS 指标 (测试段)
    oos_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_annual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_total_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oos_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 关系
    run = relationship("WalkForwardRun", back_populates="windows")

    def __repr__(self):
        return (
            f"<WalkForwardWindow(id={self.id}, window={self.window_id}, "
            f"oos_sharpe={self.oos_sharpe})>"
        )


class TechnicalIndicator(Base):
    """
    技术指标预计算表
    数据来源: market_data/technical_indicators.csv — 预计算技术指标
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
    macd_dif: Mapped[float | None] = mapped_column(Float, nullable=True, comment="MACD DIF线")
    macd_dea: Mapped[float | None] = mapped_column(Float, nullable=True, comment="MACD DEA线")
    macd_hist: Mapped[float | None] = mapped_column(Float, nullable=True, comment="MACD 柱状线")

    # RSI
    rsi14: Mapped[float | None] = mapped_column(Float, nullable=True, comment="RSI(14)")

    # KDJ (9, 3, 3)
    kdj_k: Mapped[float | None] = mapped_column(Float, nullable=True, comment="KDJ_K值")
    kdj_d: Mapped[float | None] = mapped_column(Float, nullable=True, comment="KDJ_D值")
    kdj_j: Mapped[float | None] = mapped_column(Float, nullable=True, comment="KDJ_J值")

    # Bollinger Bands (20, 2)
    boll_mid: Mapped[float | None] = mapped_column(Float, nullable=True, comment="布林带中轨(MA20)")
    boll_upper: Mapped[float | None] = mapped_column(Float, nullable=True, comment="布林带上轨")
    boll_lower: Mapped[float | None] = mapped_column(Float, nullable=True, comment="布林带下轨")

    def __repr__(self):
        return f"<TechnicalIndicator(code={self.code}, date={self.trade_date})>"


class FinanceSummary(Base):
    """
    季度财务摘要表（真实财报数据）
    数据来源: market_data/finance_summary.csv — 东方财富季度财报
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
    _date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="数据日期")
    EndDate: Mapped[date | None] = mapped_column(Date, nullable=True, comment="报告期截止日")

    # 盈利能力
    ROE: Mapped[float | None] = mapped_column(Float, nullable=True, comment="ROE(%)")
    ROETTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="ROE TTM(%)")
    ROEWeighted: Mapped[float | None] = mapped_column(Float, nullable=True, comment="加权ROE(%)")
    EPS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股收益")
    EPSTTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股收益 TTM")
    BasicEPS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="基本每股收益")
    DilutedEPS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="稀释每股收益")
    NAPS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股净资产")
    NetProfitRatio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净利率(%)")
    NetProfitRatioTTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净利率 TTM(%)")

    # 负债与结构
    DebtAssetsRatio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="资产负债率(%)")
    DebtEquityRatio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="权益乘数")

    # 营收与利润
    OperatingRevenue: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营业收入")
    OperatingRevenueTTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营业收入 TTM")
    OperatingRevenueGrowRate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营收增长率(%)")
    OperatingProfit: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营业利润")
    OperatingProfitTTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营业利润 TTM")
    TotalOperatingRevenue: Mapped[float | None] = mapped_column(Float, nullable=True, comment="营业总收入")
    NPParentCompanyOwners: Mapped[float | None] = mapped_column(Float, nullable=True, comment="归母净利润")
    NPParentCompanyOwnersTTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="归母净利润 TTM")
    NPParentCompanyYOY: Mapped[float | None] = mapped_column(Float, nullable=True, comment="归母净利润同比(%)")

    # 现金流
    NetOperateCashFlow: Mapped[float | None] = mapped_column(Float, nullable=True, comment="经营活动现金流净额")
    NetOperateCashFlowTTM: Mapped[float | None] = mapped_column(Float, nullable=True, comment="经营活动现金流 TTM")

    # 资产与股东权益
    TotalAssets: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总资产")
    TotalShareholderEquity: Mapped[float | None] = mapped_column(Float, nullable=True, comment="股东权益合计")
    TotalLiability: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总负债")

    # 增长率
    NetAssetGrowRate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净资产增长率(%)")
    TotalAssetGrowRate: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总资产增长率(%)")

    # 每股现金流
    CashFlowPS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股现金流")
    OperCashFlowPS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股经营现金流")
    MainIncomePS: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股主营收入")

    def __repr__(self):
        return f"<FinanceSummary(code={self.code}, date={self._date})>"


class StockProfile(Base):
    """
    股票概况表
    数据来源: market_data/stock_profile.csv — 包含行业/板块/上市日期/注册资本等
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
    listed_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上市日期")
    industry: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="所属行业")
    sector: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="所属板块")
    issue_price: Mapped[float | None] = mapped_column(Float, nullable=True, comment="发行价")
    reg_capital: Mapped[float | None] = mapped_column(Float, nullable=True, comment="注册资本(万元)")
    chairman: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="董事长")
    establish_date: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="成立日期")
    website: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="公司网站")
    business: Mapped[str | None] = mapped_column(Text, nullable=True, comment="主营业务")
    reg_address: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="注册地址")

    def __repr__(self):
        return f"<StockProfile(code={self.code}, name={self.name}, industry={self.industry})>"


class FundFlowData(Base):
    """
    资金流向表
    数据来源: fund_flow_120d.csv — 主力/超大单/大单/中单/小单净流入
    """
    __tablename__ = "fund_flow_data"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_ff_code_date"),
        Index("idx_ff_code", "code"),
        Index("idx_ff_date", "trade_date"),
        Index("idx_ff_code_date", "code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, comment="股票代码")
    market: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="市场")
    name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="股票名称")
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="交易日期")
    main_net: Mapped[float | None] = mapped_column(Float, nullable=True, comment="主力净流入(万元)")
    super_large_net: Mapped[float | None] = mapped_column(Float, nullable=True, comment="超大单净流入(万元)")
    large_net: Mapped[float | None] = mapped_column(Float, nullable=True, comment="大单净流入(万元)")
    medium_net: Mapped[float | None] = mapped_column(Float, nullable=True, comment="中单净流入(万元)")
    small_net: Mapped[float | None] = mapped_column(Float, nullable=True, comment="小单净流入(万元)")

    def __repr__(self):
        return f"<FundFlowData(code={self.code}, date={self.trade_date})>"
