# DataManager 使用指南 (2026-06-24)

> **TL;DR**: 所有新代码访问数据, 一律用 `from src.data import data_mgr`. 老代码不强迁, 各取所需.

## 为什么需要 DataManager

之前数据接口散落 `src/data/downloader.py` / `westock.py` / `xgb_loader.py` / `datafeed/local.py` / `datafeed/parquet.py`, 调用方要写:

```python
from src.data.downloader import DataDownloader
from src.data.westock import get_kline
from src.data.xgb_loader import XgbV4Model
from src.data.xgb_scaler import load_scaler
from src.datafeed import LocalDatafeed  # 还在 src/datafeed/ 顶层
```

**问题**:
1. **5 个 import** vs 1 个 import
2. **路径散落** — 不知道哪个数据源在 `src/data/` 哪个在 `src/datafeed/` 顶层
3. **单例管理混乱** — XGBoost 单例要单独调 `get_xgb_v4()`, DataDownloader 每次 new
4. **改一个影响所有** — 重命名要改 5 个 import 路径

**DataManager 统一后**:
```python
from src.data import data_mgr

# 1. 历史数据下载
data_mgr.downloader.download_full()

# 2. 实时行情
kline = data_mgr.westock.get_kline("sh600000")

# 3. XGBoost 模型 (单例)
prob = data_mgr.model.predict_proba(X)

# 4. 特征缩放
sc = data_mgr.load_scaler("data/xgb_scaler.json")

# 5. vnpy 风格 Datafeed (默认 Local, 可切 Parquet)
bars = data_mgr.datafeed.get_bars("000001.SZ", "1d")
```

## 5 个 API 一览

| 属性 | 类型 | 初始化 | 用途 |
|------|------|--------|------|
| `data_mgr.downloader` | `DataDownloader` | lazy | 历史数据下载 (akshare/baostock) |
| `data_mgr.westock` | `westock` module | lazy | 实时行情函数 (`get_kline`/`get_technical`/...) |
| `data_mgr.model` | `XgbV4Model` (单例) | lazy | XGBoost 模型 |
| `data_mgr.datafeed` | `BaseDatafeed` (单例) | lazy | vnpy 风格行情查询 (默认 Local) |
| `data_mgr.load_scaler(path)` | `dict` | 立即 | 加载 JsonScaler |
| `data_mgr.save_scaler(sc, fn, path)` | `Path` | 立即 | 保存 JsonScaler |

## 何时用 data_mgr, 何时用具体类

### ✅ 用 `data_mgr` (推荐)

- **新写的代码** (新策略 / 新工具 / 新接口)
- **脚本/CLI 入口** (`scripts/foo.py`, `run.py`)
- **web 路由** (`src/web/routes/api.py`)
- **统一管理** — 想用单例 (model) 或可切换 (datafeed) 时
- **快速原型** — 不关心具体数据源在哪

### ⚠️ 直接用具体类 (允许)

- **单测** (`tests/test_*.py`) — 测单类行为, 显式 import 更清晰
- **策略层显式路径** (`v_leader_main_surge.py`) — `XgbV4Model.load(path, path)` 比 `data_mgr.model` 更显式
- **DataManager 自己的单测** — 用具体类 mock
- **历史代码** — 不强迁, 老 import 仍可用 (`from src.data.xgb_loader import XgbV4Model` 仍 work)

### ❌ 不要用 data_mgr

- **DataManager 内部** (`src/data/manager.py`) — 避免循环
- **Datafeed 子包内部** (`src/data/datafeed/*.py`) — 同上

## 切换 Datafeed (Local ↔ Parquet)

```python
from src.data.datafeed import set_datafeed_kind, get_datafeed
from src.data import data_mgr

# 切到 Parquet (全市场 scan 快)
set_datafeed_kind("parquet")
df = data_mgr.datafeed  # 触发新建 ParquetDatafeed
print(df)  # <ParquetDatafeed ...>

# 切回 Local (单股精确查询快)
set_datafeed_kind("local")
df = data_mgr.datafeed  # 触发新建 LocalDatafeed
```

选型参考 (实测, 2026-06-23):
- **全市场 scan / 特征工程 / ML** → Parquet (5-30x 快)
- **小 universe 回测 hot path / 单点查询** → Local (SQLite)

## 切换模型路径

```python
# 切到自定义模型 (热更新 / 测试)
data_mgr.set_model_paths(
    model_path="data/alt_xgb_model.json",
    scaler_path="data/alt_xgb_scaler.json",
)

# 下次访问 model 自动重载
model = data_mgr.model
```

## 测试 DataManager

```python
from src.data import DataManager, get_data_manager, data_mgr

def test_data_mgr_singleton():
    assert get_data_manager() is data_mgr

def test_data_mgr_lazy_downloader(monkeypatch):
    # 替换内部实现做隔离
    from src.data import manager
    fake = MagicMock()
    monkeypatch.setattr(manager, "DataDownloader", lambda: fake)
    # 触发 lazy
    assert data_mgr.downloader is fake
```

## 设计原则 (TL;DR)

1. **5 类数据源都 lazy** — 第一次访问才创建, 启动 0 开销
2. **单例 + 可切换** — model / datafeed 用单例 + setter 切换
3. **可 mock** — 内部用 instance attr 缓存, 单测可替换
4. **老 import 兼容** — `from src.data.xgb_loader import XgbV4Model` 仍 work
5. **DataManager 内部不引 DataManager** — 避免循环, 必要时用具体类
