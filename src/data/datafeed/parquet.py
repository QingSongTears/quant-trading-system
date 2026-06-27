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
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import polars as pl

from ...gateway import BarData, ContractData
from .base import (
    BaseDatafeed,
    Interval,
    NewsEvent,
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
        # 单只股票缓存 (P1-5 2026-06-26: 用 OrderedDict + maxsize 强制 LRU 淘汰)
        # 防止未来加缓存时无内存上限 (vnpy_vs_ours_deep_diff.md §3 #9-10 风险)
        # 现状: 此 dict 初始化但未写入 (旧代码残留), 0 内存占用
        # maxsize=512 足够缓存 ~50% 股票 (5000+ 只股票场景下不会爆内存)
        self._file_cache: "OrderedDict[str, pl.DataFrame]" = OrderedDict()
        self._file_cache_maxsize: int = 512  # P1-5: 防止 OOM

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

    def _cache_put(self, key: str, df: pl.DataFrame) -> None:
        """P1-5 (2026-06-26): 带 maxsize + 内存监控的安全缓存写入

        - 超 maxsize 自动 LRU 淘汰最旧条目
        - 估算 DataFrame 内存, 超过阈值时 warning
        """
        if key in self._file_cache:
            # 已有则移到末尾 (LRU 语义)
            self._file_cache.move_to_end(key)
            self._file_cache[key] = df
            return

        # 估算内存 (polars DataFrame.estimated_size() 单位 bytes, polars >= 0.20)
        try:
            size_bytes = df.estimated_size()
            size_mb = size_bytes / (1024 * 1024)
        except AttributeError:
            # 老版 polars 无 estimated_size, 粗估: 行数 × 100 bytes
            size_mb = df.height * 100 / (1024 * 1024)

        if size_mb > 50:
            logger.warning(
                f"parquet 缓存单文件 {key} 占用 {size_mb:.1f}MB, "
                f"超过 50MB 阈值, 建议改用 LocalDatafeed"
            )

        self._file_cache[key] = df

        # LRU 淘汰: 超过 maxsize 弹出最旧
        while len(self._file_cache) > self._file_cache_maxsize:
            evicted_key, _ = self._file_cache.popitem(last=False)
            logger.debug(f"parquet 缓存淘汰: {evicted_key}")

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

    # ── 基础数据抽象 (ADR-0010 新增) ──────────────
    # 注: ParquetDatafeed 主用全市场 scan + 特征工程,
    #     行业映射与研报事件走 stock_basic.parquet / research_report.parquet (若存在)。
    #     不存在时返回空 dict / 空 list (调用方需 fallback,见 ADR-0010 §D1)

    def get_industry_map(self, codes: List[str]) -> Dict[str, str]:
        """取股票-行业映射 (从 stock_basic.parquet 或 fallback 到 stock_list extra.industry)

        Parquet 单股票文件不存 industry 字段 (snapshot 仅 OHLCV),所以:
          - 优先读 self.parquet_dir/stock_basic.parquet (若存在)
          - 否则从 self.get_stock_list() 拿 ContractData.extra["industry"]
        """
        if not codes:
            return {}

        # 路径 A: stock_basic.parquet (若 build_parquet 导出过)
        sb_path = self.parquet_dir.parent / "stock_basic.parquet"
        if sb_path.exists():
            try:
                df = (
                    pl.scan_parquet(str(sb_path))
                    .filter(pl.col("code").is_in(list(codes)))
                    .select(["code", "industry"])
                    .collect()
                )
                return {row["code"]: (row["industry"] or "") for row in df.iter_rows(named=True)}
            except Exception as e:
                logger.warning(f"ParquetDatafeed.get_industry_map 走 stock_basic.parquet 失败: {e}")

        # 路径 B: 从 get_stock_list() 拿 extra.industry (兼容早期 parquet 导出)
        try:
            contracts = self.get_stock_list()
            ind_map: Dict[str, str] = {}
            target = set(codes)
            for c in contracts:
                if c.symbol in target:
                    extra = getattr(c, "extra", {}) or {}
                    ind_map[c.symbol] = extra.get("industry", "")
            return ind_map
        except Exception as e:
            logger.warning(f"ParquetDatafeed.get_industry_map fallback 失败: {e}")
            return {}

    def get_news_events(
        self,
        codes: List[str],
        start: date,
        end: date,
    ) -> List[NewsEvent]:
        """取研报 / 新闻事件 (从 research_report.parquet, 若存在)

        Parquet 默认导出不含 research_report (build_parquet.py 只导 daily);
        走 ParquetDatafeed 的调用方需先 build 该 parquet,否则返回空列表。
        """
        rr_path = self.parquet_dir.parent / "research_report.parquet"
        if not rr_path.exists():
            logger.debug("ParquetDatafeed.get_news_events: research_report.parquet 不存在,返回空")
            return []

        try:
            lf = pl.scan_parquet(str(rr_path)).filter(
                (pl.col("date") >= pl.lit(start))
                & (pl.col("date") <= pl.lit(end))
            )
            if codes:
                lf = lf.filter(pl.col("code").is_in(list(codes)))
            df = lf.sort("date", descending=True).collect()

            events: List[NewsEvent] = []
            for row in df.iter_rows(named=True):
                d = row.get("date")
                if isinstance(d, datetime):
                    d = d.date()
                events.append(NewsEvent(
                    code=str(row.get("code", "")),
                    date=d,
                    title=str(row.get("title") or ""),
                    rating=row.get("rating"),
                    rating_change=row.get("rating_change"),
                    author=row.get("author"),
                    institution=row.get("institution"),
                    url=row.get("url"),
                    source="research_report",
                ))
            return events
        except Exception as e:
            logger.warning(f"ParquetDatafeed.get_news_events 失败: {e}")
            return []

    def get_finance_snapshot(
        self,
        codes: List[str],
    ) -> Dict[str, Dict[str, float]]:
        """取财务快照 (从 finance_summary.parquet, 若存在)

        Parquet 默认导出不含 finance_summary,直接返回空 dict。
        调用方需自己 build 该 parquet 才能拿到数据。
        """
        if not codes:
            return {}
        fs_path = self.parquet_dir.parent / "finance_summary.parquet"
        if not fs_path.exists():
            logger.debug("ParquetDatafeed.get_finance_snapshot: finance_summary.parquet 不存在,返回空")
            return {}

        try:
            df = (
                pl.scan_parquet(str(fs_path))
                .filter(pl.col("code").is_in(list(codes)))
                .collect()
            )
            result: Dict[str, Dict[str, float]] = {}
            for row in df.iter_rows(named=True):
                code = str(row.get("code", ""))
                np_v = row.get("NPParentCompanyOwnersTTM")
                tse = row.get("TotalShareholderEquity")
                pe_ttm = None
                try:
                    if np_v is not None and tse is not None and abs(float(np_v)) > 0:
                        pe_ttm = float(tse) / abs(float(np_v))
                except Exception:
                    pe_ttm = None
                result[code] = {
                    "net_profit": float(np_v) if np_v is not None else None,
                    "pe_ttm": pe_ttm,
                }
            return result
        except Exception as e:
            logger.warning(f"ParquetDatafeed.get_finance_snapshot 失败: {e}")
            return {}

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