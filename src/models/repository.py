"""
数据访问层 (Repository)
封装所有数据库查询操作，返回 Pandas DataFrame 或 ORM 对象
"""
from datetime import date
from typing import List, Optional

import pandas as pd
from sqlalchemy import create_engine, event, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, joinedload

from ..config import get_config, get_db_url
from ..db.sql_utils import read_sql
from .database import Base, StockBasic, DailyPrice, BenchmarkData, StrategyConfig, BacktestResult, DataSourceMeta, TechnicalIndicator, FinanceSummary, StockProfile, FundFlowData


# ============================================================
# SQLite 性能调优 (2026-06-21)
# ============================================================
# WAL 模式:读写不互斥,读并发性能提升 5-10x
# synchronous=NORMAL: 配合 WAL,断电丢失风险仅"最后一个事务"
# 替代默认的 synchronous=FULL (每次事务 fsync,慢但最安全)
#
# 注意: 共享缓存数据库(/:memory:)不支持 WAL,仅文件型 DB 生效
#       多进程写仍需互斥(SQLite 写锁),WAL 只解决读并发

@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """新连接建立时自动启用 WAL + NORMAL 同步

    注意: PRAGMA 在 Python sqlite3 默认隐式事务中不生效,
    必须 commit 才能让 journal_mode 切换真正生效
    (SQLAlchemy 的 connect 事件触发时,连接处于 autocommit,
    所以 commit() 是 no-op 但能保证 PRAGMA 生效)
    """
    mod = type(dbapi_connection).__module__ or ""
    if not mod.startswith("sqlite3"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # commit 确保 PRAGMA 生效
        if hasattr(dbapi_connection, "commit"):
            dbapi_connection.commit()
    except Exception:
        pass  # 内存 DB 或不支持 WAL 时静默跳过
    finally:
        cursor.close()


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
        sql = "SELECT code, name, market, list_date, industry FROM stock_basic"
        params: dict = {}
        if market:
            sql += " WHERE market = :market"
            params["market"] = market
        sql += " ORDER BY code"
        return read_sql(sql, self.engine, params)

    def get_stock_count(self) -> int:
        """获取股票总数"""
        with self.get_session() as session:
            return session.query(func.count(StockBasic.code)).scalar()

    # ===== 日线数据 =====

    def get_daily_data(self, code: str, start: date, end: date) -> pd.DataFrame:
        """获取指定股票在日期范围内的日线数据，返回 DataFrame"""
        sql = """
            SELECT trade_date, open, high, low, close, volume, amount, pct_change, turnover
            FROM daily_price
            WHERE code = :code
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date ASC
        """
        df = read_sql(sql, self.engine, {"code": code, "start": start, "end": end})
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
        sql = """
            SELECT trade_date, close, pct_change
            FROM benchmark_data
            WHERE index_code = :code
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date ASC
        """
        df = read_sql(sql, self.engine, {"code": index_code, "start": start, "end": end})
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
        sql = """
            SELECT dp.code, sb.name, dp.trade_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.amount, dp.pct_change, dp.turnover
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.trade_date >= :start
              AND dp.trade_date <= :end
            ORDER BY dp.code, dp.trade_date
        """
        df = read_sql(sql, self.engine, {"start": start, "end": end})
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
        sql = """
            SELECT trade_date, macd_dif, macd_dea, macd_hist,
                   rsi14, kdj_k, kdj_d, kdj_j,
                   boll_mid, boll_upper, boll_lower
            FROM technical_indicators
            WHERE code = :code
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date ASC
        """
        df = read_sql(sql, self.engine, {"code": code, "start": start, "end": end})
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
        sql = """
            SELECT code, _date, ROETTM, EPSTTM, NAPS,
                   DebtAssetsRatio, OperatingRevenueGrowRate,
                   NPParentCompanyOwnersTTM, NPParentCompanyYOY,
                   NetOperateCashFlowTTM, TotalShareholderEquity,
                   NetProfitRatioTTM, TotalAssets
            FROM finance_summary
            WHERE code IN :codes
        """
        return read_sql(sql, self.engine, {"codes": list(codes)})

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
        sql = """
            SELECT sp.industry, COUNT(*) as stock_count
            FROM stock_profile sp
            WHERE sp.industry IS NOT NULL AND sp.industry != ''
            GROUP BY sp.industry
            ORDER BY stock_count DESC
        """
        return read_sql(sql, self.engine)

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
        sql = """
            SELECT trade_date, main_net, super_large_net, large_net,
                   medium_net, small_net
            FROM fund_flow_data
            WHERE code = :code
              AND trade_date <= :end_date
            ORDER BY trade_date DESC
            LIMIT :lookback
        """
        df = read_sql(sql, self.engine, {
            "code": code,
            "end_date": end_date,
            "lookback": int(lookback),
        })
        if not df.empty:
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    # ===== 数据库元信息 (用于 /api/data/db-status 等监控端点) =====

    # 常用日期列名 — 按概率从高到低排列,首次命中即返回
    _DATE_COL_CANDIDATES = ("trade_date", "date", "as_of_date", "report_date", "created_at", "end_date", "_date")

    # 业务表的中文描述 — 不在表内的保持空字符串
    _TABLE_DESCRIPTIONS = {
        "daily_price": "日K线数据",
        "stock_basic": "股票基本信息",
        "tencent_quotes": "腾讯实时行情",
        "stock_profile": "股票档案",
        "fund_flow": "资金流向",
        "fund_flow_data": "资金流向",
        "technical_indicators": "技术指标",
        "lhb_institutional": "龙虎榜机构",
        "margin_trading": "融资融券",
        "shareholder_count": "股东人数",
        "finance_summary": "财务摘要",
        "backtest_result": "回测结果",
        "strategy_config": "策略配置",
    }

    def get_table_stats(self, table_name: str) -> dict:
        """获取单表统计: 行数 + 最早/最新日期

        返回格式 (供 /api/data/db-status 直接使用):
            {"name": "daily_price", "row_count": 12345,
             "latest_date": "2026-06-20", "earliest_date": "2018-01-02",
             "description": "日K线数据"}

        Args:
            table_name: 表名(已通过 sqlite_master 验证存在)

        Returns:
            dict 含 row_count / latest_date / earliest_date / description
        """
        from sqlalchemy import text, inspect
        result = {
            "name": table_name,
            "row_count": 0,
            "latest_date": None,
            "earliest_date": None,
            "description": self._TABLE_DESCRIPTIONS.get(table_name, ""),
        }
        with self.engine.connect() as conn:
            try:
                row = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).fetchone()
                result["row_count"] = row[0] if row else 0
            except Exception:
                return result

            # 探测日期列: 优先查 PRAGMA table_info 拿到列名,再批量 MAX/MIN
            try:
                insp = inspect(self.engine)
                cols = {c["name"].lower() for c in insp.get_columns(table_name)}
                for cand in self._DATE_COL_CANDIDATES:
                    if cand not in cols:
                        continue
                    r_max = conn.execute(
                        text(f'SELECT MAX({cand}) FROM "{table_name}"')
                    ).fetchone()
                    if r_max and r_max[0]:
                        result["latest_date"] = str(r_max[0])[:10]
                        r_min = conn.execute(
                            text(f'SELECT MIN({cand}) FROM "{table_name}"')
                        ).fetchone()
                        if r_min and r_min[0]:
                            result["earliest_date"] = str(r_min[0])[:10]
                    break
            except Exception:
                # 日期探测失败不阻断,只返回 row_count
                pass

        return result

    def list_tables(self) -> list:
        """列出所有业务表名(按字母排序)

        Returns:
            list[str],如 ["backtest_result", "daily_price", "stock_basic", ...]
        """
        from sqlalchemy import text
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ).fetchall()
        return [r[0] for r in rows]

    def get_db_status(self) -> dict:
        """汇总数据库状态 — 供 /api/data/db-status 等端点

        Returns:
            {"tables": [{"name", "row_count", "latest_date", "earliest_date", "description"}, ...]}
        """
        tables = self.list_tables()
        return {"tables": [self.get_table_stats(t) for t in tables]}
