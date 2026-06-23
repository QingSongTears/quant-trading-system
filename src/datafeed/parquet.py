"""
ParquetDatafeed — 从本地 parquet 文件读行情数据 (vnpy 风格)

数据源: market_data/parquet/daily/{vt_symbol}.parquet
每个文件 ~22 KB (5000+ 个文件, 总 223 MB)

优势 vs LocalDatafeed (SQLite):
  - 列存 + 强类型, pyarrow 读快
  - 无 SQL 解析开销
  - 全局 cache 友好 (整个 dataset 在 pyarrow 内存)
  - 列裁剪 (只读需要的列)

适用场景:
  - 大量 scan + filter 的回测
  - ML 特征工程 (polars/pandas)
  - 数据分析/可视化

不适用:
  - 单条精确查询 (SQLite 更快)
  - 事务型 (订单/账户) - 仍然走 SQLite
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import pandas as pd

from ..gateway import BarData, ContractData
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
    Parquet 数据源 (vnpy 风格)

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
        self._file_cache: Dict[str, pd.DataFrame] = {}  # 单只股票缓存
        self._dataset_cache: Optional[pd.DataFrame] = None  # 全市场缓存

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
        self._dataset_cache = None
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

        # 单股票直接读文件
        path = self.parquet_dir / f"{vt_symbol}.parquet"
        if not path.exists():
            logger.warning(f"parquet 文件不存在: {path}")
            return []

        df = self._read_file(path)

        # 应用过滤
        if start is not None:
            df = df[df["datetime"] >= pd.Timestamp(start)]
        if end is not None:
            df = df[df["datetime"] <= pd.Timestamp(end)]
        if count is not None and start is None and end is None:
            df = df.tail(count)

        return self._df_to_bars(df)

    # ── 取全市场合约列表 ──────────────────────────────

    def get_stock_list(self) -> List[ContractData]:
        if not self.inited:
            self.init()
        from ..gateway import ContractData

        # 一次读所有 parquet, 取 distinct symbol
        files = list(self.parquet_dir.glob("*.parquet"))
        symbols = set()
        for f in files:
            df = self._read_file(f)
            if not df.empty:
                symbols.add((df["symbol"].iloc[0], df["exchange"].iloc[0], df["name"].iloc[0] if "name" in df.columns else ""))

        contracts = []
        for symbol, exchange, name in symbols:
            c = ContractData(
                gateway_name="PARQUET",
                symbol=symbol,
                exchange=exchange,
                name=name or "",
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

        优化: 不预先 cold load 全市场
          - 有 universe: 只读 universe 范围内的文件 (lazy)
          - 无 universe: cold load 全市场 (一次性成本)
        """
        if not self.inited:
            self.init()
        if interval != Interval.DAY_1:
            return super().get_bars_by_date(query_date, universe, interval)

        target_date = pd.Timestamp(query_date)

        # 路径 A: 有 universe → 只读这些股票的文件
        if universe:
            result: Dict[str, BarData] = {}
            for vt_symbol in universe:
                path = self.parquet_dir / f"{vt_symbol}.parquet"
                if not path.exists():
                    continue
                df = self._read_file(path)
                df = df[df["datetime"] == target_date]
                if df.empty:
                    continue
                row = df.iloc[0]
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

        # 路径 B: 无 universe → cold load 全市场 + filter
        if self._dataset_cache is None:
            self._load_full_dataset()

        if self._dataset_cache is None or self._dataset_cache.empty:
            return {}

        df = self._dataset_cache[self._dataset_cache["datetime"] == target_date]

        result = {}
        for _, row in df.iterrows():
            vt_symbol = row["vt_symbol"]
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
        if self._dataset_cache is None:
            self._load_full_dataset()

        if self._dataset_cache is None or self._dataset_cache.empty:
            return []

        df = self._dataset_cache[
            (self._dataset_cache["datetime"] >= pd.Timestamp(start))
            & (self._dataset_cache["datetime"] <= pd.Timestamp(end))
        ]
        return sorted(set(df["datetime"].dt.date.tolist()))

    # ── 内部方法 ──────────────────────────────────────

    def _read_file(self, path: Path) -> pd.DataFrame:
        """读单只股票的 parquet (带 cache)"""
        key = str(path)
        if key not in self._file_cache:
            self._file_cache[key] = pd.read_parquet(path, engine="pyarrow")
        return self._file_cache[key].copy()

    def _load_full_dataset(self) -> None:
        """一次性加载全部 parquet 到内存 (pyarrow 快速)"""
        t0 = time.time()
        files = list(self.parquet_dir.glob("*.parquet"))
        if not files:
            logger.warning(f"parquet 目录为空: {self.parquet_dir}")
            return

        # 用 pyarrow dataset 一次性 read (column pruning 可选)
        try:
            import pyarrow.dataset as pds
            ds = pds.dataset(str(self.parquet_dir), format="parquet")
            # 只读需要的列, 省内存
            table = ds.to_table(columns=[
                "symbol", "exchange", "name", "datetime", "interval",
                "open_price", "high_price", "low_price", "close_price",
                "volume", "turnover",
            ])
            self._dataset_cache = table.to_pandas()
        except Exception as e:
            logger.warning(f"pyarrow.dataset 失败 ({e}), fallback to concat")
            # fallback: 逐文件读再 concat
            dfs = [self._read_file(f) for f in files]
            self._dataset_cache = pd.concat(dfs, ignore_index=True)

        # 添加 vt_symbol 列
        self._dataset_cache["vt_symbol"] = (
            self._dataset_cache["symbol"] + "." + self._dataset_cache["exchange"]
        )

        logger.info(
            f"全市场 parquet 加载完成: {len(self._dataset_cache):,} 行, "
            f"{self._dataset_cache['vt_symbol'].nunique()} 只股票, "
            f"耗时 {time.time()-t0:.1f}s"
        )

    @staticmethod
    def _df_to_bars(df: pd.DataFrame) -> List[BarData]:
        """DataFrame → List[BarData]"""
        bars = []
        for _, row in df.iterrows():
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