"""
src.db.tables - ORM 表结构定义 (兼容旧 import 路径)

实际实现位于 src.models.database；
此模块为兼容旧代码 (from src.db.tables import ...) 而保留。
"""
from src.models.database import (
    Base,
    StockBasic,
    DailyPrice,
    BenchmarkData,
    StrategyConfig,
    BacktestResult,
    DataSourceMeta,
    TechnicalIndicator,
    FinanceSummary,
    StockProfile,
    FundFlowData,
)

__all__ = [
    "Base", "StockBasic", "DailyPrice", "BenchmarkData", "StrategyConfig",
    "BacktestResult", "DataSourceMeta", "TechnicalIndicator", "FinanceSummary",
    "StockProfile", "FundFlowData",
]