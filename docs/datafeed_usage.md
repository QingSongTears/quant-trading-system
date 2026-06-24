# Datafeed 使用指南 (2026-06-25)

> **TL;DR**: `BaseDatafeed` 是 vnpy 风格数据源抽象, 本项目有 `LocalDatafeed` (SQLite, 主用) 和 `ParquetDatafeed` (全市场 scan 快) 两种实现。

## 两种 Datafeed 对比

| 维度 | LocalDatafeed (SQLite) | ParquetDatafeed (polars) |
|------|------------------------|--------------------------|
| **数据源** | `database/quant.db` (SQLite) | `market_data/parquet/daily/*.parquet` |
| **优势** | 单股精确查询快 (10ms), 小 universe 友好 | 全市场某日 / 大批量列扫描极快 (1-30x) |
| **劣势** | 全市场某日慢 (6s vs Parquet 1.1s) | 小 universe 跨 10k+ 文件 metadata 开销大 |
| **支持 interval** | 仅 `1d` (日 K) | 仅 `1d` (日 K) |
| **数据准备** | `python scripts/build_db.py` | `python scripts/build_parquet.py` |

**选型建议** (实测, 2026-06-23):
- **回测 / 精确单股查询** → **LocalDatafeed** (SQLite)
- **特征工程 / ML / 全市场某日** → **ParquetDatafeed**

## 统一接口 (BaseDatafeed)

```python
class BaseDatafeed(ABC):
    @abstractmethod
    def get_bars(self, vt_symbol, interval, start, end, count) -> List[BarData]:
        """取历史 K 线"""

    @abstractmethod
    def get_stock_list(self) -> List[ContractData]:
        """取全市场合约列表"""

    def get_bars_by_date(self, query_date, universe, interval) -> Dict[str, BarData]:
        """给定日期批量取 BarData (回测 hot path)"""

    def get_trading_calendar(self, start, end) -> List[date]:
        """取交易日历"""
```

## 通过 DataManager 访问 (推荐)

```python
from src.data import data_mgr

# 默认 Local (SQLite)
bars = data_mgr.datafeed.get_bars("000001.SZ", "1d", start=date(2024, 1, 1))
contracts = data_mgr.datafeed.get_stock_list()
calendar = data_mgr.datafeed.get_trading_calendar(date(2024, 1, 1), date(2024, 6, 1))
```

**特点**: `data_mgr.datafeed` 是单例 + lazy, 首次访问触发 init。

## 切换到 ParquetDatafeed

```python
from src.data.datafeed import set_datafeed_kind, get_datafeed

# 切到 parquet (适合全市场 scan)
set_datafeed_kind("parquet")

# 通过 data_mgr 访问 (会触发新建 ParquetDatafeed)
bars = data_mgr.datafeed.get_bars("000001.SZ", "1d", start=date(2024, 1, 1))

# 或直接拿
df = get_datafeed("parquet")
contracts = df.get_stock_list()
```

**注意**: 切换是**全局**的, 用于一次性脚本 (如特征工程), 不适合多线程。

## 直接实例化 (单测 / 特殊场景)

```python
from src.data.datafeed import LocalDatafeed, ParquetDatafeed

# 适合单测和特殊定制
df = LocalDatafeed()
df.init()
bars = df.get_bars("000001.SZ", "1d", start=date(2024, 1, 1))
df.close()
```

## 数据准备

**首次使用必须先建库**:

```bash
# 1. 准备 market_data/raw/*.csv (从 westock/baostock/akshare 下载)
# 2. 构建 SQLite
python scripts/build_db.py

# 3. (可选) 构建 Parquet
python scripts/build_parquet.py
```

**若未建库, 会得到**:
- `LocalDatafeed` → `OperationalError: no such table: daily_price`
- `ParquetDatafeed` → `FileNotFoundError: parquet 目录不存在`

## A 股市场代码映射

```python
from src.data.datafeed import code_to_market, code_to_vt_symbol

code_to_market("000001")   # → "SZ" (深市)
code_to_market("600519")   # → "SH" (沪市主板)
code_to_market("688981")   # → "SH" (科创板)
code_to_market("830799")   # → "BJ" (北交所)
code_to_vt_symbol("000001")  # → "000001.SZ"
```

## 批量取某日 (回测 hot path)

```python
from src.data import data_mgr
from datetime import date

# 取 2024-06-24 全市场 K 线 (Dict[vt_symbol, BarData])
bars_dict = data_mgr.datafeed.get_bars_by_date(
    date(2024, 6, 24),
    universe=None,  # None = 全市场
    interval="1d",
)
print(f"当日有数据的股票数: {len(bars_dict)}")

# 取指定股票池
universe = ["000001.SZ", "600519.SH", "830799.BJ"]
bars_dict = data_mgr.datafeed.get_bars_by_date(
    date(2024, 6, 24),
    universe=universe,
    interval="1d",
)
```

## 交易日历

```python
from src.data import data_mgr
from datetime import date

calendar = data_mgr.datafeed.get_trading_calendar(
    date(2024, 1, 1), date(2024, 6, 1),
)
# 返回 [date(2024, 1, 2), date(2024, 1, 3), ...]
print(f"上半年交易日数: {len(calendar)}")  # ~119
```

## 限制

- **仅支持日 K (1d)**: 分钟/小时 K 暂未实现, 调用抛 `NotImplementedError`
- **vt_symbol.exchange 校验**: `LocalDatafeed` 会 warning 不匹配 (不抛)
- **OTHER 市场过滤**: `get_stock_list()` 静默过滤掉非 SH/SZ/BJ 的代码

## 错误处理

```python
# 不存在的股票
bars = data_mgr.datafeed.get_bars("000000.SZ", "1d")
# → []  (空列表, 不抛)

# 非日 K
bars = data_mgr.datafeed.get_bars("000001.SZ", "1m")
# → NotImplementedError: LocalDatafeed 仅支持日 K (1d)

# 数据不足
bars = data_mgr.datafeed.get_bars("000001.SZ", "1d", start=date(2030, 1, 1))
# → []  (空列表)
```

## 架构位置

```
src/data/
├── __init__.py             # 公共 API
├── manager.py              # DataManager 统一门面
├── datafeed/
│   ├── __init__.py         # get_datafeed() / set_datafeed_kind() 工厂
│   ├── base.py             # BaseDatafeed (ABC) + Interval 常量
│   ├── local.py            # LocalDatafeed (SQLite)
│   └── parquet.py          # ParquetDatafeed (polars)
└── ...
```

## 与 vnpy 4.4 差异

| 维度 | vnpy 4.4 | 本项目 |
|------|----------|--------|
| 多数据源 | 支持任意 (DB/CSV/API) | 2 个 (Local/Parquet) |
| 分钟 K | 支持 | 未实现 (日 K 优先) |
| Tick 合成 | 支持 | 未实现 (A 股不适用) |
| 缓存层 | 无 | 无 (依赖 DB 索引 / polars 优化) |
