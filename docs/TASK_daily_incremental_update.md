# 任务：每天一键增量更新数据

> 目的：给另一个 AI 完整的需求 + 背景 + 实现路径，**可直接接手开发**。
>
> 创建：2026-06-24 16:25
>
> 项目路径：`E:\work\work\quant-trading-system`
>
> 目标交付物：`scripts/daily_incremental_update.py`（一个 Python 脚本，CLI 可调用，cron 可定时）

---

## 1. 背景

### 1.1 项目是什么
量化交易系统，每天需要把行情/财务/资金流/股东等数据从外部数据源更新到本地 SQLite DB（`database/quant.db`，gitignore），供回测/选股策略使用。

### 1.2 当前 DB 现状（2026-06-24 16:16）
- DB size: **1667 MB**
- 13 张业务表 + 3 张事务表
- daily_price: **4,191,557 行**（max=2026-06-23, 2026-06-22/06-23 是刚补抓的）
- technical_indicators: 3,016,976 行（max=2026-06-16, **未增量补抓过**）
- fund_flow_data: 435,092 行（max=2026-06-18）
- block_trade: 54,700 行（max=2026-06-18）
- dividend: 40,377 行（max=2026-07-09）
- announcements: 4,616 行（max=2026-06-16）
- holder_num: 5,521 行（max=2026-06-18）
- benchmark_data: 9,000 行（max=2026-06-24, westock kline 6 指数）
- finance_summary: 28,888 行（max=2024-12-31, baostock 2015-2017Q1）
- stock_basic: 5,569 行（沪深北三市 + 退市股占位）
- stock_profile: 5,565 行（含 circulating_shares 5083/5205 = 97.7%）

### 1.3 数据源（实测 2026-06-24）
| 数据源 | 网络 | 用于 | 调用方式 | 速度 |
|---|---|---|---|---|
| **westock** (腾讯自选股 npm CLI `westock-data-clawhub@1.0.4`) | ✅ 通 | 行情 / K 线 / 公司简况 / 财务摘要 | `npx.cmd westock-data-clawhub@1.0.4 <subcmd> <codes> --flags`，支持逗号批量 | 批量 50/批 × 5s ≈ 8 分钟/天 |
| **baostock** 0.9.10 | ✅ 通（但**频繁限流**：WinError 10054） | 财务三表 / 行业 | `bs.query_*` Python SDK，**无 batch API** | 单股循环 12-15 股/s |
| **akshare** 1.18.64 | ❌ RemoteDisconnected | 备用 | 当前网络断开 | — |
| **tencent_quotes.csv** + **fund_flow_120d.csv** 等 | 本地 CSV | 行情快照 / 资金流 | 读 `market_data/` 下的 CSV | 即时 |

### 1.4 已有的"数据更新脚本"
| 脚本 | 增量方式 | 备注 |
|---|---|---|
| `build_db.py --incremental` | CSV→DB，按 max(trade_date) 过滤 | 主入口，14 个 importer |
| `build_parquet.py --incremental` | 跳过已存在的 parquet | 行情派生层 |
| `fill_recent_klines.py` | 自动跳已有 `(code, trade_date)` | **westock 增量拉行情**（核心） |
| `add_bj_stocks.py` | 收集孤儿 code → westock profile | 新上市的北交所/小盘股 |
| `consolidate_db.py` | INSERT OR IGNORE | CSV 批量入库 |
| `import_from_westock_baostock_akshare.py --task finance --start-year Y --end-year Y` | 增量跳过 `(code, stat_date)` | baostock 多年财务 loop |
| `import_more_csv.py --incremental` | 跳已 import 的 CSV | — |
| `fill_circulating_shares.py` | 跑一次即可 | baostock liqa_share → stock_profile |

**没有统一的"一键增量"入口**——必须用户决定跑哪些。

---

## 2. 任务目标

**创建 `scripts/daily_incremental_update.py`**：一个 CLI 工具，做以下事：

### 2.1 数据源优先级与增量策略

| # | 数据表 | 数据源 | 增量策略 | 何时跑 |
|---|---|---|---|---|
| 1 | `daily_price` | westock kline (批量 50/批) | `max(trade_date) + 1` → 今天 | **每次跑必做** |
| 2 | `technical_indicators` | westock technical 批量 | `max(trade_date) + 1` → 今天 | **每次跑必做** |
| 3 | `fund_flow_data` | westock asfund (批量) | `max(trade_date) + 1` → 今天 | **每次跑必做** |
| 4 | `block_trade` | westock blocktrade 单股 | `max(trade_date) + 1` → 今天 | **每次跑必做** |
| 5 | `dividend` | westock dividend (按股) | INSERT OR IGNORE（dividend 是事件型，无 max date 增量）| **每天跑** |
| 6 | `announcements` | westock 暂时**无专门 subcmd**，使用本地 CSV `tencent_quotes.csv` 兜底 | 同上 | **每周一次** |
| 7 | `holder_num` | westock shareholder 单股 | INSERT OR IGNORE（事件型）| **每周一次** |
| 8 | `benchmark_data` | westock kline（指数） | `max(trade_date) + 1` → 今天 | **每次跑必做** |
| 9 | `finance_summary` | baostock profit_data 当前季度 | 当前 quarter 已存在则跳过 | **每季度跑一次** |
| 10 | `stock_basic` | westock profile（**新上市股**） | 当前 size vs 历史 size，**新出现**的 code 插入 | **每周一次** |
| 11 | `stock_profile` | westock profile | 同 #10 | **每周一次** |
| 12 | `stock_profile.circulating_shares` | baostock liqa_share (从 finance_summary 取最新) | 已存在则跳过 | **每季度跑一次** |
| 13 | 退市股标记 | westock suspension + name LIKE '%退%' | 仅"标记"操作，UPDATE delist_date | **每周一次** |

### 2.2 CLI 接口

```bash
# 默认: 跑所有 1-8（高频数据）
python scripts/daily_incremental_update.py

# 跑全量含低频
python scripts/daily_incremental_update.py --full

# 跑指定表
python scripts/daily_incremental_update.py --task daily_price --task technical_indicators

# Dry-run (不 INSERT, 只打印计划)
python scripts/daily_incremental_update.py --dry-run

# 指定日期 (默认: 今天)
python scripts/daily_incremental_update.py --date 2026-06-24

# 后台跑 (用 CREATE_NO_WINDOW 不弹 cmd 窗口)
python scripts/daily_incremental_update.py --background
```

### 2.3 输出报告

每张表完成后打印：
```
[OK] daily_price: 5,200 行 (6-22 ~ 6-24), 耗时 8.5min, 累计 4,196,757 行
[SKIP] finance_summary: 当前季度 2024Q4 已存在, 跳过 (baostock 限流中)
[FAIL] technical_indicators: westock 失败 12 批/105, 重试 3 次仍失败, 跳过
```

最终汇总:
```
========================================
📊 增量更新汇总 (2026-06-24)
========================================
✅ daily_price           +5,200 行   8.5min
✅ technical_indicators  +0 行     (跳过: 已是最新)
⏭️ fund_flow_data        +0 行     (跳过: 已是最新)
...
========================================
耗时: 12.3 min | DB size: 1667MB → 1669MB (+2MB)
下次计划: 2026-06-25
```

### 2.4 异常处理

- **westock 网络超时**: 单只 retry 3 次 (backoff 1s/2s/4s)，整批 retry 1 次，再 fail 则标记 SKIP 继续跑其他表
- **baostock 限流 WinError 10054**: 单股 retry 3 次，连续 20 错误退避 30s，仍 fail 则标记 SKIP
- **INSERT OR IGNORE 冲突**: 不报错，跳过累计
- **CSV 文件缺失**: 标记 WARN，跳过该表
- **进程被 kill / 异常退出**: 已有数据已 commit，下次跑自动从 max+1 续跑（**幂等**）

---

## 3. 实现路径

### 3.1 文件位置与命名

**`E:\work\work\quant-trading-system\scripts\daily_incremental_update.py`**

### 3.2 代码骨架（参考，非完整）

```python
#!/usr/bin/env python3
"""
daily_incremental_update.py — 每天一键增量更新所有数据到 quant.db

用法:
  python scripts/daily_incremental_update.py                 # 默认 (高频任务)
  python scripts/daily_incremental_update.py --full          # 含低频 (退市股/财务等)
  python scripts/daily_incremental_update.py --task X --task Y  # 指定表
  python scripts/daily_incremental_update.py --date 2026-06-24  # 指定日期
  python scripts/daily_incremental_update.py --dry-run
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

DB_PATH = PROJECT_ROOT / "database" / "quant.db"

# 全部任务的元数据
TASKS = {
    "daily_price": {
        "source": "westock kline (BATCH=50)",
        "freq": "high",
        "mode": "max_date+1",
        "runner": "_run_westock_kline_table",
    },
    "technical_indicators": {
        "source": "westock technical (BATCH=50)",
        "freq": "high",
        "mode": "max_date+1",
        "runner": "_run_westock_tech_table",
    },
    # ... 13 个任务
}

def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--task", action="append", choices=list(TASKS))
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--full", action="store_true", help="包含所有 freq=low 任务")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()

    # 1. 连接 DB
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 2. 确定目标日期
    target_date = args.date or date.today().strftime("%Y-%m-%d")

    # 3. 确定要跑的任务
    if args.task:
        tasks = args.task
    elif args.full:
        tasks = list(TASKS.keys())
    else:
        tasks = [k for k, v in TASKS.items() if v["freq"] == "high"]

    # 4. 逐任务跑
    results = []
    for task in tasks:
        try:
            n = run_task(task, target_date, cur, args)
            results.append((task, "OK", n))
        except Exception as e:
            results.append((task, "FAIL", str(e)))

    # 5. 汇总报告
    print_summary(results)
    conn.close()


def run_task(task, target_date, cur, args):
    """调度单个任务"""
    runner = globals()[TASKS[task]["runner"]]
    return runner(task, target_date, cur, args)


def _run_westock_kline_table(task, target_date, cur, args):
    """示例: daily_price 增量"""
    from fill_recent_klines import fetch_batch_kline, _f, _i

    # 查 max date
    cur.execute(f"SELECT MAX(trade_date) FROM {task}")
    max_date = cur.fetchone()[0] or "2023-01-01"
    start = (date.fromisoformat(max_date) + timedelta(days=1)).strftime("%Y-%m-%d")
    if start > target_date:
        return 0  # 已是最新

    # 拉取 stock 列表
    from import_from_westock_baostock_akshare import load_tradable_codes
    pairs = load_tradable_codes()
    pairs = [(c, m) for c, m in pairs if c.isdigit() and len(c) == 6]

    # 批量拉取
    days_to_fetch = []
    d = date.fromisoformat(start)
    while d <= date.fromisoformat(target_date):
        days_to_fetch.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    inserted = 0
    for d in days_to_fetch:
        # 按 50/批 拉
        BATCH = 50
        for i in range(0, len(pairs), BATCH):
            batch = pairs[i:i+BATCH]
            ws_codes = [f"{m}{c}" for c, m in batch]
            rows = fetch_batch_kline(ws_codes, date=d)
            # INSERT OR IGNORE
            # ... (照搬 fill_recent_klines 逻辑)
            # ...
    return inserted
```

### 3.3 必须遵守的约束

1. **Windows cmd 弹窗**: 所有 `subprocess.run` 调 `npx.cmd` / `npx` 必须加 `creationflags=CREATE_NO_WINDOW (0x08000000)`。参考：
   ```python
   CREATE_NO_WINDOW = 0x08000000
   subprocess.run(cmd, ..., creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
   ```
   （已有 westock_batch / fill_recent_klines 已修复，可直接复用函数）

2. **幂等性**: 所有 INSERT 必须 `INSERT OR IGNORE` + UNIQUE 约束

3. **westock 限流**: baostock 限流时退避（**fill_recent_klines / import_from_westock_baostock 已实现**，可直接复用 `fetch_batch_kline` 和 `task_finance` 函数）

4. **白名单校验**: westock 子命令入参（code）必须 `^[a-z]{2}\d{6}$`，防命令注入

5. **BG 子进程**: 如果 `--background`，用 `subprocess.Popen` + `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW`，主进程立即返回，BG 独立运行

### 3.4 可复用函数

| 函数 | 来源脚本 | 用途 |
|---|---|---|
| `westock_batch(symbols, subcommand, *flags)` | `import_from_westock_baostock_akshare.py` | 通用 westock 批量调用，自动白名单 + 隐藏窗口 |
| `fetch_batch_kline(ws_codes, date)` | `fill_recent_klines.py` | westock kline 批量 + 日期过滤 + 隐藏窗口 |
| `load_tradable_codes()` | `import_from_westock_baostock_akshare.py` | 从 tencent_quotes.csv 读 (code, market) 列表 |
| `parse_markdown_table(text)` | `import_from_westock_baostock_akshare.py` | westock markdown 输出解析 |
| `task_finance(...)` | `import_from_westock_baostock_akshare.py` | baostock 财务 loop + retry/backoff |
| `_to_float(s)` / `_to_int(s)` | `consolidate_db.py` / `fill_recent_klines.py` | 宽容处理 `'-'` / `'nan'` 等空值字符串 |

### 3.5 数据库 schema 参考

DB schema 见 `docs/DATA_SCHEMA.md`，关键点：
- `daily_price(code, trade_date)` UNIQUE
- `technical_indicators(code, trade_date)` UNIQUE
- `fund_flow_data(code, trade_date)` UNIQUE
- `benchmark_data(index_code, trade_date)` UNIQUE
- `finance_summary(code, _date)` UNIQUE
- 所有表的 `code` 字段是 6 位数字（不带 sh/sz 前缀）
- 日期字段全部 `YYYY-MM-DD` 字符串

---

## 4. 边界条件

1. **网络断开**: westock / baostock 任一断开，相关任务 SKIP，**继续跑其他任务**
2. **交易日判断**: 跳过周末 / 节假日（用户不会传周末日期，但脚本要防御）
3. **BG 被打断**: 已有数据已 commit（每张表 commit 一次），下次跑自动续
4. **重复运行**: 完全幂等（INSERT OR IGNORE + max(date) + 1）
5. **DB 文件不存在**: 友好报错，提示先跑 `build_db.py`
6. **数据 CSV 已更新但代码未更新**: 增量模式按 max date 过滤，自动跳过旧数据
7. **数据库 schema 不匹配**: 通过 `PRAGMA table_info` 检查关键列，不匹配则报错

---

## 5. 输出要求

### 5.1 日志

- 输出到 stdout + 文件 `output/daily_incremental_<date>.log`
- INFO 级别：每张表的状态
- ERROR 级别：westock / baostock 网络错误
- WARNING 级别：CSV 缺失 / 数据缺失

### 5.2 退出码

- `0`: 所有任务 OK
- `1`: 部分任务 FAIL（但其他任务完成）
- `2`: 数据库连接失败 / schema 不匹配

### 5.3 报告

- 每次跑完生成 Markdown 报告 `docs/daily_updates/<date>.md`
- 包含：每个表的状态 / 行数变化 / 耗时 / 异常 / 下次计划

---

## 6. 验收标准

1. ✅ `python scripts/daily_incremental_update.py` 一键运行全部高频任务
2. ✅ 6-24/6-25/6-26 连续跑 3 天，DB 数据每日推进
3. ✅ 没有 cmd 黑窗弹出
4. ✅ 任何任务失败不影响其他任务
5. ✅ 重跑同一日期是幂等的（行数不增加）
6. ✅ 生成的报告清晰可读
7. ✅ 处理边界（网络断 / CSV 缺失 / 周末）不崩溃

---

## 7. 关联文档

- `docs/DATA_SCHEMA.md` — DB 完整字段定义
- `docs/DATA_STATUS.md` — 当前 DB 状态
- `LIVE_TRADING_ROADMAP.md` — 实盘化主路线图
- `scripts/build_db.py` — 14 importer 统一入口（全量/增量）
- `scripts/fill_recent_klines.py` — 行情增量核心实现（参考）
- `scripts/import_from_westock_baostock_akshare.py` — westock/baostock 通用 wrapper

---

## 8. 时间预估

- 实现 + 测试：2-4 小时（中等复杂度，主要时间在调通 13 个任务的 runner）
- 跑一次（无缺失数据）：5-15 分钟
- 跑一次（缺失 5+ 天）：30-60 分钟

---

*创建：2026-06-24 16:30*
*交接给另一个 AI 时附上本文件即可*
*不依赖任何额外上下文，可独立完成*