"""
backtest 数据加载器 — ADR-0009
================================

封装 DataRepository + K 线 + 因子计算 + LRU 缓存 (含 RLock)。

职责单一:
- load_bars(code, start, end) → 单股 OHLCV (供 BacktestEngine / runner 用)
- load_universe(date) → 全市场股票池 (供 PortfolioBacktestEngine / portfolio_runner 用)
- load_benchmark(code, start, end) → 基准指数 (沪深 300 等)
- load_all_market_data(start, end) → 全市场 daily_price (含 LRU 缓存)
- _load_universe_factors(data, as_of_date, lookback) → 因子构建 (供 portfolio_runner 用)

不依赖 backtesting.py (隔离原则: 第三方边界只在 runner.py)
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Any

import pandas as pd

from ..db.sql_utils import read_sql
from ..models.repository import DataRepository

logger = logging.getLogger(__name__)


class BacktestDataLoader:
    """回测数据加载器 — 封装 DataRepository + 缓存

    与原 PortfolioBacktestEngine._load_all_data 行为一致:
    - LRU 缓存 (FIFO, max=4)
    - 双检锁 (RLock) 防 walk_forward 并发 race
    - pct_change 全 NULL 时从 close 自动计算
    - turnover 全 NULL 时使用 1.0 代理值

    使用示例:
        loader = BacktestDataLoader()
        df = loader.load_bars("000001", date(2024, 1, 1), date(2024, 6, 1))
    """

    def __init__(self, repo: DataRepository | None = None, cache_max: int = 4):
        self.repo = repo if repo is not None else DataRepository()
        try:
            self.repo.init_database()
        except Exception:
            pass  # 测试场景允许 repo mock

        self._data_cache: dict = {}
        self._data_cache_max = cache_max
        self._data_cache_lock = threading.RLock()

    # ─────────────────────────────────────────────
    # 单股 K 线 / 全市场数据
    # ─────────────────────────────────────────────

    def load_bars(
        self,
        code: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """加载单只股票的日 K 线数据(单股回测用)

        Args:
            code: 股票代码 (e.g. "000001")
            start, end: 回测起止日期

        Returns:
            DataFrame with columns: trade_date, open, high, low, close, volume, amount, pct_change, turnover
        """
        return self.repo.get_daily_data(code, start, end)

    def load_benchmark(
        self,
        code: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """加载基准指数数据 (e.g. sh000300 沪深 300)"""
        return self.repo.get_benchmark_data(code, start, end)

    def load_stock_list(self) -> pd.DataFrame:
        """加载股票列表 (含 code / name)"""
        return self.repo.get_stock_list()

    def lookup_stock_name(self, code: str) -> str:
        """获取股票名称(找不到返回空字符串)"""
        try:
            stock_list = self.load_stock_list()
            match = stock_list[stock_list["code"] == code]
            if not match.empty:
                return str(match.iloc[0]["name"])
        except Exception:
            pass
        return ""

    def load_universe(self, as_of_date: date) -> list[str]:
        """加载某交易日全市场股票列表(不含退市)

        Args:
            as_of_date: 交易日

        Returns:
            股票代码列表
        """
        try:
            stock_list = self.load_stock_list()
            return stock_list["code"].astype(str).tolist()
        except Exception as e:
            logger.warning(f"load_universe 失败: {e}")
            return []

    def load_all_market_data(
        self,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """加载全市场 daily_price 数据(含 LRU 缓存 + RLock)

        兼容原 PortfolioBacktestEngine._load_all_data 行为:
        - 第一检: 无锁快速路径
        - 第二检: 加锁后再次检查(防止 walk_forward 并发 race)
        - 缓存驱逐: FIFO (删最早插入的)
        - 字段修复: pct_change / turnover 全 NULL 时的兜底

        Args:
            start, end: 数据起止日期

        Returns:
            DataFrame with columns: code, name, list_date, mcap_yi, trade_date,
                                    open, high, low, close, volume, amount,
                                    pct_change, turnover
        """
        cache_key = (start, end)
        # 第一检 (无锁快速路径)
        if cache_key in self._data_cache:
            return self._data_cache[cache_key].copy()

        sql = """
            SELECT dp.code, sb.name, sb.list_date, NULL AS mcap_yi, dp.trade_date,
                   dp.open, dp.high, dp.low, dp.close,
                   dp.volume, dp.amount, dp.pct_change, dp.turnover
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.trade_date >= :start
              AND dp.trade_date <= :end
            ORDER BY dp.code, dp.trade_date
        """
        df = read_sql(sql, self.repo.engine, {"start": start, "end": end})
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 修复: pct_change 列可能为 NULL, 从 close 价格自动计算
        if df["pct_change"].isna().all():
            logger.info("pct_change 全为 NULL, 从 close 价格计算...")
            df = df.sort_values(["code", "trade_date"])
            df["prev_close"] = df.groupby("code")["close"].shift(1)
            df["pct_change"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
            df["pct_change"] = df["pct_change"].fillna(0)
            df = df.drop(columns=["prev_close"])

        # 修复: turnover 列可能为 NULL — 选股策略依赖它计算 mcap_yi
        if df["turnover"].isna().all():
            logger.warning(
                "turnover 全为 NULL, 使用 1.0 作为代理值. "
                "mcap_yi 退化为 amount/1e6 (亿元), 仅保留相对顺序."
            )
            df["turnover"] = 1.0
        else:
            df["turnover"] = df["turnover"].fillna(0)

        # 第二检 (锁内再检, 防止 race 后另一个 worker 已写入)
        with self._data_cache_lock:
            if cache_key not in self._data_cache:
                if len(self._data_cache) >= self._data_cache_max:
                    oldest_key = next(iter(self._data_cache))
                    del self._data_cache[oldest_key]
                self._data_cache[cache_key] = df

        return df

    def clear_cache(self) -> None:
        """清空数据缓存(测试用)"""
        with self._data_cache_lock:
            self._data_cache.clear()

    # ─────────────────────────────────────────────
    # 因子构建 (供 portfolio_runner 用)
    # ─────────────────────────────────────────────

    def build_universe_factors(
        self,
        data: pd.DataFrame,
        as_of_date: pd.Timestamp,
        lookback: int,
    ) -> pd.DataFrame:
        """为指定日期构建股票池(含因子)

        等价于原 PortfolioBacktestEngine._build_universe。
        输出列: code, name, close, avg_amount_wan, avg_turnover,
                market_cap_yi, return_Nd, volatility_Nd

        Args:
            data: 来自 load_all_market_data 的全市场数据
            as_of_date: 调仓日 (pd.Timestamp)
            lookback: 回看天数

        Returns:
            因子 DataFrame
        """
        cutoff_data = data[data["trade_date"] <= as_of_date].copy()
        recent = cutoff_data.groupby("code").tail(lookback)

        stock_counts = recent.groupby("code").size()
        if len(stock_counts) == 0:
            return pd.DataFrame()
        max_records = int(stock_counts.max())
        min_required = min(lookback // 2, max(max_records // 2, 1))
        if max_records < lookback // 2:
            logger.warning(
                "build_universe_factors: 数据区间只有 %d 条, 不足 lookback/2=%d, "
                "放宽到 %d",
                max_records, lookback // 2, min_required,
            )
        valid_codes = stock_counts[stock_counts >= min_required].index
        recent = recent[recent["code"].isin(valid_codes)]

        if recent.empty:
            return pd.DataFrame()

        agg_dict = {
            "name": "last",
            "list_date": "first",
            "mcap_yi": "last",
            "close": "last",
            "amount": "mean",
            "turnover": "mean",
            "pct_change": ["mean", "std"],
        }
        factors = recent.groupby("code").agg(agg_dict).reset_index()
        factors.columns = [
            "code", "name", "list_date", "mcap_from_db", "close",
            "avg_amount", "avg_turnover",
            "avg_return", "volatility",
        ]

        # 日均成交额 (元 → 万元)
        factors["avg_amount_wan"] = factors["avg_amount"] / 10000

        # 估算总市值 (亿元)
        valid_turnover = factors["avg_turnover"].notna() & (factors["avg_turnover"] > 0.01)
        factors["market_cap_yi"] = None
        factors.loc[valid_turnover, "market_cap_yi"] = (
            factors.loc[valid_turnover, "avg_amount"]
            / factors.loc[valid_turnover, "avg_turnover"]
            * 100 / 100000000
        )
        has_db_mcap = factors["mcap_from_db"].notna() & (factors["mcap_from_db"] > 0)
        no_calc_mcap = factors["market_cap_yi"].isna()
        factors.loc[no_calc_mcap & has_db_mcap, "market_cap_yi"] = (
            factors.loc[no_calc_mcap & has_db_mcap, "mcap_from_db"]
        )

        # 近 N 日累计收益率 / 波动率
        factors["return_Nd"] = factors["avg_return"] * lookback
        factors["volatility_Nd"] = factors["volatility"]

        factors = factors.drop(columns=["mcap_from_db"], errors="ignore")
        factors = factors.dropna(subset=["close", "avg_amount_wan"])
        factors = factors[factors["close"] > 0]
        return factors


def get_rebalance_dates(data: pd.DataFrame, rebalance_days: int) -> list[pd.Timestamp]:
    """获取调仓日列表 (每 rebalance_days 个交易日)

    等价于原 PortfolioBacktestEngine._get_rebalance_dates
    """
    all_dates = sorted(data["trade_date"].unique())
    return all_dates[::rebalance_days]