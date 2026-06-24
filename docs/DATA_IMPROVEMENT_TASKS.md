# 数据完整性改进任务清单 (2026-06-25 审计)

> 生成时间: 2026-06-25
> 数据源: `D:\gitHub\qunat\quant-trading-system\database\quant.db`
> 审计脚本: `python -X utf8 -c "from src.models.repository import DataRepository; ..."`
> 当前日期: 2026-06-25

## 📊 当前数据快照 (审计时)

### 表级覆盖

| 表 | 行数 | 日期范围 | 距今天数 | 状态 |
|---|---:|---|---:|:---:|
| `stock_basic` | 5,209 | — | — | ✅ |
| `daily_price` | 3,017,078 | 2024-01-02 ~ 2026-06-16 | 9 天 | ⚠️ 部分 |
| `technical_indicators` | 1,797,447 | 2025-01-02 ~ 2026-06-16 | 9 天 | ⚠️ 缺 2024 |
| `benchmark_data` | 5,931 | 2002-01-04 ~ 2026-06-18 | 7 天 | ✅ |
| `fund_flow_data` | 435,092 | 2023-01-03 ~ 2026-06-18 | 7 天 | ✅ |
| `shareholder_count` | 5,521 | 2013-06-30 ~ 2026-06-18 | 7 天 | ✅ |
| `block_trade` | 54,700 | 2000-08-29 ~ 2026-06-18 | 7 天 | ✅ |
| `margin_trading` | 27,729 | 2026-05-29 ~ 2026-06-18 | 7 天 | ⚠️ 仅 20 天 |
| `backtest_result` | 95 | — | — | ✅ |
| `strategy_config` | 14 | — | — | ✅ |
| `finance_summary` | **0** | — | — | ❌ **完全空** |
| `prediction_record` | — | — | — | ❌ **表不存在** |
| `chip_distribution` | — | — | — | ❌ **表不存在** |

### 关键列 NULL 率

| 表.列 | NULL 数 | NULL 率 | 状态 |
|---|---:|---:|:---:|
| `daily_price.pct_change` | 3,017,078 / 3,017,078 | **100.0%** | ❌ 致命 |
| `daily_price.turnover` | 3,017,078 / 3,017,078 | **100.0%** | ❌ 致命 |
| `daily_price.amount` | 0 / 3,017,078 | 0.0% | ✅ |
| `technical_indicators.macd_hist` | 0 / 1,797,447 | 0.0% | ✅ |
| `technical_indicators.rsi14` | 1,631 / 1,797,447 | 0.1% | ✅ |
| `fund_flow_data.main_net` | 0 / 435,092 | 0.0% | ✅ |

### 跨表代码覆盖 (基准 5209 只)

| 表 | 覆盖代码数 | 覆盖率 |
|---|---:|---:|
| `daily_price` | 5,206 | 99.9% ✅ |
| `technical_indicators` | 5,200 | 99.8% ✅ |
| `fund_flow_data` | 5,065 | 97.2% ⚠️ |
| `shareholder_count` | 5,521 | 106.0% ⚠️ (有退市股,正常) |
| `block_trade` | 4,891 | 93.9% ⚠️ |

### daily_price 时间间隙 (>5 天,可能是数据缺失)

```
2024-02-08 → 2024-02-19 (11天)  ← 春节假期 (正常)
2024-04-30 → 2024-05-06 (6天)   ← 五一假期 (正常)
2024-09-30 → 2024-10-08 (8天)   ← 国庆假期 (正常)
2025-01-27 → 2025-02-05 (9天)   ← 春节假期 (正常)
2025-04-30 → 2025-05-06 (6天)   ← 五一假期 (正常)
2025-09-30 → 2025-10-09 (9天)   ← 国庆假期 (正常)
2026-02-13 → 2026-02-24 (11天)  ← 春节假期 (正常)
2026-04-30 → 2026-05-06 (6天)   ← 五一假期 (正常)
```

8 处间隙全部对应中国法定长假(春节/五一/国庆),**正常**。无数据缺口。

### `technical_indicators` 缺口

```
daily_price:           2024-01-02 ~ 2026-06-16 (592 个交易日)
technical_indicators:  2025-01-02 ~ 2026-06-16 (357 个交易日)
                                                  ^^^ 2024 全年缺失
```

**2024 全年 (235 个交易日) 的技术指标全部缺失**。任何需要 2024 数据回测的策略只能用 daily_price 实时算指标 (慢)。

---

## 🔴 P0: 致命缺陷 (阻塞功能)

### T1. `daily_price.pct_change` 列 100% NULL

**影响:**
- 任何使用 `pct_change` 的回测/排序/选股都退化
- `PortfolioBacktestEngine` 每次跑都 fallback 到从 `close` 实时计算 (`portfolio_engine.py:142`):
  ```python
  if df["pct_change"].isna().all():
      logger.info("pct_change 全为 NULL，从 close 价格计算...")
      df = df.sort_values(["code", "trade_date"])
      df["prev_close"] = df.groupby("code")["close"].shift(1)
      df["pct_change"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
  ```
- 启动时已加 fallback,**功能不阻塞**,但**慢**(每个 PortfolioBacktestEngine.run() 启动都重算 60 天 × 5209 股票 = 31 万次)
- `mcap_yi` 因为 `pct_change` 缺失 → 退化为 `amount/1e6`,市值估算精度低

**修复路径 (按工作量):**
- A. 一次性 SQL 回填 (推荐,5 分钟):
  ```sql
  -- 在 daily_price 表加回 pct_change (用 close 自计算)
  UPDATE daily_price SET pct_change = (
      SELECT (close - prev_close) / prev_close * 100
      FROM (SELECT code, trade_date, close,
            LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
            FROM daily_price) prev
      WHERE prev.code = daily_price.code AND prev.trade_date = daily_price.trade_date
  );
  ```
- B. 修改 importer,下次导入时直接计算 pct_change 并入库

**优先级:** P0 — 影响 PortfolioBacktestEngine 性能
**负责:** 数据运维 (已有 `incremental_update.py`)

### T2. `daily_price.turnover` 列 100% NULL

**影响:**
- PortfolioBacktestEngine `_build_universe` 中:
  ```python
  if df["turnover"].isna().all():
      df["turnover"] = 1.0  # 退化为常量
  ```
- 市值估算 `mcap_yi = amount / turnover` 退化为 `amount/1e6`,**绝对值无意义,仅保留相对顺序**
- 任何依赖 turnover 的策略(成交活跃度过滤、低流动性过滤)都失效

**修复路径:**
- A. 计算逻辑 (5 分钟): turnover ≈ volume / total_shares,需 stock_profile.total_share
- B. 从 westock 增量采集 (慢,网络依赖,半天)
- C. 行业均值兜底 (粗糙,1 小时)

**优先级:** P0 — 选股策略市值过滤失效
**负责:** 数据运维

---

## 🟠 P1: 重要缺陷 (影响回测真实性)

### T3. `finance_summary` 表完全空

**影响:**
- 基本面评分器 `fundamental_scorer.py` 完全失效
- `ThreeFactorStrategy` / `ShieldSpearStrategy` 等依赖 ROE/EPS 的策略无 ROE 数据
- `get_latest_finance_for_codes` 返回空 DataFrame

**修复路径:**
- A. 从 akshare 拉取 `stock_financial_report_sina` / `stock_financial_abstract_ths` (半天,网络依赖)
- B. 走 baostock 拉 `query_profit_data` (半天,免费但慢)
- C. 从 baostock 历史 CSV 导入 (已有 `scripts/import_benchmark_akshare.py`,可参考)

**优先级:** P1 — 影响基本面策略,但有 workarounds (用 daily_price 技术面)
**负责:** 数据运维

### T4. `prediction_record` 表不存在

**影响:**
- `/api/predict/{history,stats,verify}` 端点返回 `available: false`
- LHB 预测闭环断 (verify → update verified 标记)
- Web `/predict` 页面无数据

**修复路径:**
- A. 在 `src/models/database.py` 加 `PredictionRecord` ORM + `Base.metadata.create_all()` (30 分钟)
- B. 跑实际预测生成数据 (需要 XGBoost 模型训练,数小时)

**优先级:** P1 — 仅影响 LHB/XGBoost 预测闭环
**负责:** AI 团队

### T5. `chip_distribution` 表不存在

**影响:**
- `chip_scorer.py` (chip 评分) 完全失效
- 数据有但没建表:`market_data/chip_distribution.csv` (312KB)
- 选股筹码面信号缺失

**修复路径:**
- A. 在 database.py 加 `ChipDistribution` ORM + importer (1 小时)
- B. 从 `market_data/chip_distribution.csv` 灌入 (10 分钟)

**优先级:** P2 — chip 评分对 V龙头 XGBoost 有影响,对其他策略影响小
**负责:** 数据运维

---

## 🟡 P2: 时间覆盖缺口 (影响历史回测)

### T6. `technical_indicators` 缺 2024 全年

```
缺失: 2024-01-02 ~ 2024-12-31 (235 个交易日)
已有: 2025-01-02 ~ 2026-06-16 (357 个交易日)
```

**影响:**
- walk_forward 在 2024 区间内只能用 daily_price 实时算指标 (慢 5-10x)
- 任何要求 "技术指标已预计算" 的策略在 2024 退化

**修复路径:**
- A. 跑 `scripts/parallel_tech_indicators.py` 补算 (有脚本,需时间)
- B. 从 `market_data/technical_indicators/*.csv` 增量导入 (1 小时)

**优先级:** P2 — walk_forward 在 2024 区间可用但慢
**负责:** 数据运维

### T7. `margin_trading` 仅 20 天 (2026-05-29 ~ 2026-06-18)

**影响:**
- 融资融券评分信号窗口太短,无统计意义
- 长期均值/标准差计算退化

**修复路径:**
- A. 从 westock / 东方财富 拉历史融资融券数据 (慢,数小时)
- B. 接受现状 (评分仍可用,只是置信度低)

**优先级:** P2 — 评分仍可工作,只是信号弱
**负责:** 数据运维

---

## 🟢 P3: 优化项 (提升数据质量)

### T8. 跨表代码覆盖不均 (fund_flow 97.2%, block_trade 93.9%)

- `fund_flow_data` 缺 144 只 (2.8%)
- `block_trade` 缺 318 只 (6.1%)
- 这些通常是次新股、退市股、ST 股

**修复:** 增量导入时按 stock_basic 兜底
**优先级:** P3

### T9. `shareholder_count` 106% (>100%) 但只有半年频次

- 数据完整,但只有半年度披露,时间分辨率低
- 选股过滤 "股东户数下降" 信号粗糙

**修复:** 接受现状(数据源限制)或拉季报
**优先级:** P3

---

## 📋 改进任务优先级总结

| ID | 任务 | 优先级 | 责任 | 工作量 | 阻塞功能 |
|---|---|:---:|---|---:|---|
| T1 | daily_price.pct_change 回填 | 🔴 P0 | 数据运维 | 5min | PortfolioBacktestEngine 性能 |
| T2 | daily_price.turnover 计算/采集 | 🔴 P0 | 数据运维 | 半天 | 市值过滤、turnover-based 选股 |
| T3 | finance_summary 从 akshare 导入 | 🟠 P1 | 数据运维 | 半天 | 基本面评分 |
| T4 | prediction_record 表 + XGBoost 训练 | 🟠 P1 | AI 团队 | 数小时 | LHB/XGBoost 预测 |
| T5 | chip_distribution 表 + importer | 🟡 P2 | 数据运维 | 1 小时 | chip 评分 |
| T6 | technical_indicators 2024 全年补算 | 🟡 P2 | 数据运维 | 数小时 | walk_forward 2024 区间 |
| T7 | margin_trading 历史拉取 | 🟡 P2 | 数据运维 | 数小时 | 融资融券信号弱 |
| T8 | fund_flow / block_trade 增量兜底 | 🟢 P3 | 数据运维 | 1 小时 | 次新股覆盖 |
| T9 | shareholder_count 季度频 | 🟢 P3 | 数据运维 | — | 接受现状 |

---

## 🚀 建议执行顺序

### 立即做 (本周)
1. **T1** pct_change 回填 (5 分钟,SQL 一行,影响所有回测)
2. **T5** chip_distribution 建表 + 灌数据 (1 小时,恢复 chip 评分)
3. **T4** prediction_record 建表 (30 分钟,恢复 predict 接口)

### 短期 (本月)
4. **T2** turnover 计算 (半天)
5. **T6** technical_indicators 2024 补算 (数小时,关键)
6. **T8** fund_flow / block_trade 兜底 (1 小时)

### 中期 (下月)
7. **T3** finance_summary 拉取 (半天)
8. **T7** margin_trating 历史 (数小时)

---

## 🔧 紧急修复 (本周内)

### E1. 修复 daily_price.pct_change (P0,5 分钟)

```bash
# 单 SQL 回填 (用 SQLite 窗口函数)
sqlite3 database/quant.db <<EOF
UPDATE daily_price
SET pct_change = ROUND((close - prev_close) / prev_close * 100, 4)
FROM (
    SELECT code, trade_date, close,
           LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
    FROM daily_price
) prev
WHERE daily_price.code = prev.code
  AND daily_price.trade_date = prev.trade_date
  AND prev_close IS NOT NULL
  AND prev_close > 0;
EOF
```

或 Python:
```python
from src.db.engine import get_engine
from sqlalchemy import text
with get_engine().connect() as conn:
    conn.execute(text("""
        UPDATE daily_price SET pct_change = (
            SELECT (close - prev_close) / prev_close * 100
            FROM (
                SELECT code, trade_date, close,
                       LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
                FROM daily_price
            ) prev
            WHERE prev.code = daily_price.code
              AND prev.trade_date = daily_price.trade_date
        )
    """))
    conn.commit()
```

**验证:** `SELECT COUNT(*) FROM daily_price WHERE pct_change IS NOT NULL` 应等于当前总行数。

### E2. 修复 daily_price.turnover (P0,半天)

**方案 A (推荐):** 从 westock 增量采集 (有脚本框架)
```python
# scripts/fill_turnover.py (新建)
from src.data.westock import WestockClient
from src.models.repository import DataRepository
# 遍历 stock_basic,逐个拉 westock.turnover,更新 daily_price.turnover
```

**方案 B (临时):** 用 amount / outstanding_shares 估算
```sql
UPDATE daily_price SET turnover = (
    amount / NULLIF((SELECT total_share FROM stock_profile WHERE code = daily_price.code), 0)
)
WHERE turnover IS NULL;
```

(需要先灌 stock_profile.total_share)

### E3. 建 chip_distribution 表 + 灌数据 (P2,1 小时)

```python
# scripts/build_chip_distribution.py
from src.models.database import Base, ChipDistribution
from src.models.repository import DataRepository
from sqlalchemy import text
import pandas as pd

# 1. 加 ORM 模型
class ChipDistribution(Base):
    __tablename__ = "chip_distribution"
    id = Column(Integer, primary_key=True)
    code = Column(String(10))
    trade_date = Column(Date)
    profit_rate = Column(Float)  # 获利比例
    avg_cost = Column(Float)     # 平均成本
    concentration_90 = Column(Float)
    concentration_70 = Column(Float)

# 2. create_all
repo = DataRepository()
Base.metadata.create_all(repo.engine)

# 3. 从 market_data/chip_distribution.csv 导入
df = pd.read_csv("market_data/chip_distribution.csv")
df.to_sql("chip_distribution", repo.engine, if_exists="append", index=False)
```

### E4. 建 prediction_record 表 (P1,30 分钟)

```python
# src/models/database.py 加 ORM
class PredictionRecord(Base):
    __tablename__ = "prediction_record"
    id = Column(Integer, primary_key=True)
    code = Column(String(10))
    stock_name = Column(String(50))
    pred_month = Column(String(7))  # YYYY-MM
    as_of_date = Column(Date)
    pred_proba = Column(Float)
    signal = Column(String(10))
    auc = Column(Float)
    dim_scores = Column(Text)  # JSON
    logistic_coef = Column(Text)
    actual_return_20d = Column(Float, nullable=True)
    actual_return_60d = Column(Float, nullable=True)
    verified = Column(Integer, default=0)
    created_at = Column(DateTime)
```

`Base.metadata.create_all()` 自动建表。

---

## 📝 后续跟踪

- [ ] T1: pct_change 回填 (本周,数据运维)
- [ ] T2: turnover 计算/采集 (本周,数据运维)
- [ ] T3: finance_summary 导入 (本月)
- [ ] T4: prediction_record 建表 (本周)
- [ ] T5: chip_distribution 建表 (本周)
- [ ] T6: technical_indicators 2024 补算 (本月)
- [ ] T7: margin_trading 历史 (下月)
- [ ] T8: fund_flow/block_trade 兜底 (本月)
- [ ] T9: shareholder_count 季度频 (下月)

---

*本文档由 walk_forward web 验证过程中触发,作为数据运维团队的工作清单。*  
*下次审计时间: 2026-07-25 (1 个月后,或 T1-T5 完成后)*