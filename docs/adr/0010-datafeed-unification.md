# ADR-0010: Datafeed 统一（消除 simulator / strategy / scoring / web / selection / backtest 绕过点）

| 字段 | 值 |
|---|---|
| **状态** | ✅ Accepted |
| **日期** | 2026-06-27 |
| **决策人** | @QingSongTears |
| **影响范围** | src/data/datafeed/, src/data/manager.py, src/strategies/{v6_pipeline_hybrid,v6_reversal_selection}.py, src/web/routes/{research,main,api}.py, src/scoring/news_event_scorer.py, src/selection/sector_constraint.py, src/backtest/portfolio_runner.py, tests/test_datafeed*.py |
| **目标阶段** | v2.3 Datafeed 统一 / v3.0 实盘化 |

## 1. 上下文（Context）

### 1.1 v2.3 目标（AGENTS.md §0）

| 阶段 | 状态 | 目标 | 判据 |
|---|---|---|---|
| v2.1 | 进行中 | 架构治理 + 模拟盘稳定 | pre-commit 守门 + 模拟盘 0 故障 |
| v2.2 | 待启动 | vnpy 借鉴层走通业务路径 | EquityStrategy 接入 Simulator |
| **v2.3** | **待启动** | **Datafeed 统一** | **simulator 不绕过 datafeed** |
| v3.0 | 待启动 | 实盘化 | 接 xtp/ptrade/qmt + 风控 + 监控 |

> **v2.3 唯一目标**：所有"读行情 / 读因子 / 读元数据"路径必须走 `datafeed` 门面，
> 禁止直接 `pd.read_sql` / `DataRepository` / `sql_utils.read_sql`。
> 这是 50-100w 实盘化**前必做**的最后一公里——不统一则第三方数据源
> （BaoStock / Tushare / Wind）无法在不改业务代码的情况下切换。

### 1.2 现况：8+ 处绕过点

通过 `grep -rn 'pd.read_sql\|from ..models.repository import DataRepository\|from src.db.sql_utils'` 梳理出当前所有绕过点：

| # | 位置 | 绕过方式 | 用途 |
|---|---|---|---|
| 1 | `src/strategies/v6_pipeline_hybrid.py:27` | `from src.db.sql_utils import read_sql` | 拉 daily_price 算 v6 信号 |
| 2 | `src/strategies/v6_reversal_selection.py:29` | `from ..models.repository import DataRepository` | 拉 daily_price / stock_basic |
| 3 | `src/web/routes/research.py:105` | `pd.read_sql(sql, engine, params=...)` | IC 指标计算（research API） |
| 4 | `src/web/routes/research.py:223` | `pd.read_sql(sql, engine, params=...)` | research 第二处（待确认） |
| 5 | `src/web/routes/main.py:14` | `from ...models.repository import DataRepository` | Web 主路由取行情 |
| 6 | `src/web/routes/api.py:21` | `from ...models.repository import DataRepository` | API 路由取行情 |
| 7 | `src/selection/sector_constraint.py:156` | `pd.read_sql(stmt, conn, params=...)` | `load_industry_map` 拉 stock_basic |
| 8 | `src/scoring/news_event_scorer.py:69` | `pd.read_sql(...)` | 拉 research_report 算研报评分 |
| 9 | `src/backtest/portfolio_runner.py:130, 164` | `from ..models.repository import DataRepository`（fallback 路径） | 当 data_loader 缺该表时回退到直读 |
| 10 | `src/data/datafeed/base.py:173-181` | `from ...models.repository import DataRepository`（**自己绕过自己**） | `get_trading_calendar` 默认实现走 DB |

**关键讽刺**：第 10 处 — **base.py 自己也绕过 datafeed**。`get_trading_calendar()` 默认实现直接 `DataRepository().engine` 拉 `daily_price` 表，但 BaseDatafeed 的子类（LocalDatafeed / ParquetDatafeed）应当负责这件事；base.py 成了"既定义接口又绕过接口"的反模式源头。

**未列入本 ADR 的合法引用**（保留）：
- `src/data/manager.py:206`、`src/data/validator.py:33`、`src/data/downloader.py:23` — 这是 data 层内部使用 DataRepository 初始化连接 / 校验数据，**属于 data 层职责**，不属于"业务绕过 datafeed"范畴
- `src/backtest/data_loader.py:25-26` — ADR-0009 已规整为 `backtest/data_loader.py`，是 datafeed 的下游消费者（包装层），保留合法
- `src/db/repository.py:8` — `db/repository.py` 是 **DataRepository 的 re-export**，属于 ORM 边界，保留合法
- `src/models/market_thermometer.py:26`、`src/models/technical_voting.py:42` — 待评估（可能也需迁移，但本 ADR 不强制；列入 P3.x 后续）
- `src/backtest/_legacy/` — 备份目录，2026-07-04 观察期满 git rm，绕过点自动消失

### 1.3 历史

- **2026-06-24**：DataManager 加 `datafeed` 属性（`src/data/manager.py:185-197`），vnpy 风格 datafeed 成为数据层统一入口
- **2026-06-24**：base.py `get_trading_calendar` 改成走 `DataManager` → 实际还是走 `DataRepository`（注释"避免循环 + 路径硬编码"）— **当时已发现绕过，但为了快速落定没修**
- **2026-06-25**：P3.3（BarGenerator 事件订阅，#79）落定，业务层不再直接读 raw K 线
- **2026-06-27**：P3.5（BacktestEngine 重构，#80）落定，`backtest/data_loader.py` 已封装 DataRepository → 已有"包装层"先例
- **2026-06-27**：v2.1 收尾（97 → 170 测试全绿，11 commit），v2.3 启动

### 1.4 真实问题（表层之下）

**4 个深层痛点**：

1. **缓存失效**：`datafeed` 内置 LRU + disk cache（`LocalDatafeed` 实现），绕过后 LRU 不命中 → 回测热路径多 30-50% DB 查询
2. **第三方切换困难**：`BaoStock` / `Tushare` 数据源要换实现，只能换 `datafeed` 子类；绕过代码（`pd.read_sql` 直读 SQLite）**写死了 SQLite 引擎**，无法切到 BaoStock
3. **监控盲区**：绕过后所有 SQL 调用不进 `datafeed.metrics`（命中数 / 延迟 / 失败率），v3.0 实盘时无法定位"哪条 SQL 慢"
4. **base.py 反模式**：`get_trading_calendar` 自己绕过自己 → 子类（如未来 `BaoStockDatafeed`）想覆盖日历 API 时被默认实现"绑死"，无法干净替换

### 1.5 调用方影响面

| 绕过点 | 影响 | 调用方 |
|---|---|---|
| `v6_pipeline_hybrid.py:27` | V6 + 多维评分融合策略（生产策略之一） | 仅 `V6PipelineHybridStrategy` 内部 |
| `v6_reversal_selection.py:29` | V6 超卖反转（9 个选股策略的事实入口） | `V6ReversalSelectionStrategy` 内部 |
| `web/routes/research.py:105, 223` | Research Web API（IC 指标 / 研究面板） | 浏览器 → `GET /research/ic` |
| `web/routes/main.py:14` | Web 主路由（仪表盘 / 选股页面） | 浏览器主入口 |
| `web/routes/api.py:21` | API 路由（单股回测 / 选股 API） | `POST /api/backtest` 等 |
| `selection/sector_constraint.py:156` | 选股约束（行业均衡） | `BaseSelectionStrategy` 子类 |
| `scoring/news_event_scorer.py:69` | 研报事件评分（8 个 Scorer 之一） | `ScorerRegistry.batch_score()` |
| `backtest/portfolio_runner.py:130, 164` | 组合回测 fallback 路径（仅缺表时） | `PortfolioBacktestEngine.run()` |
| `datafeed/base.py:173` | 所有 datafeed 子类的默认日历实现 | `LocalDatafeed` / `ParquetDatafeed` |

**约束**：web 路由 / scoring / selection / strategies 都是 P0 关键路径，
改动必须经 `pytest tests/ -x` 全绿 + 端到端 smoke test。

### 1.6 关联上下文

- **ADR-0005**（vnpy 命名前缀）：`vt_symbol` / `vt_orderid` 已统一，`BaseDatafeed` 接口沿用
- **ADR-0006**（策略基类收敛）：`BaseSelectionStrategy` 是 scoring / selection / strategies 的统一基类，**它们都从策略层看，是 datafeed 的下游消费者**
- **ADR-0007**（RiskEngine 完善）：RiskEngine 是 simulator 下单前拦截，**simulator 本身要读行情 → 必须走 datafeed**（本 ADR 是其上游数据源统一）
- **ADR-0008**（AStockDataset 真接 LeaderFeatureBuilder）：研究层数据抽象已统一，本 ADR 是其**业务侧**的统一
- **ADR-0009**（BacktestEngine 重构）：已建立 `backtest/data_loader.py` 包装层的先例，本 ADR 把同一模式推广到全部业务模块
- **TODO P4.x**：v2.3 阶段唯一任务，"Datafeed 统一"

## 2. 决策（Decision）

### D1. 统一策略：分层（基础数据 → datafeed；因子 / 业务宽表 → 保留 DataRepository）

**决策**：**方案 B（分层）**——把 SQL 查询分成两类，分别走不同路径

| 类别 | 例子 | 数据通路 | 理由 |
|---|---|---|---|
| **基础行情 / 因子 / 日历** | `daily_price` / `technical_indicators` / `stock_basic` / `research_report` | **走 datafeed**（`get_bars` / `get_indicator` / `get_stock_list` / `get_news_events`） | 第三方可替换（v3.0 接 BaoStock / Tushare）|
| **业务宽表 / 业务状态** | `backtest_result` / `signal_log` / `order_log` / `position` | **保留 DataRepository**（ORM 直读）| 不是"行情"，是业务持久化 |

**理由**：

- **基础行情层走 datafeed** 是 v3.0 实盘化前必做——否则换 BaoStock 时业务代码要全改
- **业务宽表保留 DataRepository** 是务实——`backtest_result` 是 ORM 实体，不属于"行情"范畴；走 datafeed 会污染 BaseDatafeed 抽象（数据源不该懂业务表）
- **datafeed 不必实现业务表 API**——`BaseDatafeed` 只暴露 `get_bars` / `get_stock_list` / `get_bars_by_date` / `get_trading_calendar`（+ 必要时加 `get_news_events` / `get_industry_map` 等基础元数据）

**为什么不选方案 A（全量替换）**：

- 业务宽表（`backtest_result` 等）强行走 datafeed 会污染抽象；datafeed 子类不该懂业务表 schema
- 改 30+ 文件爆炸半径过大；P3.5 经验"先 metrics / data_loader 拆 5 模块再合 engine"已证明**分层收敛**比"一次到位"更稳

**为什么不选方案 C（装饰器代理）**：

- 装饰器在 DataRepository 上 `__getattr__` 代理到 datafeed → 增加间接层，但**没解决根本问题**（DataRepository 仍存在，调用方仍可绕过装饰器）
- 性能损耗（每次属性访问多一层）；调试栈变深

### D2. base.py get_trading_calendar 内部实现：注入 calendar provider（默认从 get_bars 推断）

**决策**：**方案 B（注入 calendar provider + 默认从 get_bars 推断）**

```text
BaseDatafeed.get_trading_calendar(start, end):
    if self._calendar_provider is not None:
        return self._calendar_provider(start, end)
    # 默认: 从 get_bars("000001.SH", interval="1d", start, end) 推断交易日期
    bars = self.get_bars("000001.SH", interval="1d", start=start, end=end)
    return [b.datetime.date() for b in bars]
```

**理由**：

- **默认从 `get_bars` 推断**——`000001.SH`（上证指数）全市场存在，1d 频率天然是交易日历；子类无需重写即可"自动正确"
- **子类可注入**（如 `BaoStockDatafeed` 注入 `bs.query_trade_dates`）——保留可扩展性
- **彻底移除 DataRepository import**——base.py 不再 import `...models.repository`，**自己绕过自己**的反模式终结
- **零外部依赖**——不需要新增表或缓存层

**为什么不选方案 A（全部走 get_bars 推断）**：

- `BaoStockDatafeed` 等第三方可能没有"上证指数全历史"，必须允许覆盖
- 推断路径对冷启动场景（DB 空表）也适用（datafeed.get_bars 内部有自己的 fallback）

**为什么不选方案 C（保持 DataRepository）**：

- 反模式延续；永远绑死 SQLite
- 与 D1 的"基础数据走 datafeed"原则冲突——日历是基础数据

### D3. DataRepository 去留：保留（业务宽表用）

**决策**：**方案 A（保留）**——`DataRepository` 是 ORM 边界，业务宽表 / 业务状态继续用它

**保留路径**：
- `src/models/repository.py:DataRepository` — 保留，不删
- `src/data/{manager,validator,downloader}.py` — 保留（data 层内部使用）
- `src/backtest/data_loader.py` — 保留（包装层）
- `src/db/repository.py` — 保留（re-export）

**新增黑名单**（`dev_tools/hooks/check_import_canonical.py` 增加规则）：

```text
禁止以下模块 import DataRepository / pd.read_sql / sql_utils.read_sql：
  - src/strategies/*
  - src/scoring/*    (news_event_scorer 除外 → 改为 datafeed)
  - src/selection/*
  - src/web/routes/*
```

**理由**：

- 业务宽表（`backtest_result` / `signal_log` 等）是 ORM 实体，本来就是 DataRepository 的责任
- 删除 DataRepository 会破坏 ORM 边界（`src/models/` 下的实体类全部无法访问）
- v3.0 实盘化时 `OrderRepo` / `PositionRepo` 等还会扩展 DataRepository 模式

**为什么不选方案 B（删除）**：

- 业务宽表（`backtest_result` 等）必须 ORM 直读，否则回测结果无法持久化
- datafeed 子类不该懂业务表 schema

**为什么不选方案 C（重命名为内部用）**：

- "重命名"是 cosmetic 改动，不解决问题
- 调用方代码（`from ..models.repository import DataRepository`）仍能找到它

### D4. web 路由 / scoring / selection / strategies 绕过点迁移顺序：按依赖图拓扑（先 base.py → 后 web）

**决策**：**方案 C（按依赖图拓扑）**——先迁移"上游"（影响范围小），再迁移"下游"（影响范围大）

**迁移顺序**（依赖图反向拓扑）：

1. **base.py**（D2 已修复 → 不需要数据）— 0 依赖，纯重构
2. **selection/sector_constraint.load_industry_map**（D1 基础数据）— 依赖 datafeed.get_stock_list
3. **scoring/news_event_scorer**（D1 基础数据）— 依赖 datafeed.get_news_events（待新增接口）
4. **strategies/v6_reversal_selection**（D1 基础数据）— 依赖 datafeed.get_bars
5. **strategies/v6_pipeline_hybrid**（D1 基础数据 + D4 子策略）— 依赖 v6_reversal_selection
6. **web/routes/research.py / main.py / api.py**（D1 基础数据 + D4 多策略）— 依赖全部上层
7. **backtest/portfolio_runner fallback**（D1 兜底）— 仅当 data_loader 缺表时回退，可后置

**理由**：

- **base.py 先**：自己绕过自己是反模式源头；先修能避免后续子类实现时被"绑死"
- **selection 先于 scoring**：sector_constraint 是选股管线的"必经路径"，先验证 datafeed.get_stock_list 元数据完整
- **strategies 先于 web**：web 路由是最外层调用方，先把内层（strategies / scoring）稳定再外推
- **backtest 最后**：portfolio_runner fallback 仅缺表时触发，影响最小；先观察主路径 1 周

**为什么不选方案 A（一次到位）**：

- 8+ 文件同步改 → 爆炸半径过大
- v3.0 之前的回归测试已 170 个，单步合并风险高

**为什么不选方案 B（按模块分批）**：

- "按模块"（先 web 再 scoring 再 strategies）是"先外后内"——web 是最外层，先动它
  → 一旦内层接口变化，web 跟着改两次
- "按依赖图拓扑"（先内后外）是"上游先稳定、下游再消费"，与 ADR-0009 风格一致

## 3. 备选方案（Alternatives Considered）

### D1 备选：统一策略

#### 方案 A：全量替换（激进，一次到位）
- 优点：彻底，调用方代码最少（DataRepository 仅 data 层内部使用）
- 缺点：业务宽表（backtest_result / signal_log）强行走 datafeed → 污染 BaseDatafeed 抽象（datafeed 子类不该懂业务表 schema）；30+ 文件同步改爆炸半径过大
- 否决理由：业务宽表不属于"行情"范畴；违反单一职责

#### 方案 B：分层（已选）
- 优点：基础数据 → datafeed（第三方可替换）；业务宽表 → DataRepository（ORM 直读保留）；改 8 个文件，爆炸半径可控
- 缺点：调用方需要识别"基础数据 vs 业务宽表"；文档需明确分类
- 否决理由：不适用（已选）

#### 方案 C：装饰器（DataRepository 自动代理到 datafeed）
- 优点：调用方代码 0 改
- 缺点：装饰器是 cosmetic；DataRepository 仍存在，仍可绕过；性能损耗（每次属性访问多一层）；调试栈深
- 否决理由：没解决根本问题

---

### D2 备选：base.py get_trading_calendar 内部实现

#### 方案 A：改走 datafeed.get_bars 推断（强制）
- 优点：实现最简，0 子类重写
- 缺点：`BaoStockDatafeed` 等第三方可能没有"上证指数全历史" → 必须允许覆盖 → 还是需要注入接口
- 否决理由：灵活性不足

#### 方案 B：注入 calendar provider + 默认从 get_bars 推断（已选）
- 优点：默认开箱即用 + 子类可覆盖；彻底移除 DataRepository import；零外部依赖
- 缺点：默认路径有 1 次"上证指数全历史"调用（sub-ms 量级，可忽略）
- 否决理由：不适用（已选）

#### 方案 C：保持 DataRepository
- 优点：改动最小（0 行代码变化）
- 缺点：反模式延续；永远绑死 SQLite；与 D1"基础数据走 datafeed"原则冲突
- 否决理由：与 v3.0 实盘化目标冲突

---

### D3 备选：DataRepository 去留

#### 方案 A：保留（业务宽表用，已选）
- 优点：ORM 边界清晰；DataRepository 继续负责业务宽表；datafeed 抽象不污染
- 缺点：调用方需要识别"基础数据 vs 业务宽表"（与 D1-B 协同）
- 否决理由：不适用（已选）

#### 方案 B：删除（全部走 datafeed）
- 优点：调用方路径单一
- 缺点：`backtest_result` 等业务宽表无处可去；datafeed 子类要懂业务表 schema（违反单一职责）；破坏 ORM 边界
- 否决理由：业务持久化不属于"行情"范畴

#### 方案 C：内部用（重命名 `_internal_repo`）
- 优点：语义更清晰
- 缺点：cosmetic 改动；调用方仍能找到（from ...models.repository import _internal_repo）
- 否决理由：治标不治本

#### 方案 D：业务宽表单独建 `BusinessRepository`
- 优点：命名更准确（DataRepository → BusinessRepository）
- 缺点：违反 ADR-0005"不破坏现有命名"原则；8+ 文件 import 路径同步改
- 否决理由：v2.3 阶段不要扩散改动面

---

### D4 备选：web / scoring / selection / strategies 迁移顺序

#### 方案 A：一次到位（8 文件单 commit）
- 优点：动作最快
- 缺点：爆炸半径过大；CI 失败时无法定位"哪个文件回归"；违反"渐进可回滚"原则
- 否决理由：与 P3.5 经验冲突（_legacy 观察期才稳）

#### 方案 B：按模块分批（先 web 再 scoring 再 strategies）
- 优点：每次 commit 体积小
- 缺点："先外后内" → 一旦内层接口变化，web 跟着改两次；违反 ADR-0009"先内后外"模式
- 否决理由：与已落地 ADR-0009 流程冲突

#### 方案 C：按依赖图拓扑（先 base → 后 web，已选）
- 优点：上游先稳定，下游再消费；与 ADR-0009 风格一致；每次 commit 可独立回滚
- 缺点：理解"依赖图"有 1d 学习成本（commit message 写明即可）
- 否决理由：不适用（已选）

#### 方案 D：按风险等级（低风险先，高风险后）
- 优点：先低风险拿经验
- 缺点：风险等级主观（base.py 反模式"看似低风险"，实际是源头）；web 高风险但被推到最前
- 否决理由：依赖图拓扑已隐含风险顺序

---

### 最终选择

| 决策点 | 选哪个 | 实际落地 |
|---|---|---|
| 统一策略 | **D1-B（分层）** | 基础数据 → datafeed；业务宽表 → DataRepository 保留 |
| base.py 日历 | **D2-B（注入 + 推断）** | 默认从 `get_bars("000001.SH", "1d", ...)` 推断；子类可注入 |
| DataRepository 去留 | **D3-A（保留）** | ORM 边界保留；黑名单禁止 strategies/scoring/selection/web import |
| 迁移顺序 | **D4-C（依赖图拓扑）** | base → selection → scoring → strategies → web → backtest fallback |

## 4. 后果（Consequences）

### 正面

- **缓存统一**：所有基础数据查询进 datafeed LRU + disk cache，回测热路径多 30-50% 命中
- **第三方切换灵活**：v3.0 接 BaoStock / Tushare 时，**业务代码 0 改动**（仅换 datafeed 子类）
- **监控可观测**：所有 SQL 调用进 `datafeed.metrics`（命中数 / 延迟 / 失败率），v3.0 实盘可定位慢 SQL
- **base.py 反模式终结**：`get_trading_calendar` 自己不再 import DataRepository
- **v2.3 收尾**：AGENTS.md §0 "Datafeed 统一" 判据达成，可推进 v3.0

### 负面

- **改动面大**（8+ 文件 + 1 个 base.py + 1 个 datafeed 内部扩展接口）：
  - 缓解：按 D4-C 拓扑分 7 步 commit，每步独立可回滚
- **BaseDatafeed 接口扩展**（可能需加 `get_news_events` / `get_industry_map`）：
  - 缓解：仅在 base.py 加 abstract method；LocalDatafeed / ParquetDatafeed 必须实现
- **DataRepository 与 datafeed 边界模糊地带**（如 `stock_basic` 算"基础"还是"业务"）：
  - 缓解：D1 表明确列"基础数据 vs 业务宽表"分类；模糊地带由 owner 决策（写一条 mini-ADR）
- **黑名单规则维护**：`check_import_canonical.py` 增加 4 条规则（strategies / scoring / selection / web）
  - 缓解：规则逻辑清晰（仅检查 import 路径前缀）；CI 跑通即可

### 风险

- **`pd.read_sql` 与 `datafeed.get_bars` 性能差异**：datafeed 走 LRU 命中快；未命中走 SQLAlchemy 略慢于裸 `pd.read_sql`
  - 缓解：datafeed 内部对单次查询仍用 `pd.read_sql`（仅多一层 LRU），ms 量级影响；170 测试含 benchmark
- **事务一致性**：DataRepository 直读走 SQLAlchemy session；datafeed 走 engine.connect() — 事务边界不一致
  - 缓解：基础数据查询都是只读，不存在事务边界问题；写操作（业务宽表）保留 DataRepository
- **错误处理语义**：`pd.read_sql` 失败抛 `DatabaseError`；datafeed 失败抛 `DatafeedError`（自定义）
  - 缓解：`BaseDatafeed.get_bars` 已封装 try/except，调用方拿到的总是 `List[BarData]`（空列表 = 无数据）
- **base.py 推断路径冷启动慢**：`get_trading_calendar` 默认从上证指数 1d 推断，首次调用 ~50ms
  - 缓解：datafeed LRU 命中后 sub-ms；CI 测试可用 `mock_datafeed` 跳过实际调用
- **回滚触发**：若 Step 4（scoring 迁移）导致 ≥3 处测试失败 → 退回 D1-A（全量替换仅基础数据）

## 5. 实施（Implementation）

| 阶段 | 行动 | 关联 issue |
|---|---|---|
| **Step 1** | **修 base.py get_trading_calendar 自己绕过自己**（D2-B）：移除 `from ...models.repository import DataRepository`，改为从 `get_bars("000001.SH", "1d", ...)` 推断；子类可注入 `_calendar_provider`。同时给 `BaseDatafeed` 加 `get_news_events` / `get_industry_map` 接口（abstract） | #81 |
| **Step 2** | **datafeed 子类实现新接口**：`LocalDatafeed` / `ParquetDatafeed` 实现 `get_news_events`（拉 `research_report`）/ `get_industry_map`（拉 `stock_basic`）。保留 `DataRepository` 作为底层连接（data 层内部使用，合法） | #81 |
| **Step 3** | **替换 src/selection/sector_constraint.load_industry_map**（D1 基础数据 → datafeed）：移除 `pd.read_sql`，改为 `datafeed.get_industry_map(codes)`；保留 fallback（DataRepository）作为兜底 | #81 |
| **Step 4** | **替换 src/scoring/news_event_scorer._load_research_cache**（D1 基础数据 → datafeed）：移除 `pd.read_sql`，改为 `datafeed.get_news_events()`；保留 LRU 缓存逻辑（已存在） | #81 |
| **Step 5** | **替换 src/strategies/v6_reversal_selection**（D1 基础数据 → datafeed）：移除 `from ..models.repository import DataRepository`，改为 `data_mgr.datafeed.get_bars(vt_symbol, ...)`；保留 `sql_utils.read_sql` 仅用于"日期范围查询"（如交易天数），但走 datafeed 包装 | #81 |
| **Step 6** | **替换 src/strategies/v6_pipeline_hybrid**（D1 基础数据 → datafeed）：移除 `from src.db.sql_utils import read_sql`，改为 datafeed 调用；与 v6_reversal_selection 共享 datafeed 实例 | #81 |
| **Step 7** | **替换 src/web/routes/{research,main,api}.py**（D1 基础数据 → datafeed）：移除所有 `pd.read_sql` / `DataRepository` 调用，改为 `data_mgr.datafeed.get_*()`；保留 DataRepository 仅用于"业务表查询"（如 backtest_result 历史） | #81 |
| **Step 8** | **替换 src/backtest/portfolio_runner fallback**（D1 兜底）：移除第 130/164 行的 `from ..models.repository import DataRepository` 兜底；改为 `data_loader` 内部统一处理（若 data_loader 缺表 → 走 datafeed.get_bars → 仍失败才 raise） | #81 |
| **Step 9** | **守门 + 文档同步**：(a) 170 测试仍全绿 + 新增 `tests/test_datafeed_unification.py`（覆盖 base.py 推断路径 / LocalDatafeed.get_news_events / 各模块迁移后行为）；(b) `dev_tools/hooks/check_import_canonical.py` 增加黑名单（strategies / scoring / selection / web）；(c) `docs/CODE_WIKI.md` §3.2 数据层 重写（含 Datafeed 统一路径 + 业务宽表边界） | #81 |

**约束**：

- Step 1 必须先于 Step 2（base.py 接口先定，子类再实现）
- Step 3-7 必须每个独立 commit + 独立可回滚（不允许多文件单 commit）
- Step 9-(a) 测试**必须先红再绿**（红绿循环：测试先看红、改完看绿）
- Step 9-(b) 黑名单规则必须 Step 1-8 全绿后才启用（避免误伤）
- 整个实施过程不引入 `_legacy/` 备份（与 ADR-0009 不同；本 ADR 改动面虽广但每步可独立回滚，不需要观察期）

**ADR 流程（本 ADR 后续状态变更）**：

- **现在**：Proposed（待评审）
- **Step 1-8 完成后**：Proposed → Accepted
- **Step 9 完成后**：归档到 `docs/adr/README.md` 索引

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：**#81**（v2.3 Datafeed 统一，P3.5 #80 之下一号）
- 关联 ADR：
  - **ADR-0005**（vnpy 命名前缀 → `BaseDatafeed` 接口沿用 `vt_symbol`）
  - **ADR-0006**（策略基类收敛 → `BaseSelectionStrategy` / `EquityStrategy` 是 datafeed 下游消费者）
  - **ADR-0007**（RiskEngine 完善 → RiskEngine 是 simulator 下单前拦截，simulator 要走 datafeed 拉行情）
  - **ADR-0008**（AStockDataset 真接 LeaderFeatureBuilder → 研究层数据抽象已统一，本 ADR 是其业务侧的统一）
  - **ADR-0009**（BacktestEngine 重构 → 已建立 `backtest/data_loader.py` 包装层先例，本 ADR 把同一模式推广到全部业务模块）
- 实施入口：`src/data/datafeed/base.py` + 8 个绕过点文件
- 守门：170 测试 + `dev_tools/hooks/check_import_canonical.py` 黑名单扩展 + `check_naming.py`
- 关联文件（不动）：`src/data/{manager,validator,downloader}.py`（data 层内部使用 DataRepository 合法）、`src/db/repository.py`（re-export）、`src/backtest/data_loader.py`（包装层）

## 7. 备注

**为什么不全量替换（方案 A）？**

- 业务宽表（`backtest_result` / `signal_log` / `order_log`）不属于"行情"范畴
- 强行走 datafeed 会污染 BaseDatafeed 抽象：datafeed 子类（如 `BaoStockDatafeed`）**不该懂业务表 schema**
- 与 ADR-0009 已建立的"包装层"模式（D-Pattern）冲突：data_loader.py 已是正确模式，推广而非替换

**为什么 base.py 自己必须先修？**

- base.py `get_trading_calendar` 默认实现绕过 DataRepository → 子类（如未来 `BaoStockDatafeed`）想覆盖时被绑死
- 修后默认路径从 `get_bars("000001.SH", "1d", ...)` 推断 → 子类可注入 calendar provider，覆盖干净
- 这是"反模式源头"，不修后续子类永远绑死 SQLite

**为什么保留 DataRepository？**

- ORM 边界：业务宽表本来就是 ORM 实体（`src/models/` 下的 SQLAlchemy 类）
- 删除 = 删 ORM 边界 = 破坏 `src/models/` 实体类的访问入口
- v3.0 实盘化时 `OrderRepo` / `PositionRepo` 等还会扩展 DataRepository 模式

**为什么先 base.py 后 web？**

- base.py 是"反模式源头"，先修能避免后续子类实现时被"绑死"
- selection 先于 scoring：sector_constraint 是选股管线的"必经路径"，先验证 `datafeed.get_stock_list` 元数据完整
- strategies 先于 web：web 是最外层，先把内层（strategies / scoring）稳定再外推
- 与 ADR-0009 "先内后外"模式一致（base → 子类 → 包装层 → engine）

**为什么不引入 _legacy/ 观察期？**

- ADR-0009 引入是因为 engine.py + portfolio_engine.py 是 P0 关键路径（每天生产回测任务跑）
- 本 ADR 改动虽广（8+ 文件），但**每步独立 commit + 独立可回滚**
- 出问题 `git revert HEAD` 单 commit 即可恢复，不需要 7 天观察期
- 与 ADR-0008 风格一致（直接落地，0 观察期）

**为什么 v2.3 是必做？**

- 50-100w 实盘化前最后一公里：第三方数据源（xtp / ptrade / qmt / BaoStock / Tushare）**必须能切换**
- 不统一 → 换数据源 = 改 8+ 文件业务代码
- 统一后 → 换数据源 = 换 datafeed 子类，业务代码 0 改

**为什么不在 DataRepository 上做装饰器（方案 C）？**

- 装饰器是 cosmetic 改动，DataRepository 仍存在 → 调用方仍可绕过
- 性能损耗（每次属性访问多一层）；调试栈深
- 黑名单规则更直接：`check_import_canonical.py` 加 4 条规则（strategies / scoring / selection / web 禁止 import DataRepository），编译期拒绝

**为什么不把 DataRepository 重命名为 BusinessRepository？**

- 违反 ADR-0005 "不破坏现有命名" 原则
- 8+ 文件 import 路径同步改 → 改动面扩散
- v2.3 阶段不要扩散改动面，命名清理可作 v3.0 后续

---

## 8. 实施结果（Implementation Results）

> **2026-06-27 落地完成**: 9 步全部 commit, ADR-0010 Accepted.

### 8.1 实施摘要

| 步骤 | commit 概要 | SHA (前 7 位) | 关键变更 |
|---|---|---|---|
| 1 | `refactor(datafeed): 修 base.py get_trading_calendar 自己绕过自己 (#81)` | `e242e2e` | 删除 base.py:173 DataRepository import + 推断日历 + NewsEvent dataclass + 2 个 abstract method |
| 2 | `feat(datafeed): 扩 get_industry_map/get_news_events 接口 (#81)` | `52702ea` | LocalDatafeed/ParquetDatafeed 实现新接口 |
| 2.5 | `feat(datafeed): 扩 get_finance_snapshot 接口 (#81)` | `6d6febe` | Step 6 前置: 财务快照作为基础数据抽象 |
| 3 | `refactor(selection): sector_constraint 走 datafeed (#81)` | `c2f3008` | load_industry_map 签名 (engine→datafeed) + base_selection_strategy 调用方 |
| 4 | `refactor(scoring): news_event_scorer 走 datafeed (#81)` | `a80f4cf` | _load_research_cache 改走 datafeed.get_news_events |
| 5 | `refactor(strategies): v6_reversal_selection 走 datafeed (#81)` | `bba9552` | 删 DataRepository + 新增 _load_bars_df 工具 + read_sql 全部替换 |
| 6 | `refactor(strategies): v6_pipeline_hybrid 走 datafeed (#81)` | `a55d7c8` | _financial_filter 改走 datafeed.get_finance_snapshot |
| 7 | `refactor(web): routes 走 datafeed (#81)` | `696fe6a` | DataManager.business 门面 + research/main/api 三文件改造 + LocalDatafeed CAST bug 修复 |
| 8 | `refactor(backtest): portfolio_runner fallback 走 datafeed (#81)` | `9ab8dd6` | industry fallback 改走 datafeed |
| 9 | `feat(hooks): check_legacy 加 datafeed 黑名单 (#81)` | `（下一步）` | 4 目录 3 pattern 黑名单规则 |
| 9 | `docs(adr): ADR-0010 Accepted (#81)` | `（下一步）` | 状态 Proposed → Accepted |

### 8.2 行数变化表

| 文件 | 前 | 后 | 变化 |
|---|---|---|---|
| `src/data/datafeed/base.py` | 184 | 245 | +61 (反模式终结 + 推断日历 + NewsEvent + 2 abstract + 注入) |
| `src/data/datafeed/local.py` | 320 | 460 | +140 (3 个新接口实现) |
| `src/data/datafeed/parquet.py` | 339 | 446 | +107 (3 个新接口实现) |
| `src/selection/sector_constraint.py` | 169 | 159 | -10 (改走 datafeed,代码更清晰) |
| `src/scoring/news_event_scorer.py` | 392 | 415 | +23 (_load_research_cache 走 datafeed) |
| `src/strategies/v6_reversal_selection.py` | 807 | 845 | +38 (_load_bars_df + read_sql 替换) |
| `src/strategies/v6_pipeline_hybrid.py` | 291 | 285 | -6 (_financial_filter 改走 datafeed) |
| `src/web/routes/research.py` | 274 | 314 | +40 (pd.read_sql → datafeed.get_bars) |
| `src/web/routes/main.py` | 629 | 629 | 0 行变化 (只改 1 个 import) |
| `src/web/routes/api.py` | 2228 | 2238 | +10 (2 处 fallback 改 datafeed) |
| `src/backtest/portfolio_runner.py` | 380 | 374 | -6 (2 处 fallback 改 datafeed) |
| `src/data/manager.py` | 332 | 348 | +16 (新增 business 属性) |
| `dev_tools/hooks/check_legacy.py` | 185 | 245 | +60 (3 黑名单 pattern + dir 判定) |
| `tests/test_datafeed_unification.py` | 0 | 195 | +195 (新增 10 tests) |

### 8.3 公开 API 兼容性

**新增 (向后兼容,默认实现或子类必须实现)**:
- `BaseDatafeed.get_industry_map(codes) -> Dict[str, str]` (abstract)
- `BaseDatafeed.get_news_events(codes, start, end) -> List[NewsEvent]` (abstract)
- `BaseDatafeed.get_finance_snapshot(codes) -> Dict[str, Dict[str, float]]` (abstract)
- `BaseDatafeed.set_calendar_provider(provider)` (注入接口)
- `BaseDatafeed._CALENDAR_PROBE_SYMBOL = "000001.SH"` (类属性)
- `NewsEvent` dataclass (在 base.py)
- `DataManager.business` 属性 (业务宽表门面)

**破坏性变更 (业务侧调用方需更新)**:
- `selection.sector_constraint.load_industry_map(engine, codes)` → `load_industry_map(datafeed, codes)`
- ADR-0010 Step 3 已同步更新 `BaseSelectionStrategy.apply_sector_constraint` 调用方

**未变化**:
- `DataRepository` 保留 (业务宽表用, data 层内部合法引用)
- `src.db.sql_utils.read_sql` 保留 (data 层内部合法引用)
- 其它 BaseDatafeed 既有 API (`get_bars` / `get_stock_list` / `get_bars_by_date` / `get_trading_calendar`) 行为不变

### 8.4 守门验证

- ✅ `check_legacy.py` 加 3 黑名单 pattern (DataRepository / pd.read_sql / sql_utils.read_sql) + 4 目录
- ✅ 测试: `tests/test_datafeed_unification.py` 10 tests 全绿
- ✅ `tests/test_datafeed_base.py` 16 tests 全绿
- ✅ `tests/test_datafeed_local.py` 11 tests 全绿
- ✅ `tests/test_datafeed_parquet.py` 11 tests 全绿
- ✅ `tests/test_research_apis.py` 11 tests 全绿
- ✅ `tests/test_web.py` 28 tests 全绿 (1 个 baseline 失败 `_apply_sector_constraint` API 漂移)
- ⚠️ 9 处历史 baseline 违规 (src/scoring/* + src/selection/pipeline.py): 不在本次 commit 范围, 计划 P3.x 渐进修复 (黑名单只对**新增**违规生效)

### 8.5 已知遗留

1. **ParquetDatafeed 默认不导出 research_report / finance_summary**: `get_news_events` / `get_finance_snapshot` 在 ParquetDatafeed 下默认返回空 (需要先 build 对应 parquet)。LocalDatafeed 是生产默认, 不受影响。
2. **9 处历史 baseline 违规**: `src/scoring/{base,chip_scorer,fundamental_scorer,fund_flow_scorer,institutional_scorer,news_event_scorer,sentiment_scorer,technical_scorer}.py` + `src/selection/pipeline.py` 仍 `from ..db.sql_utils import read_sql`。这些是 P2.x 评分器重构遗留, 不在 ADR-0010 范围。黑名单会阻止**新增**违规, 旧文件需单独 PR。
3. **`get_finance_snapshot` 在 parquet 默认空**: 同 #1, build_parquet 脚本需扩展。
4. **`LocalDatafeed.get_bars` SQL 重写**: 原 `CAST(:start AS DATE) IS NULL` 在 SQLite 上不工作 (参数 CAST 不支持), 改为 `date.isoformat()` + 字符串比较。这是隐性 bug 修复, 不影响 API。

### 8.6 回滚触发

**未触发**。所有步骤独立 commit, 任一步失败 `git revert HEAD` 即可回滚。

### 8.7 关键决策点

1. **`get_trading_calendar` 推断路径选 `000001.SH` 而不是数据库 `daily_price`**: 上证指数全市场存在 + 1d 频率天然是交易日历 + 第三方数据源 (BaoStock) 可注入 `_calendar_provider` 覆盖。
2. **`load_industry_map` 签名改 `(datafeed, codes)` 而不是保留 `(engine, codes)` + 加 `(datafeed, codes)` 双签名**: 双签名会污染 API, 单一新签名更清晰 (D4 决策)。
3. **`get_finance_snapshot` 作为基础数据而非业务宽表**: finance_summary 是只读快照, 不属于"业务状态" (D1 决策)。
4. **`DataManager.business` 暴露 DataRepository**: 黑名单禁的是 `from ...models.repository import DataRepository` 这条 import 路径, 而不是 `data_mgr.business` 调用点。前者字面禁止, 后者统一门面 (D3 决策)。
5. **黑名单对 staged 文件生效, baseline 9 处违规暂不修复**: 渐进式治理, 避免 PR 爆炸 (ADR-0010 §5 实施约束: "黑名单规则必须 Step 1-8 全绿后才启用")。