"""
数据访问层 (Repository)
封装所有数据库查询操作，返回 Pandas DataFrame 或 ORM 对象
"""
from datetime import date
from typing import List, Optional

import pandas as pd
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, joinedload

from ..config import get_config, get_db_url
from .database import Base, StockBasic, DailyPrice, BenchmarkData, StrategyConfig, BacktestResult, DataSourceMeta, TechnicalIndicator, FinanceSummary, StockProfile, FundFlowData


class DataRepository:
    """数据仓库 — 统一数据访问入口"""

    def __init__(self):
        config = get_config()
        self.engine = create_engine(
            get_db_url(config),
            echo=config.get("database", {}).get("echo", False)
        )

    # ===== 初始化 =====

    def init_database(self):
        """创建所有表结构"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return Session(self.engine)

    # ===== 股票基本信息 =====

    def upsert_stock_basic(self, session: Session, code: str, name: str,
                           market: str, list_date: Optional[date] = None,
                           industry: Optional[str] = None):
        """插入或更新股票基本信息"""
        stock = session.query(StockBasic).filter_by(code=code).first()
        if stock:
            stock.name = name
            stock.market = market
            if list_date:
                stock.list_date = list_date
            if industry:
                stock.industry = industry
        else:
            stock = StockBasic(
                code=code, name=name, market=market,
                list_date=list_date, industry=industry
            )
            session.add(stock)

    def get_stock_list(self, market: Optional[str] = None) -> pd.DataFrame:
        """获取股票列表，可选按市场筛选"""
        query = "SELECT code, name, market, list_date, industry FROM stock_basic"
        if market:
            query += f" WHERE market = '{market}'"
        query += " ORDER BY code"
        return pd.read_sql(query, self.engine)

    def get_stock_count(self) -> int:
        """获取股票总数"""
        with self.get_session() as session:
            return session.query(func.count(StockBasic.code)).scalar()

    # ===== 日线数据 =====

    def get_daily_data(self, code: str, start: date, end: date) -> pd.DataFrame:
        """获取指定股票在日期范围内的日线数据，返回 DataFrame"""
        query = f"""
            SELECT trade_date, open, high, low, close, volume, amount, pct_change, turnover
            FROM daily_price
            WHERE code = '{code}'
              AND trade_date >= '{start}'
              AND trade_date <= '{end}'
            ORDER BY trade_date ASC
        """
        df = pd.read_sql(query, self.engine)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
        return df

    def get_latest_date(self, code: str) -> Optional[date]:
        """获取某只股票的最新数据日期"""
        with self.get_session() as session:
            result = session.query(func.max(DailyPrice.trade_date)) \
                .filter(DailyPrice.code == code).scalar()
            return result

    def get_data_coverage(self) -> dict:
        """获取数据覆盖概览"""
        with self.get_session() as session:
            total_stocks = session.query(func.count(StockBasic.code)).scalar()
            total_records = session.query(func.count(DailyPrice.id)).scalar()
            min_date = session.query(func.min(DailyPrice.trade_date)).scalar()
            max_date = session.query(func.max(DailyPrice.trade_date)).scalar()

        return {
            "total_stocks": total_stocks,
            "total_records": total_records,
            "date_range": {"start": min_date, "end": max_date}
        }

    def batch_insert_daily(self, session: Session, records: List[dict]):
        """批量插入日线数据（使用 bulk_insert_mappings 提升性能）"""
        if not records:
            return
        # 使用核心表对象进行批量操作
        from sqlalchemy import Table, MetaData
        metadata = MetaData()
        metadata.reflect(bind=self.engine)
        table = Table('daily_price', metadata, autoload_with=self.engine)

        session.execute(table.insert().prefix_with("OR IGNORE"), records)

    # ===== 基准数据 =====

    def get_benchmark_data(self, index_code: str, start: date, end: date) -> pd.DataFrame:
        """获取基准指数数据"""
        query = f"""
            SELECT trade_date, close, pct_change
            FROM benchmark_data
            WHERE index_code = '{index_code}'
              AND trade_date >= '{start}'
              AND trade_date <= '{end}'
            ORDER BY trade_date ASC
        """
        df = pd.read_sql(query, self.engine)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df.set_index('trade_date', inplace=True)
        return df

    # ===== 策略配置 =====

    def save_strategy_config(self, session: Session, name: str, class_path: str,
                             params: str, description: str = None, source: str = None):
        """保存策略配置"""
        existing = session.query(StrategyConfig).filter_by(name=name).first()
        if existing:
            existing.class_path = class_path
            existing.params = params
            existing.description = description
            existing.source = source
        else:
            config = StrategyConfig(
                name=name, class_path=class_path,
                params=params, description=description, source=source
            )
            session.add(config)

    def get_all_strategies(self) -> List[StrategyConfig]:
        """获取所有已注册策略"""
        with self.get_session() as session:
            return session.query(StrategyConfig).all()

    # ===== 回测结果 =====

    def save_backtest_result(self, session: Session, result_data: dict) -> int:
        """保存回测结果，返回记录ID"""
        result = BacktestResult(**result_data)
        session.add(result)
        session.flush()
        return result.id

    def get_backtest_result(self, result_id: int) -> Optional[BacktestResult]:
        """获取回测结果详情（已 eager load strategy 关系）"""
        with self.get_session() as session:
            return session.query(BacktestResult) \
                .options(joinedload(BacktestResult.strategy)) \
                .filter_by(id=result_id).first()

    def get_recent_backtests(self, limit: int = 10) -> List[BacktestResult]:
        """获取最近的回测结果（已 eager load strategy 关系）"""
        with self.get_session() as session:
            return session.query(BacktestResult) \
                .options(joinedload(BacktestResult.strategy)) \
                .order_by(BacktestResult.created_at.desc()) \
                .limit(limit).all()

    def get_backtests_by_strategy(self, strategy_name: str) -> List[BacktestResult]:
        """获取某策略的所有回测记录"""
        with self.get_session() as session:
            strategy = session.query(StrategyConfig).filter_by(name=strategy_name).first()
            if not strategy:
                return []
            return session.query(BacktestResult).filter_by(strategy_id=strategy.id).all()

    # ===== 数据源元信息 =====

    def save_download_meta(self, session: Session, meta: dict):
        """保存下载元信息"""
        record = DataSourceMeta(**meta)
        session.add(record)

    def get_download_history(self) -> List[DataSourceMeta]:
        """获取下载历史"""
        with self.get_session() as session:
            return session.query(DataSourceMeta) \
                .order_by(DataSourceMeta.download_time.desc()) \
                .limit(10).all()

    # ===== 全市场数据 (选股策略用) =====

    def get_all_daily_data(self, start: date, end: date) -> pd.DataFrame:
        """
        获取全市场日线数据 (JOIN stock_basic 获取 name)

        Returns:
            DataFrame with columns: code, name, trade_date, open, high, low,
            close, volume, amount, pct_change, turnover
        """
        query = f"""
            SELECT dp.code, sb.name, dp.trade_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.amount, dp.pct_change, dp.turnover
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.trade_date >= '{start}'
              AND dp.trade_date <= '{end}'
            ORDER BY dp.code, dp.trade_date
        """
        df = pd.read_sql(query, self.engine)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df

    def get_all_stock_basics(self) -> pd.DataFrame:
        """获取所有股票基本信息"""
        return self.get_stock_list()

    # ===== 技术指标 =====

    def get_technical_indicators(self, code: str, start: date, end: date) -> pd.DataFrame:
        """
        获取指定股票在日期范围内的预计算技术指标

        Returns:
            DataFrame with columns: trade_date, macd_dif, macd_dea, macd_hist,
            rsi14, kdj_k, kdj_d, kdj_j, boll_mid, boll_upper, boll_lower
        """
        query = f"""
            SELECT trade_date, macd_dif, macd_dea, macd_hist,
                   rsi14, kdj_k, kdj_d, kdj_j,
                   boll_mid, boll_upper, boll_lower
            FROM technical_indicators
            WHERE code = '{code}'
              AND trade_date >= '{start}'
              AND trade_date <= '{end}'
            ORDER BY trade_date ASC
        """
        df = pd.read_sql(query, self.engine)
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df.set_index("trade_date", inplace=True)
        return df

    def get_technical_indicator_coverage(self) -> dict:
        """获取技术指标数据覆盖概览"""
        with self.get_session() as session:
            total_records = session.query(func.count(TechnicalIndicator.id)).scalar()
            total_stocks = session.query(func.count(
                func.distinct(TechnicalIndicator.code)
            )).scalar()
            min_date = session.query(func.min(TechnicalIndicator.trade_date)).scalar()
            max_date = session.query(func.max(TechnicalIndicator.trade_date)).scalar()

        return {
            "total_records": total_records,
            "total_stocks": total_stocks,
            "date_range": {"start": min_date, "end": max_date}
        }

    # ===== 财务摘要 (finance_summary) =====

    def get_finance_summary(self, code: str) -> Optional[dict]:
        """获取某只股票的最新财务摘要"""
        with self.get_session() as session:
            result = session.query(FinanceSummary) \
                .filter(FinanceSummary.code == code) \
                .first()
            if not result:
                return None
            return {c.name: getattr(result, c.name) for c in result.__table__.columns}

    def get_latest_finance_for_codes(self, codes: List[str]) -> pd.DataFrame:
        """批量获取多只股票的最新财务摘要

        Returns:
            DataFrame with code, NPParentCompanyOwnersTTM, ROETTM, TotalShareholderEquity, etc.
        """
        if not codes:
            return pd.DataFrame()
        codes_str = ", ".join([f"'{c}'" for c in codes])
        query = f"""
            SELECT code, _date, ROETTM, EPSTTM, NAPS,
                   DebtAssetsRatio, OperatingRevenueGrowRate,
                   NPParentCompanyOwnersTTM, NPParentCompanyYOY,
                   NetOperateCashFlowTTM, TotalShareholderEquity,
                   NetProfitRatioTTM, TotalAssets
            FROM finance_summary
            WHERE code IN ({codes_str})
        """
        return pd.read_sql(query, self.engine)

    def get_profitable_codes(self) -> List[str]:
        """获取所有 NPParentCompanyOwnersTTM > 0 的股票代码"""
        with self.get_session() as session:
            results = session.query(FinanceSummary.code) \
                .filter(FinanceSummary.NPParentCompanyOwnersTTM > 0) \
                .all()
            return [r[0] for r in results]

    # ===== 股票概况 (stock_profile) =====

    def get_stock_profile(self, code: str) -> Optional[dict]:
        """获取某只股票的概况信息"""
        with self.get_session() as session:
            result = session.query(StockProfile) \
                .filter(StockProfile.code == code) \
                .first()
            if not result:
                return None
            return {c.name: getattr(result, c.name) for c in result.__table__.columns}

    def get_industry_distribution(self) -> pd.DataFrame:
        """获取行业分布统计"""
        query = """
            SELECT sp.industry, COUNT(*) as stock_count
            FROM stock_profile sp
            WHERE sp.industry IS NOT NULL AND sp.industry != ''
            GROUP BY sp.industry
            ORDER BY stock_count DESC
        """
        return pd.read_sql(query, self.engine)

    def enrich_stock_basic_with_profile(self) -> int:
        """从 stock_profile 补充 stock_basic 中缺失的 industry 和 list_date

        Returns:
            更新的行数
        """
        updated = 0
        with self.get_session() as session:
            profiles = session.query(StockProfile).all()
            for sp in profiles:
                stock = session.query(StockBasic).filter(StockBasic.code == sp.code).first()
                if not stock:
                    continue
                changed = False
                if sp.industry and (not stock.industry or stock.industry == ''):
                    stock.industry = sp.industry
                    changed = True
                if sp.listed_date and not stock.list_date:
                    stock.list_date = sp.listed_date
                    changed = True
                if changed:
                    updated += 1
            session.commit()
        return updated

    # ===== 资金流向 (fund_flow_data) =====

    def get_fund_flow_coverage(self) -> dict:
        """获取资金流向数据覆盖概览"""
        with self.get_session() as session:
            total = session.query(func.count(FundFlowData.id)).scalar()
            stocks = session.query(func.count(func.distinct(FundFlowData.code))).scalar()
            min_d = session.query(func.min(FundFlowData.trade_date)).scalar()
            max_d = session.query(func.max(FundFlowData.trade_date)).scalar()
        return {
            "total_records": total,
            "total_stocks": stocks,
            "date_range": {"start": min_d, "end": max_d},
            "available": total > 0,
        }

    def get_fund_flow_for_code(self, code: str, end_date: str, lookback: int = 30) -> "pd.DataFrame":
        """获取某只股票的资金流向数据"""
        import pandas as pd
        query = f"""
            SELECT trade_date, main_net, super_large_net, large_net,
                   medium_net, small_net
            FROM fund_flow_data
            WHERE code = '{code}'
              AND trade_date <= '{end_date}'
            ORDER BY trade_date DESC
            LIMIT {lookback}
        """
        df = pd.read_sql(query, self.engine)
        if not df.empty:
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df
