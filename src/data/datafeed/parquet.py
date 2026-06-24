"""
ParquetDatafeed — 从本地 parquet 文件读行情数据 (vnpy 风格 + polars)

数据源: market_data/parquet/daily/{vt_symbol}.parquet
每个文件 ~22 KB (5000+ 个文件, 总 223 MB)

vnpy alpha 的关键选择:
  - 每只股票一个 parquet 文件 (按 vt_symbol 切, OLAP 友好)
  - polars lazy API (pl.scan_parquet + filter + collect)
  - 跨文件查询时用 pl.scan_parquet(dir/*.parquet) 一次 lazy, 自动并行
  - 增量更新: pl.concat + unique + sort

vs LocalDatafeed (SQLite):
  - 优势: 大批量列扫描 + filter (全市场某日 / get_stock_list 极快, 30x)
  - 劣势: 小 universe 查询 (跨 10k+ 文件 metadata 开销, 比 SQLite 慢)
  - 实测 (2026-06-23):
      get_stock_list:      Parquet 1.1s vs SQLite 35s   ✅ 30x
      全市场某日 5040 只:  Parquet 1.1s vs SQLite 6s    ✅ 5x
      50 只某日:           Parquet 1.2s vs SQLite 2ms  ❌ 0.002x
      单只股票 K 线:       Parquet 10ms vs SQLite 36ms ✅ 4x
  - 选型建议:
      全市场 scan / 特征工程 / ML / 大批量指标计算 → Parquet
      小 universe 回测 hot path / 单点精确查询 → SQLite
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import polars as pl

from ...gateway import BarData, ContractData
from .base import (
    BaseDatafeed,
    Interval,
    code_to_market,
    code_to_vt_symbol,
    vt_symbol_to_code,
    vt_symbol_to_exchange,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# ── 默认路径 ──────────────────────────────────────


DEFAULT_PARQUET_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "market_data"
    / "parquet"
    / "daily"
)


# ── ParquetDatafeed ──────────────────────────────


class ParquetDatafeed(BaseDatafeed):
    """
    Parquet 数据源 (vnpy 风格 + polars lazy)

    数据准备:
      python scripts/build_parquet.py   # 从 CSV 生成 parquet

    用法:
        df = ParquetDatafeed()
        df.init()
        bars = df.get_bars("000001.SZ", start=date(2024, 1, 1), end=date(2024, 3, 1))
    """

    name: str = "PARQUET"

    def __init__(self, parquet_dir: Optional[Path] = None) -> None:
        super().__init__()
        self.parquet_dir = Path(parquet_dir) if parquet_dir else DEFAULT_PARQUET_DIR
        # 单只股票缓存 (LazyFrame → collect 后)
        self._file_cache: Dict[str, pl.DataFrame] = {}

    def init(self) -> None:
        """检查 parquet 目录是否存在"""
        if not self.parquet_dir.exists():
            raise FileNotFoundError(
                f"parquet 目录不存在: {self.parquet_dir}\n"
                f"请先运行: python scripts/build_parquet.py"
            )
        files = list(self.parquet_dir.glob("*.parquet"))
        if not files:
            raise FileNotFoundError(
                f"parquet 目录为空: {self.parquet_dir}\n"
                f"请先运行: python scripts/build_parquet.py"
            )
        self.inited = True
        logger.info(f"{self.name} Datafeed 初始化完成 ({len(files)} 个文件)")

    def close(self) -> None:
        """清缓存"""
        self._file_cache.clear()
        self.inited = False

    # ── 取历史 K 线 ─────────────────────────────────

    def get_bars(
        self,
        vt_symbol: str,
        interval: str = Interval.DAY_1,
        start: Optional[date] = None,
        end: Optional[date] = None,
        count: Optional[int] = None,
    ) -> List[BarData]:
        if not self.inited:
            self.init()

        if interval != Interval.DAY_1:
            raise NotImplementedError(
                f"ParquetDatafeed 仅支持日 K (1d), 当前 {interval} 待实现"
            )

        # 单股票 lazy read
        path = self.parquet_dir / f"{vt_symbol}.parquet"
        if not path.exists():
            logger.warning(f"parquet 文件不存在: {path}")
            return []

        df = self._scan_file(path)

        # 应用过滤 (predicate pushdown)
        if start is not None:
            df = df.filter(pl.col("datetime") >= pl.lit(pd_to_datetime(start)))
        if end is not None:
            df = df.filter(pl.col("datetime") <= pl.lit(pd_to_datetime(end)))
        if count is not None and start is None and end is None:
            df = df.tail(count)

        return self._pl_to_bars(df.collect())

    # ── 取全市场合约列表 ──────────────────────────────

    def get_stock_list(self) -> List[ContractData]:
        if not self.inited:
            self.init()

        # 一次 lazy scan 取 distinct (symbol, exchange, name)
        # 注意: 每只股票每个文件只有一行 (snapshot 取首行)
        lf = pl.scan_parquet(
            str(self.parquet_dir / "*.parquet"),
        )
        df = (
            lf
            .select(["symbol", "exchange", "name"])
            .unique(subset=["symbol", "exchange"])
            .sort("symbol")
            .collect()
        )

        contracts = []
        for row in df.iter_rows(named=True):
            c = ContractData(
                gateway_name="PARQUET",
                symbol=row["symbol"],
                exchange=row["exchange"],
                name=row["name"] or "",
                product="STOCK",
                size=1,
                pricetick=0.01,
                min_volume=100,
            )
            contracts.append(c)
        return contracts

    # ── 批量取某日 BarData (回测 hot path) ────────────

    def get_bars_by_date(
        self,
        query_date: date,
        universe: Optional[List[str]] = None,
        interval: str = Interval.DAY_1,
    ) -> Dict[str, BarData]:
        """
        给定日期, 批量取所有股票的 BarData (回测用)

        polars lazy scan 跨文件 + predicate pushdown, 极快
        """
        if not self.inited:
            self.init()
        if interval != Interval.DAY_1:
            return super().get_bars_by_date(query_date, universe, interval)

        target_dt = pd_to_datetime(query_date)

        # 路径 A: 有 universe → filter by vt_symbol in universe
        lf = pl.scan_parquet(
            str(self.parquet_dir / "*.parquet"),
        ).filter(pl.col("datetime") == pl.lit(target_dt))

        if universe:
            # vt_symbol = symbol + "." + exchange (重构)
            lf = lf.with_columns(
                (pl.col("symbol") + pl.lit(".") + pl.col("exchange")).alias("vt_symbol")
            ).filter(pl.col("vt_symbol").is_in(universe))

        df = lf.collect()

        # 转 dict
        result: Dict[str, BarData] = {}
        for row in df.iter_rows(named=True):
            vt_symbol = f"{row['symbol']}.{row['exchange']}"
            bar = BarData(
                gateway_name="PARQUET",
                symbol=row["symbol"],
                exchange=row["exchange"],
                datetime=row["datetime"],
                interval=row.get("interval", "1d"),
                name=row.get("name", ""),
                open_price=float(row["open_price"]),
                high_price=float(row["high_price"]),
                low_price=float(row["low_price"]),
                close_price=float(row["close_price"]),
                volume=float(row["volume"]),
                turnover=float(row["turnover"]),
                open_interest=float(row.get("open_interest", 0.0)),
            )
            result[vt_symbol] = bar

        return result

    # ── 交易日历 ──────────────────────────────────────

    def get_trading_calendar(
        self,
        start: date,
        end: date,
    ) -> List[date]:
        if not self.inited:
            self.init()

        df = (
            pl.scan_parquet(str(self.parquet_dir / "*.parquet"))
            .select(pl.col("datetime").unique().sort())
            .filter(
                (pl.col("datetime") >= pl.lit(pd_to_datetime(start)))
                & (pl.col("datetime") <= pl.lit(pd_to_datetime(end)))
            )
            .collect()
        )

        return sorted({d.date() for d in df["datetime"].to_list()})

    # ── 内部方法 ──────────────────────────────────────

    def _scan_file(self, path: Path) -> pl.LazyFrame:
        """单文件 lazy read (predicate pushdown)"""
        return pl.scan_parquet(path)


# ── 工具函数 ──────────────────────────────────────


def pd_to_datetime(d) -> datetime:
    """统一 date/datetime 转 datetime"""
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, datetime.min.time())


# ── Backward compat alias ────────────────────────

# 让用户能直接看到 BarData 列表转换函数（外部可能用到）
def _pl_to_bars(df: pl.DataFrame) -> List[BarData]:
    """polars DataFrame → List[BarData]"""
    bars = []
    for row in df.iter_rows(named=True):
        bar = BarData(
            gateway_name="PARQUET",
            symbol=row["symbol"],
            exchange=row["exchange"],
            datetime=row["datetime"],
            interval=row.get("interval", "1d"),
            name=row.get("name", ""),
            open_price=float(row["open_price"]),
            high_price=float(row["high_price"]),
            low_price=float(row["low_price"]),
            close_price=float(row["close_price"]),
            volume=float(row["volume"]),
            turnover=float(row["turnover"]),
            open_interest=float(row.get("open_interest", 0.0)),
        )
        bars.append(bar)
    return bars


# 把 _pl_to_bars 绑到 ParquetDatafeed 类
ParquetDatafeed._pl_to_bars = staticmethod(_pl_to_bars)