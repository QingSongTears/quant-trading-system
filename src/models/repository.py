"""
数据访问层 (Repository)
封装所有数据库查询操作，返回 Pandas DataFrame 或 ORM 对象
"""
from __future__ import annotations
from datetime import date

import pandas as pd
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from ..config import get_config
from ..db.engine import get_engine
from ..db.sql_utils import read_sql
from .database import Base, StockBasic, DailyPrice, BenchmarkData, StrategyConfig, BacktestResult, DataSourceMeta, TechnicalIndicator, FinanceSummary, StockProfile, FundFlowData, WalkForwardRun, WalkForwardWindow


class DataRepository:
    """数据仓库 — 统一数据访问入口"""

    def __init__(self):
        self.engine = get_engine()

    # ===== 初始化 =====

    def init_database(self):
        """创建所有表结构"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return Session(self.engine)

    # ===== 股票基本信息 =====

    def upsert_stock_basic(self, session: Session, code: str, name: str,
                           market: str, list_date: date | None = None,
                           industry: str | None = None):
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

    def get_stock_list(self, market: str | None = None) -> pd.DataFrame:
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

    def get_latest_date(self, code: str) -> date | None:
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

    def batch_insert_daily(self, session: Session, records: list[dict]):
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

    def get_all_strategies(self) -> list[StrategyConfig]:
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

    def get_backtest_result(self, result_id: int) -> BacktestResult | None:
        """获取回测结果详情（已 eager load strategy 关系）"""
        with self.get_session() as session:
            return session.query(BacktestResult) \
                .options(joinedload(BacktestResult.strategy)) \
                .filter_by(id=result_id).first()

    def get_recent_backtests(self, limit: int = 10) -> list[BacktestResult]:
        """获取最近的回测结果（已 eager load strategy 关系）"""
        with self.get_session() as session:
            return session.query(BacktestResult) \
                .options(joinedload(BacktestResult.strategy)) \
                .order_by(BacktestResult.created_at.desc()) \
                .limit(limit).all()

    def get_backtests_by_strategy(self, strategy_name: str) -> list[BacktestResult]:
        """获取某策略的所有回测记录"""
        with self.get_session() as session:
            strategy = session.query(StrategyConfig).filter_by(name=strategy_name).first()
            if not strategy:
                return []
            return session.query(BacktestResult).filter_by(strategy_id=strategy.id).all()

    def get_strategy_by_name(self, name: str) -> "StrategyConfig | None":
        """PR3.3: 按 name 查策略 — 替代 api.py 中 session.query(StrategyConfig).filter_by(...).first() 模式"""
        with self.get_session() as session:
            return session.query(StrategyConfig).filter_by(name=name).first()

    def get_models_summary_for_stock(
        self, models: list[dict], stock_code: str
    ) -> list[dict]:
        """PR3.3: 批量获取多模型在某只股票上的最近回测结果摘要 — 替代 api.py 内联查询

        Args:
            models: 模型配置列表,每项含 {'name': ..., 'strategy_type': ...}
            stock_code: 股票代码

        Returns:
            summary 列表,每项含 strategy_name/strategy_type/total_return/...
            或 has_data=False 表示无数据
        """
        from sqlalchemy import or_
        with self.get_session() as session:
            summaries = []
            for s in models:
                strategy_record = session.query(StrategyConfig).filter_by(name=s["name"]).first()
                if not strategy_record:
                    # 模糊匹配 fallback
                    strategy_record = session.query(StrategyConfig).filter(
                        StrategyConfig.name.like(f"%{s['name']}%")
                    ).first()

                if strategy_record:
                    result = session.query(BacktestResult).filter(
                        BacktestResult.strategy_id == strategy_record.id,
                        BacktestResult.stock_code == stock_code,
                    ).order_by(BacktestResult.created_at.desc()).first()

                    if result:
                        summaries.append({
                            "strategy_name": s["name"],
                            "strategy_type": s.get("strategy_type", "signal"),
                            "total_return": result.total_return,
                            "annual_return": result.annual_return,
                            "sharpe_ratio": result.sharpe_ratio,
                            "max_drawdown": result.max_drawdown,
                            "win_rate": result.win_rate,
                            "total_trades": result.total_trades,
                            "excess_return": result.excess_return,
                            "result_id": result.id,
                            "created_at": str(result.created_at),
                        })
                    else:
                        summaries.append({
                            "strategy_name": s["name"],
                            "strategy_type": s.get("strategy_type", "signal"),
                            "has_data": False,
                            "result_id": None,
                        })
                else:
                    summaries.append({
                        "strategy_name": s["name"],
                        "strategy_type": s.get("strategy_type", "signal"),
                        "has_data": False,
                        "result_id": None,
                    })
            return summaries

    # ===== 数据源元信息 =====

    def save_download_meta(self, session: Session, meta: dict):
        """保存下载元信息"""
        record = DataSourceMeta(**meta)
        session.add(record)

    def get_download_history(self) -> list[DataSourceMeta]:
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

    def get_finance_summary(self, code: str) -> dict | None:
        """获取某只股票的最新财务摘要"""
        with self.get_session() as session:
            result = session.query(FinanceSummary) \
                .filter(FinanceSummary.code == code) \
                .first()
            if not result:
                return None
            return {c.name: getattr(result, c.name) for c in result.__table__.columns}

    def get_latest_finance_for_codes(self, codes: list[str]) -> pd.DataFrame:
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

    def get_profitable_codes(self) -> list[str]:
        """获取所有 NPParentCompanyOwnersTTM > 0 的股票代码"""
        with self.get_session() as session:
            results = session.query(FinanceSummary.code) \
                .filter(FinanceSummary.NPParentCompanyOwnersTTM > 0) \
                .all()
            return [r[0] for r in results]

    # ===== 股票概况 (stock_profile) =====

    def get_stock_profile(self, code: str) -> dict | None:
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

    # ============================================================
    # param_server.py 迁移专用方法 (2026-06-24)
    # 19 处 sqlite3.connect 收敛到 DataRepository 的命名方法
    # ============================================================

    def get_all_stock_industries(self) -> dict[str, str]:
        """PR-fix param_server: 获取所有 code → industry 映射 (启动加载)

        原 param_server.py:274 启动时加载用于 industry_map 全局变量
        Returns:
            {code: industry_name}
        注: 实际数据在 stock_basic.industry (stock_profile 表为空时)
        """
        try:
            df = read_sql(
                "SELECT code, industry FROM stock_basic WHERE industry IS NOT NULL AND industry != ''",
                self.engine,
            )
            if df.empty:
                return {}
            return dict(zip(df["code"].astype(str), df["industry"].astype(str)))
        except Exception:
            return {}

    def get_all_stock_codes_names(self) -> pd.DataFrame:
        """PR-fix param_server: 获取所有股票 code+name (搜索索引构建用)

        原 param_server.py:565 `_build_search_index()` 全表读取
        Returns:
            DataFrame with columns: code, name
        注: 实际数据在 stock_basic (stock_profile 表为空时)
        """
        sql = "SELECT code, name FROM stock_basic WHERE name IS NOT NULL AND name != ''"
        return read_sql(sql, self.engine)

    def get_latest_benchmark_closes(
        self, index_code: str = "sh000300", n: int = 21
    ) -> list[tuple]:
        """PR-fix param_server: 获取最近 N 个交易日基准指数收盘价

        原 param_server.py:2111 大盘过滤逻辑
        Returns:
            [(trade_date, close), ...] 按 trade_date DESC 排序
        """
        sql = """
            SELECT trade_date, close FROM benchmark_data
            WHERE index_code = :code
            ORDER BY trade_date DESC
            LIMIT :n
        """
        df = read_sql(sql, self.engine, {"code": index_code, "n": int(n)})
        if df.empty:
            return []
        return list(zip(df["trade_date"].tolist(), df["close"].tolist()))

    def get_tech_indicators_at_or_before(
        self, code: str, trade_date: str | date, lookback_days: int = 10
    ) -> dict | None:
        """PR-fix param_server: 获取某日(或向前 N 天内最近)的技术指标

        原 param_server.py:2513 `_get_tech_features_single()`
        Returns:
            dict with macd_hist/rsi14/kdj_k/kdj_j 或 None
        """
        from sqlalchemy import text
        d_str = str(trade_date)[:10]
        sql = """
            SELECT macd_hist, rsi14, kdj_k, kdj_j FROM technical_indicators
            WHERE code = :code AND trade_date <= :d
            ORDER BY trade_date DESC
            LIMIT :n
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(sql),
                    {"code": code, "d": d_str, "n": int(lookback_days)},
                ).fetchall()
            if not rows:
                return None
            return dict(rows[0]._mapping)
        except Exception:
            return None

    # ----- backtest_result 复合查询 (Phase 2) -----

    def get_backtest_history_with_strategy(self, limit: int = 50) -> list[dict]:
        """PR-fix param_server: 回测历史 + 策略名 JOIN (dashboard 用)

        原 param_server.py:1861 /api/backtest/history
        Returns:
            [{id, strategy_id, strategy_name, stock_code, stock_name,
              start_date, end_date, total_return, sharpe_ratio, ...}, ...]
        """
        from sqlalchemy import text
        sql = """
            SELECT r.id, r.strategy_id, s.name AS strategy_name,
                   r.stock_code, r.stock_name,
                   r.start_date, r.end_date,
                   r.total_return, r.sharpe_ratio, r.max_drawdown,
                   r.win_rate, r.total_trades, r.annual_return,
                   r.created_at
            FROM backtest_result r
            LEFT JOIN strategy_config s ON r.strategy_id = s.id
            ORDER BY r.created_at DESC
            LIMIT :limit
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql), {"limit": int(limit)}).fetchall()
            return [dict(r._mapping) for r in rows]
        except Exception:
            return []

    def get_backtest_detail_with_strategy(self, result_id: int) -> dict | None:
        """PR-fix param_server: 单条回测 + 策略名 JOIN (backtest_detail 页面用)

        原 param_server.py:1890 /api/backtest/history/<id>
        """
        from sqlalchemy import text
        sql = """
            SELECT r.*, s.name AS strategy_name, s.class_path, s.params AS strategy_params
            FROM backtest_result r
            LEFT JOIN strategy_config s ON r.strategy_id = s.id
            WHERE r.id = :id
        """
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text(sql), {"id": int(result_id)}).fetchone()
            return dict(row._mapping) if row else None
        except Exception:
            return None

    def get_strategy_votes_for_stock(
        self, stock_code: str, min_trades: int = 1, limit: int = 100
    ) -> list[dict]:
        """PR-fix param_server: 某只股票上的多策略投票汇总

        原 param_server.py:2092 /api/strategy/signal/<code>
        Returns:
            [{strategy_name, total_return, sharpe_ratio, win_rate, total_trades, ...}]
        """
        from sqlalchemy import text
        sql = """
            SELECT s.name AS strategy_name, r.stock_code,
                   r.total_return, r.sharpe_ratio, r.max_drawdown,
                   r.win_rate, r.total_trades, r.created_at
            FROM backtest_result r
            LEFT JOIN strategy_config s ON r.strategy_id = s.id
            WHERE r.stock_code = :code
              AND r.total_return IS NOT NULL
              AND r.total_trades > :min_trades
            ORDER BY r.created_at DESC
            LIMIT :limit
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(sql),
                    {"code": stock_code, "min_trades": int(min_trades), "limit": int(limit)},
                ).fetchall()
            return [dict(r._mapping) for r in rows]
        except Exception:
            return []

    def get_dashboard_stats(self) -> dict:
        """PR-fix param_server: dashboard 聚合统计 (4 个查询合一)

        原 param_server.py:1919 /api/dashboard/stats
        Returns:
            {
              total_backtests, total_stocks, total_strategies,
              by_strategy: [{strategy_name, count, avg_return, max_return, min_return, ...}],
              top_by_strategy: [{strategy_name, stock_code, total_return, max_drawdown, ...}],
              prediction_total, prediction_verified, prediction_pending
            }
        """
        from sqlalchemy import text
        stats: dict = {"available": True}
        try:
            with self.engine.connect() as conn:
                # 1. 总数
                stats["total_backtests"] = conn.execute(
                    text("SELECT COUNT(*) FROM backtest_result")
                ).scalar() or 0
                stats["total_stocks"] = conn.execute(
                    text("SELECT COUNT(DISTINCT stock_code) FROM backtest_result WHERE stock_code IS NOT NULL")
                ).scalar() or 0
                stats["total_strategies"] = conn.execute(
                    text("SELECT COUNT(*) FROM strategy_config")
                ).scalar() or 0

                # 2. 按策略聚合
                by_strategy_rows = conn.execute(text("""
                    SELECT s.name AS strategy_name, COUNT(*) AS cnt,
                           AVG(r.total_return) AS avg_return,
                           MAX(r.total_return) AS max_return,
                           MIN(r.total_return) AS min_return,
                           AVG(r.sharpe_ratio) AS avg_sharpe,
                           AVG(r.win_rate) AS avg_win_rate
                    FROM backtest_result r
                    JOIN strategy_config s ON r.strategy_id = s.id
                    GROUP BY s.id
                    ORDER BY avg_return DESC
                """)).fetchall()
                stats["by_strategy"] = [dict(r._mapping) for r in by_strategy_rows]

                # 3. 每个策略 Top 20
                top_rows = conn.execute(text("""
                    SELECT s.name AS strategy_name, r.stock_code, r.stock_name,
                           r.total_return, r.sharpe_ratio, r.max_drawdown,
                           r.win_rate, r.created_at
                    FROM backtest_result r
                    JOIN strategy_config s ON r.strategy_id = s.id
                    WHERE r.id IN (
                        SELECT id FROM backtest_result r2
                        WHERE r2.strategy_id = r.strategy_id
                        ORDER BY r2.total_return DESC LIMIT 20
                    )
                    ORDER BY s.name, r.total_return DESC
                """)).fetchall()
                stats["top_by_strategy"] = [dict(r._mapping) for r in top_rows]

                # 4. prediction_record 统计(表可能不存在)
                try:
                    stats["prediction_total"] = conn.execute(
                        text("SELECT COUNT(*) FROM prediction_record")
                    ).scalar() or 0
                    stats["prediction_verified"] = conn.execute(
                        text("SELECT COUNT(*) FROM prediction_record WHERE verified = 1")
                    ).scalar() or 0
                    stats["prediction_pending"] = conn.execute(
                        text("SELECT COUNT(*) FROM prediction_record WHERE verified = 0 OR verified IS NULL")
                    ).scalar() or 0
                except Exception:
                    stats["prediction_total"] = 0
                    stats["prediction_verified"] = 0
                    stats["prediction_pending"] = 0
            return stats
        except Exception as e:
            logger.warning("get_dashboard_stats 失败: %s", e)
            stats["available"] = False
            stats["error"] = str(e)[:200]
            return stats

    def get_strategy_compare_stats(self, top_n: int = 10) -> dict:
        """PR-fix param_server: /api/strategy/compare 策略汇总 + Top N

        原 param_server.py:2027
        """
        from sqlalchemy import text
        out: dict = {"available": True}
        try:
            with self.engine.connect() as conn:
                # 策略汇总
                rows = conn.execute(text("""
                    SELECT s.name AS strategy_name,
                           COUNT(*) AS cnt,
                           AVG(r.total_return) AS avg_return,
                           AVG(r.sharpe_ratio) AS avg_sharpe,
                           MAX(r.total_return) AS best_return,
                           MIN(r.total_return) AS worst_return
                    FROM backtest_result r
                    JOIN strategy_config s ON r.strategy_id = s.id
                    GROUP BY s.id
                    ORDER BY avg_return DESC
                """)).fetchall()
                out["strategies"] = [dict(r._mapping) for r in rows]

                # Top N per strategy
                top_rows = conn.execute(text("""
                    SELECT s.name AS strategy_name, r.stock_code,
                           r.total_return, r.sharpe_ratio, r.max_drawdown,
                           r.created_at
                    FROM backtest_result r
                    JOIN strategy_config s ON r.strategy_id = s.id
                    WHERE r.id IN (
                        SELECT id FROM backtest_result r2
                        WHERE r2.strategy_id = r.strategy_id
                        ORDER BY r2.total_return DESC LIMIT :n
                    )
                    ORDER BY s.name, r.total_return DESC
                """), {"n": int(top_n)}).fetchall()
                out["top_by_strategy"] = [dict(r._mapping) for r in top_rows]
            return out
        except Exception as e:
            logger.warning("get_strategy_compare_stats 失败: %s", e)
            return {"available": False, "error": str(e)[:200]}

    # ----- 写操作 (Phase 2/3) -----

    def upsert_strategy_config_by_name(
        self, name: str, class_path: str, params: str,
        description: str = "", source: str = ""
    ) -> int:
        """PR-fix param_server: 按 name upsert strategy_config,返回 id

        原 param_server.py:1778
        """
        with self.get_session() as session:
            existing = session.query(StrategyConfig).filter_by(name=name).first()
            if existing:
                existing.class_path = class_path
                existing.params = params
                existing.description = description
                existing.source = source
                session.flush()
                return int(existing.id)
            sc = StrategyConfig(
                name=name, class_path=class_path, params=params,
                description=description, source=source,
            )
            session.add(sc)
            session.flush()
            return int(sc.id)

    def upsert_backtest_result(
        self, result_data: dict, key_fields: tuple = ("strategy_id", "stock_code", "start_date", "end_date")
    ) -> int:
        """PR-fix param_server: 按 (strategy_id, stock_code, 起止日期) upsert

        原 param_server.py:1778 落库逻辑
        Args:
            result_data: dict 含全部 BacktestResult 字段
            key_fields: 用于去重的字段元组
        Returns:
            int: 记录 id
        """
        from sqlalchemy import inspect
        from sqlalchemy.orm.attributes import instrumented_attribute
        with self.get_session() as session:
            # 构造过滤条件
            filters = {f: result_data[f] for f in key_fields if f in result_data}
            existing = None
            if filters:
                existing = session.query(BacktestResult).filter_by(**filters).first()
            if existing:
                # 更新
                for k, v in result_data.items():
                    if hasattr(existing, k):
                        setattr(existing, k, v)
                session.flush()
                return int(existing.id)
            else:
                record = BacktestResult(**result_data)
                session.add(record)
                session.flush()
                return int(record.id)

    # ============================================================
    # Walk-Forward (PR-fix 2026-06-24, LIVE_TRADING_ROADMAP 阶段 1)
    # ============================================================

    def save_walk_forward_run(
        self,
        run_data: dict,
        windows: list[dict],
    ) -> int:
        """保存一次 walk_forward 运行 + N 个窗口结果

        Args:
            run_data: 含 strategy_name/start_date/end_date/train_months/test_months
                     /step_months/n_optimize_samples/n_windows/oos_sharpe_mean 等
            windows: [{window_id, train_*, test_*, best_params (dict),
                       in_sample_sharpe, oos_sharpe, ...}, ...]
        Returns:
            int: run_id
        """
        import json as _json
        from datetime import date as _date
        session = self.get_session()
        try:
            # 规范化日期字段 (string → date)
            rd = dict(run_data)
            for k in ("start_date", "end_date"):
                if k in rd and isinstance(rd[k], str):
                    rd[k] = _date.fromisoformat(rd[k])
            wf_run = WalkForwardRun(**rd)
            session.add(wf_run)
            session.flush()
            run_id = int(wf_run.id)
            for w in windows:
                wd = dict(w)
                params = wd.pop("best_params", None)
                wd["best_params_json"] = _json.dumps(params, ensure_ascii=False) if params else None
                wd["run_id"] = run_id
                # 规范化窗口日期
                for k in ("train_start", "train_end", "test_start", "test_end"):
                    if k in wd and isinstance(wd[k], str):
                        wd[k] = _date.fromisoformat(wd[k])
                session.add(WalkForwardWindow(**wd))
            session.flush()
            session.commit()  # PR-fix: 必须 commit 否则 with 退出时 rollback
            return run_id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_walk_forward_runs(self, limit: int = 50) -> list[dict]:
        """列出所有 walk_forward 运行 (按创建时间倒序)"""
        with self.get_session() as session:
            runs = (
                session.query(WalkForwardRun)
                .order_by(WalkForwardRun.created_at.desc())
                .limit(int(limit))
                .all()
            )
            return [
                {
                    "id": r.id,
                    "strategy_name": r.strategy_name,
                    "start_date": str(r.start_date),
                    "end_date": str(r.end_date),
                    "train_months": r.train_months,
                    "test_months": r.test_months,
                    "step_months": r.step_months,
                    "n_optimize_samples": r.n_optimize_samples,
                    "n_windows": r.n_windows,
                    "oos_sharpe_mean": r.oos_sharpe_mean,
                    "oos_sharpe_std": r.oos_sharpe_std,
                    "worst_max_drawdown": r.worst_max_drawdown,
                    "avg_oos_annual_return": r.avg_oos_annual_return,
                    "avg_oos_win_rate": r.avg_oos_win_rate,
                    "passes_gate": bool(r.passes_gate),
                    "created_at": str(r.created_at)[:19] if r.created_at else None,
                }
                for r in runs
            ]

    def get_walk_forward_run(self, run_id: int) -> dict | None:
        """获取 walk_forward 运行详情 (含所有窗口)"""
        import json as _json
        with self.get_session() as session:
            r = session.query(WalkForwardRun).filter_by(id=run_id).first()
            if not r:
                return None
            windows = (
                session.query(WalkForwardWindow)
                .filter_by(run_id=run_id)
                .order_by(WalkForwardWindow.window_id)
                .all()
            )
            return {
                "id": r.id,
                "strategy_name": r.strategy_name,
                "start_date": str(r.start_date),
                "end_date": str(r.end_date),
                "train_months": r.train_months,
                "test_months": r.test_months,
                "step_months": r.step_months,
                "n_optimize_samples": r.n_optimize_samples,
                "n_windows": r.n_windows,
                "oos_sharpe_mean": r.oos_sharpe_mean,
                "oos_sharpe_std": r.oos_sharpe_std,
                "worst_max_drawdown": r.worst_max_drawdown,
                "avg_oos_annual_return": r.avg_oos_annual_return,
                "avg_oos_win_rate": r.avg_oos_win_rate,
                "passes_gate": bool(r.passes_gate),
                "created_at": str(r.created_at)[:19] if r.created_at else None,
                "windows": [
                    {
                        "window_id": w.window_id,
                        "train_start": str(w.train_start),
                        "train_end": str(w.train_end),
                        "test_start": str(w.test_start),
                        "test_end": str(w.test_end),
                        "best_params": _json.loads(w.best_params_json) if w.best_params_json else None,
                        "in_sample_sharpe": w.in_sample_sharpe,
                        "oos_sharpe": w.oos_sharpe,
                        "oos_annual_return": w.oos_annual_return,
                        "oos_max_drawdown": w.oos_max_drawdown,
                        "oos_total_trades": w.oos_total_trades,
                        "oos_win_rate": w.oos_win_rate,
                    }
                    for w in windows
                ],
            }

    def get_walk_forward_summary(self) -> dict:
        """汇总 walk_forward 全局状态 (跨 run)"""
        with self.get_session() as session:
            runs = session.query(WalkForwardRun).all()
            if not runs:
                return {
                    "total_runs": 0, "total_windows": 0,
                    "passed_runs": 0, "available": False,
                    "message": "暂无 walk_forward 记录,请先运行 scripts/walk_forward.py",
                }
            total_windows = sum(r.n_windows for r in runs)
            passed_runs = sum(1 for r in runs if r.passes_gate)
            best_run = max(runs, key=lambda r: (r.oos_sharpe_mean or -999))
            return {
                "total_runs": len(runs),
                "total_windows": total_windows,
                "passed_runs": passed_runs,
                "best_run": {
                    "id": best_run.id,
                    "strategy_name": best_run.strategy_name,
                    "oos_sharpe_mean": best_run.oos_sharpe_mean,
                    "worst_max_drawdown": best_run.worst_max_drawdown,
                },
                "available": True,
            }
