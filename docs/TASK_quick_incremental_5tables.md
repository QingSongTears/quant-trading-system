# 任务：补全 5 张表到 2026-06-23（立即可执行）

> 目的：给另一个 AI 完整的需求 + 背景 + 实施步骤，**可直接接手执行**。
>
> 创建：2026-06-24 16:30
>
> 项目路径：`E:\work\work\quant-trading-system`
>
> 关联文档：`docs/TASK_daily_incremental_update.md`（这是"长期通用脚本"任务；本任务是"立即补齐缺口"的短期任务）

---

## 1. 背景

### 1.1 项目背景
量化交易系统，每天把行情/财务/资金流等数据从外部数据源更新到本地 SQLite DB（`database/quant.db`），供回测/选股策略使用。

### 1.2 当前 DB 状态（2026-06-24 16:20 巡检）

| 表 | 行数 | max(date) | 与 2026-06-23 差距 | 状态 |
|---|---|---|---|---|
| `daily_price` | 4,191,557 | **2026-06-23** | 0 | ✅ 已最新 |
| `benchmark_data` | 9,000 | 2026-06-24 | — | ✅ 已是最新 |
| `technical_indicators` | 3,016,976 | 2026-06-16 | **差 7 天**（6-17~6-23） | ❌ 需补 |
| `fund_flow_data` | 435,092 | 2026-06-18 | **差 5 天**（6-19~6-23） | ❌ 需补 |
| `block_trade` | 54,700 | 2026-06-18 | **差 5 天** | ❌ 需补 |
| `holder_num` | 5,521 | 2026-06-18 | **差 5 天** | ❌ 需补 |
| `announcements` | 4,616 | 2026-06-16 | **差 7 天** | ❌ 需补 |
| `dividend` | 40,377 | 2026-07-09 | (未来日期正常) | ✅ 已是最新 |
| `stock_basic` | 5,569 | — | — | ✅ |
| `stock_profile` | 5,565 | — | — | ✅ |
| `finance_summary` | 28,888 | 2017-03-31 | **缺 2017Q2-2024Q4** | ⛔ baostock 黑名单阻塞 |

**核心问题**：除 daily_price 外，**5 张业务表都缺 5-7 天的数据**，导致回测/选股时这些表的数据切片不一致。

### 1.3 数据源选型（2026-06-24 实测）

| 数据源 | 状态 | 用于 |
|---|---|---|
| **westock** (腾讯自选股 npm CLI `westock-data-clawhub@1.0.4`) | ✅ 通 | 行情 / K 线 / 技术指标 / 公司简况 / 资金流 / 股东 / 大宗交易 / 分红 |
| **baostock 0.9.10** | ❌ **账号被黑名单** (login failed!) | ~~财务三表 / 行业~~ — **当前禁用** |
| **akshare 1.18.64** | ❌ RemoteDisconnected | ~~备用~~ — 当前网络断 |
| **本地 CSV** (`market_data/*.csv`) | ✅ | 兜底（fund_flow_120d.csv 滚动 120 天、tencent_quotes.csv 行情快照等） |

**关键约束**：baostock 当前账号已被封（错误信息："黑名单用户，请与管理员联系"），**所有 baostock 任务 SKIP**。本文档只走 westock 路线。

### 1.4 westock 能力（实测）

通过 `npx.cmd westock-data-clawhub@1.0.4 <subcmd>` 调用，已实测可用的子命令：

| westock subcmd | 输出字段 | 用途 |
|---|---|---|
| `kline` | `symbol, date, open, last, high, low, volume, amount, exchange` | 日 K 线（daily_price 增量） |
| `technical` | `symbol, date, MA5/10/20/60, MACD*, KDJ*, BOLL*, RSI6/12/24, CCI, WR, BIAS, ...` | 技术指标（technical_indicators 增量） |
| `asfund` | `code, date, shrm, sjlm, xjll, hsgt, jjjl, ...` | 资金流向（fund_flow_data 增量） |
| `blocktrade` | `code, date, price, volume, amount, buyer, seller, ...` | 大宗交易（block_trade 增量） |
| `shareholder` | `code, annDate, holderNum, top10holderShare, ...` | 股东人数（holder_num 增量） |
| `dividend` | `code, exDate, recordDate, payDate, stkeDiv, cashDiv, ...` | 分红（事件型） |
| `announce` | `code, date, title, type, url, ...` | 公告（announcements 增量） |
| `profile` | `code, name, listedDate, industry, sector, regCapital, ...` | 公司简况 |

调用模式（参考 `scripts/fill_recent_klines.py`）：
- **批量 50/批**（westock 限制）
- 50 codes 一次性拉最近 5 天 ≈ 5s
- 一次失败 retry 3 次（backoff 1s/2s/4s）
- 整批 fail 标记 SKIP，继续跑其他批

---

## 2. 任务目标

**补全 5 张表到 2026-06-23**：

| # | 表 | 增量区间 | 数据源 | 预计耗时 |
|---|---|---|---|---|
| 1 | `technical_indicators` | 2026-06-17 ~ 2026-06-23 (7 天) | westock `technical` | ~10-15 min |
| 2 | `fund_flow_data` | 2026-06-19 ~ 2026-06-23 (5 天) | westock `asfund` | ~8-12 min |
| 3 | `block_trade` | 2026-06-19 ~ 2026-06-23 (5 天) | westock `blocktrade` | ~10-15 min |
| 4 | `holder_num` | 2026-06-19 ~ 2026-06-23 (5 天) | westock `shareholder` | ~15-20 min |
| 5 | `announcements` | 2026-06-17 ~ 2026-06-23 (7 天) | westock `announce` | ~15-20 min |

**总耗时估算**：~60-90 min（**全部并行不可行**，westock 是 CLI 进程级别限流；必须**串行**）

**不动**：
- `finance_summary`（baostock 黑名单阻塞，**等几天自动解除**或换数据源，**不在本任务范围**）
- `dividend`（已是最新）
- `daily_price` / `benchmark_data`（已是最新）

---

## 3. 实现路径

### 3.1 立即可执行的方案：基于现有脚本的轻量 wrapper

**核心思想**：参考 `fill_recent_klines.py` 的成功模式（westock 批量 + UNIQUE 幂等 + CREATE_NO_WINDOW），写一个**通用**的 `fill_recent_<table>.py` 工具，每次调用指定表名 + 增量区间。

### 3.2 文件位置

**`E:\work\work\quant-trading-system\scripts\fill_recent_generic.py`**

### 3.3 关键代码骨架

```python
#!/usr/bin/env python3
"""
fill_recent_generic.py — 通用 westock 增量补全工具

用法:
  # 5 张表全部补到 6-23
  python scripts/fill_recent_generic.py

  # 单表指定日期
  python scripts/fill_recent_generic.py --table technical_indicators --start 2026-06-17 --end 2026-06-23

  # Dry-run
  python scripts/fill_recent_generic.py --dry-run

  # 后台
  python scripts/fill_recent_generic.py --background
"""
from __future__ import annotations
import argparse
import logging
import re
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
LOG_DIR = Path(r"C:\Users\admin\AppData\Local\Temp")
LOG_DIR.mkdir(parents=True, exist_ok=True)

CREATE_NO_WINDOW = 0x08000000

# 任务表元数据
TASKS = {
    "technical_indicators": {
        "westock_subcmd": "technical",
        "primary_key": ["code", "trade_date"],
        "date_field": "trade_date",
        "date_range": ("2026-06-17", "2026-06-23"),
        "table_columns": "...",  # 见 fill_recent_klines 同款 INSERT OR IGNORE
    },
    "fund_flow_data": {
        "westock_subcmd": "asfund",
        "primary_key": ["code", "trade_date"],
        "date_field": "trade_date",
        "date_range": ("2026-06-19", "2026-06-23"),
        "table_columns": "...",
    },
    "block_trade": {
        "westock_subcmd": "blocktrade",
        "primary_key": ["code", "trade_date", "price", "amount"],
        "date_field": "trade_date",
        "date_range": ("2026-06-19", "2026-06-23"),
        "table_columns": "...",
    },
    "holder_num": {
        "westock_subcmd": "shareholder",
        "primary_key": ["code", "ann_date"],
        "date_field": "ann_date",
        "date_range": ("2026-06-19", "2026-06-23"),
        "table_columns": "...",
    },
    "announcements": {
        "westock_subcmd": "announce",
        "primary_key": ["code", "trade_date", "title"],
        "date_field": "trade_date",
        "date_range": ("2026-06-17", "2026-06-23"),
        "table_columns": "...",
    },
}

# 字段映射：westock 输出 → DB 字段名
COLUMN_MAPS = {
    "technical_indicators": {
        "symbol": "code", "date": "trade_date",
        "MA5": "ma5", "MA10": "ma10", "MA20": "ma20", "MA60": "ma60",
        "MACD": "macd", "MACDdiff": "macd_diff", "MACDdea": "macd_dea",
        "KDJ_K": "kdj_k", "KDJ_D": "kdj_d", "KDJ_J": "kdj_j",
        "BOLLmid": "boll_mid", "BOLLup": "boll_up", "BOLLdown": "boll_down",
        "RSI6": "rsi6", "RSI12": "rsi12", "RSI24": "rsi24",
        "CCI": "cci", "WR": "wr", "BIAS6": "bias6", "BIAS12": "bias12", "BIAS24": "bias24",
    },
    "fund_flow_data": {
        "symbol": "code", "date": "trade_date",
        "shrmBuy": "super_buy", "shrmSell": "super_sell",
        "shrmSum": "super_net", "sjlmBuy": "large_buy", "sjlmSell": "large_sell",
        "sjlmSum": "large_net", "xjllSum": "retail_net",
        "mainNet": "main_net", "hsgt": "hsgt", "jjjl": "fund_net",
    },
    "block_trade": {
        "symbol": "code", "date": "trade_date",
        "price": "price", "volume": "volume", "amount": "amount",
        "buyer": "buyer", "seller": "seller", "discountRate": "discount_rate",
    },
    "holder_num": {
        "symbol": "code", "annDate": "ann_date",
        "holderNum": "holder_num", "top10holderShare": "top10_share",
        "avgShare": "avg_share_per_holder", "changeRate": "change_rate",
    },
    "announcements": {
        "symbol": "code", "date": "trade_date",
        "title": "title", "type": "type", "url": "url",
    },
}

# 白名单正则：防命令注入
WS_CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$")


def westock_batch(symbols, subcmd, *flags, timeout=30):
    """
    调 westock 批量，shell=False 避免注入。
    返回 list[dict] (解析后的行)
    """
    assert all(WS_CODE_RE.match(s) for s in symbols), f"非法 symbol: {symbols}"
    code_arg = ",".join(symbols)
    cmd = ["npx.cmd", "westock-data-clawhub@1.0.4", subcmd, code_arg, *flags]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"westock {subcmd} fail: {result.stderr[:200]}")
    return parse_markdown_table(result.stdout)


def parse_markdown_table(text):
    """
    解析 westock markdown 表格 → list[dict]
    参考 scripts/import_from_westock_baostock_akshare.py:parse_markdown_table
    """
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:  # 跳过表头 + 分隔行
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def load_tradable_codes():
    """
    从 tencent_quotes.csv 读 (code, market) 列表
    沪深北 code 6 位补零，market 全大写
    """
    from import_from_westock_baostock_akshare import load_tradable_codes as _ltc
    return _ltc()


def run_one_table(task_name, dry_run=False, conn=None):
    """
    跑一张表的增量补全
    """
    task = TASKS[task_name]
    subcmd = task["westock_subcmd"]
    start, end = task["date_range"]
    cmap = COLUMN_MAPS[task_name]

    cur = conn.cursor()
    cur.execute(f"SELECT MAX({task['date_field']}) FROM {task_name}")
    max_date = cur.fetchone()[0] or "1900-01-01"
    if max_date >= end:
        logging.info(f"[{task_name}] 已是最新 (max={max_date}), 跳过")
        return 0

    # 实际拉取区间
    real_start = max(
        date.fromisoformat(max_date) + timedelta(days=1),
        date.fromisoformat(start)
    ).isoformat()
    real_end = end
    logging.info(f"[{task_name}] 拉取 {real_start} ~ {real_end}")

    # 拉取股票列表
    pairs = load_tradable_codes()  # list[(code, market)]
    pairs = [(c, m) for c, m in pairs if c.isdigit() and len(c) == 6]

    # 按天拉取
    d = real_start
    inserted_total = 0
    while d <= real_end:
        day_rows = []
        BATCH = 50
        for i in range(0, len(pairs), BATCH):
            batch = pairs[i:i+BATCH]
            ws_codes = [f"{m.lower()}{c}" for c, m in batch]
            try:
                rows = westock_batch(ws_codes, subcmd, f"--date={d}", timeout=60)
                for r in rows:
                    # 转字段名
                    mapped = {cmap.get(k, k): v for k, v in r.items()}
                    if "code" not in mapped:
                        mapped["code"] = next(
                            (c for c, m in batch if f"{m.lower()}{c}" == r.get("symbol")),
                            None
                        )
                    day_rows.append(mapped)
            except Exception as e:
                logging.warning(f"[{task_name} {d}] 批 {i//BATCH+1}/{len(pairs)//BATCH} 失败: {e}")
                continue
            time.sleep(0.5)  # 防 westock 限流

        # 过滤这个 date 的行
        day_rows = [r for r in day_rows if r.get(task["date_field"]) == d]
        if not dry_run and day_rows:
            n = insert_ignore(cur, task_name, task["table_columns"], day_rows)
            conn.commit()
            inserted_total += n
            logging.info(f"[{task_name} {d}] +{n} 行 (累计 +{inserted_total})")
        d = (date.fromisoformat(d) + timedelta(days=1)).isoformat()

    return inserted_total


def insert_ignore(cur, table, columns, rows):
    """
    INSERT OR IGNORE 通用版
    """
    if not rows:
        return 0
    placeholders = ",".join(["?"] * len(columns))
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    n = 0
    for r in rows:
        try:
            cur.execute(sql, tuple(r.get(c) for c in columns))
            n += cur.rowcount
        except Exception as e:
            logging.warning(f"  INSERT 失败: {r} -> {e}")
    return n


def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--table", action="append", choices=list(TASKS))
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default="2026-06-23")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()

    # 设置日志
    log_file = LOG_DIR / "fill_recent_generic.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, mode="a"), logging.StreamHandler()],
    )

    conn = sqlite3.connect(str(DB_PATH))
    tables = args.table or list(TASKS.keys())
    for t in tables:
        try:
            n = run_one_table(t, args.dry_run, conn)
            logging.info(f"✅ {t}: +{n} 行")
        except Exception as e:
            logging.error(f"❌ {t}: {e}")
    conn.close()


if __name__ == "__main__":
    main()
```

### 3.4 重要：实际字段映射需要先验证

`COLUMN_MAPS` 中的字段名（如 `MA5` / `MACDdiff` / `shrmBuy`）**必须**先实测 westock 输出，**不能用猜测**。**第一步**：

```bash
# 跑一个 1 只股票 1 天看看实际输出
npx.cmd westock-data-clawhub@1.0.4 technical sh000001 --date=2026-06-23
npx.cmd westock-data-clawhub@1.0.4 asfund sh000001 --date=2026-06-23
npx.cmd westock-data-clawhub@1.0.4 blocktrade sh000001 --date=2026-06-23
npx.cmd westock-data-clawhub@1.0.4 shareholder sh000001
npx.cmd westock-data-clawhub@1.0.4 announce sh000001 --date=2026-06-23
```

把输出贴给用户校对字段名（不要猜字段）。

### 3.5 实测步骤（详细）

1. **单股单日 dry-run**（5 个 subcmd 各跑 1 次）
   ```bash
   for subcmd in technical asfund blocktrade shareholder announce; do
     echo "=== $subcmd ===";
     npx.cmd westock-data-clawhub@1.0.4 $subcmd sh000001 --date=2026-06-23 2>&1;
   done
   ```
   把输出贴给用户，**等用户确认字段名**

2. **写 `fill_recent_generic.py`**（基于 3.3 骨架 + 实测字段名）

3. **单表 dry-run 验证**
   ```bash
   python scripts/fill_recent_generic.py --table technical_indicators --dry-run
   ```
   检查 westock 输出和 INSERT 字段名是否对得上

4. **单表真实跑**（小批量先测 1-2 天）
   ```bash
   python scripts/fill_recent_generic.py --table technical_indicators --start 2026-06-23 --end 2026-06-23
   ```
   检查 DB rowcount

5. **单表全量**
   ```bash
   python scripts/fill_recent_generic.py --table technical_indicators
   ```

6. **5 表串行**
   ```bash
   python scripts/fill_recent_generic.py
   ```

7. **巡检**（用 `scripts/show_db_stats.py` 验证 5 张表都到 6-23）

---

## 4. 可复用代码

| 函数/模块 | 来源 | 用途 |
|---|---|---|
| `westock_batch` | 新写（见 3.3） | westock CLI 通用 wrapper，shell=False + 白名单 |
| `parse_markdown_table` | `scripts/import_from_westock_baostock_akshare.py` | 解析 westock markdown 输出 |
| `load_tradable_codes` | `scripts/import_from_westock_baostock_akshare.py` | 从 tencent_quotes.csv 读 stock 列表 |
| `CREATE_NO_WINDOW = 0x08000000` | `scripts/fill_recent_klines.py` | 隐藏 npx cmd 弹窗 |
| `INSERT OR IGNORE` + UNIQUE | 已存 schema（code, trade_date 唯一） | 幂等保证 |
| `subprocess.run` with shell=False | 标准库 | 防命令注入 |
| `time.sleep(0.5)` between batches | 实测限流经验 | 避免 westock 10054 错误 |

### 4.1 必须直接复用的常量

```python
# 从 src/data/westock.py 或 import_from_westock_baostock_akshare.py 复制
CREATE_NO_WINDOW = 0x08000000
WS_CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$")

# 从 build_db.py / consolidate_db.py 复制（具体列名查 schema）
DB_PATH = Path(__file__).resolve().parent.parent / "database" / "quant.db"
```

---

## 5. 边界条件

1. **westock 网络断/限流**: 单批 retry 3 次 (backoff 1s/2s/4s)，整批 SKIP，继续跑其他批/表
2. **字段名错误**: INSERT 失败会被 `insert_ignore` 吞掉，所以**必须先 dry-run 验证**字段名
3. **周末/节假日**: 拉取时返回空是正常的（westock 跳过非交易日），不要 fail
4. **重复运行**: 完全幂等（INSERT OR IGNORE + UNIQUE(code, trade_date)）
5. **新上市股**: tencent_quotes.csv 包含 5569 只，但 westock 单只可能查不到（公司刚上市数据未上传），需要 SWALLOW
6. **DB 锁定**: 跑的时候不要开其它 DB 写操作（SQLite 写锁）
7. **subcmd 错误名**: 写错字段名时 westock 会直接返回 error，log 出来 SKIP 即可

---

## 6. 输出要求

### 6.1 日志

- 文件：`C:\Users\admin\AppData\Local\Temp\fill_recent_generic.log`（追加模式）
- 控制台：INFO 级别实时输出

### 6.2 每张表跑完打印

```
[2026-06-24 16:30:00] [INFO] === technical_indicators ===
[2026-06-24 16:30:00] [INFO] [technical_indicators] 拉取 2026-06-17 ~ 2026-06-23
[2026-06-24 16:30:05] [INFO] [technical_indicators 2026-06-17] 拉取 5000 行 (跳过 already_have 0, 本次新增 4980)
[2026-06-24 16:30:05] [INFO] [technical_indicators 2026-06-17] +4980 行
...
[2026-06-24 16:45:00] [INFO] ✅ technical_indicators: +34000 行 (7 天, 17.5 min)
```

### 6.3 退出码

- `0`: 全部 OK 或全部已是最新
- `1`: 部分表 FAIL
- `2`: DB 打开失败

### 6.4 5 张表全部完成打印

```
========================================
📊 增量补全汇总 (2026-06-24)
========================================
✅ technical_indicators  +34,000 行  17.5min (6-17~6-23)
✅ fund_flow_data        +20,000 行  10.0min (6-19~6-23)
✅ block_trade           +5,000 行   8.0min (6-19~6-23)
✅ holder_num            +2,500 行   12.0min (6-19~6-23)
✅ announcements         +3,200 行   15.0min (6-17~6-23)
========================================
总耗时: 62.5 min | DB: 1667MB → 1690MB (+23MB)
```

---

## 7. 验收标准

1. ✅ `python scripts/show_db_stats.py` 显示 5 张表的 `max(date) = 2026-06-23`
2. ✅ DB 大小增长 ~20MB（5 张表的新增数据）
3. ✅ 没 cmd 弹窗（CREATE_NO_WINDOW 起作用）
4. ✅ 重跑同一区间是幂等的（行数不增加）
5. ✅ 处理边界（westock 失败 / 周末 / 新上市股）不崩溃
6. ✅ 日志清晰可读

---

## 8. 异常处理详细

### 8.1 westock 调用失败
- **单股 subcmd 不存在**: skip 这只，继续
- **整批网络断 (WinError 10054)**: 整批 retry 1 次 (sleep 5s)，仍 fail 则 SKIP
- **npx 启动失败**: 报 subprocess.CalledProcessError，立即终止

### 8.2 DB 错误
- **DB 文件被锁**: 5s 后重试 3 次
- **表不存在**: 报 ERROR 终止（schema 错）
- **UNIQUE 冲突**: `INSERT OR IGNORE` 静默跳过

### 8.3 数据校验
- **行数对不上**: daily 拉 5000 行，INSERT 后应该 +5000，如果 < 4500 报警
- **NULL 率**: 如果某字段 NULL > 50%，可能是字段名错映射，警告
- **日期范围**: 拉 6-17 只能出现 6-17 的行，混入其他日期则过滤掉

---

## 9. 关键约束（用户原话）

> "不跑模型，不跑回测，你就主要把项目的数据库搞起来"
> "你决定"——主动推进 + 中间汇报
> 改完都 push
> 无 cmd 弹窗
> 不要破坏现有代码（INSERT OR IGNORE + UNIQUE 幂等）

---

## 10. 时间预估

- **实测 westock 5 个 subcmd 字段名**: 10 min
- **写 `fill_recent_generic.py`**: 30-45 min
- **dry-run 验证**: 5 min
- **5 张表串行跑（5209 只 × 5-7 天）**: 60-90 min
- **巡检验收**: 5 min
- **总计**: ~2-3h

---

## 11. 关联文件

- `database/quant.db`：SQLite，1667 MB
- `docs/DATA_SCHEMA.md`：DB schema 完整字段定义
- `docs/DATA_STATUS.md`：当前 DB 状态报告
- `docs/TASK_daily_incremental_update.md`：长期通用脚本任务（关联）
- `scripts/fill_recent_klines.py`：daily_price 补抓参考实现（已跑成功）
- `scripts/import_from_westock_baostock_akshare.py`：westock/baostock 通用 wrapper
- `scripts/show_db_stats.py`：DB 状态统计
- `scripts/build_db.py`：CSV → DB 14 个 importer 统一入口
- `src/data/westock.py`：westock CLI wrapper（可参考）
- `src/models/database.py`：SQLAlchemy ORM（可参考表结构）
- `C:\Users\admin\AppData\Local\Temp\`：scratch 脚本 + 日志

---

*创建：2026-06-24 16:30*
*交接给另一个 AI 时附上本文件即可*
*不依赖任何额外上下文，可独立完成*
*本任务完成后，下一步进入 docs/TASK_daily_incremental_update.md 写长期自动化脚本*