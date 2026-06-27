# AGENTS.md — AI 工作守则

> **本文件是项目唯一权威规则。每个 AI 会话、每个 PR、每个 commit 之前必须读完。**
> 最后更新：v2.1.0-hook-foundation · 2026-06-27
> 配套机制：`.githooks/pre_commit/`（自动守门脚本） + `docs/adr/`（决策记录）

---

## 0. 项目唯一目标（One Goal）

> **用 vnpy 4.4 架构对齐 + 模拟盘稳定运行，把 A 股量化策略推向 50-100w 实盘化。**

任何代码、文档、决策的最终判据只有一个：**它推动这个目标了吗？**

| 阶段 | 状态 | 目标 | 判据 |
|---|---|---|---|
| **当前 (v2.1)** | 进行中 | 架构治理 + 模拟盘稳定 | pre-commit 守门 + 模拟盘 0 故障 |
| v2.2 | 待启动 | vnpy 借鉴层走通业务路径 | EquityStrategy 接入 Simulator |
| v2.3 | 待启动 | Datafeed 统一 | simulator 不绕过 datafeed |
| v3.0 | 待启动 | 实盘化 | 接 xtp/ptrade/qmt + 风控 + 监控 |

**如果你做的工作不在以上任何阶段里，停下来问：这是必要的吗？**

---

## 1. 三条不可违反的硬规则（Hard Rules）

### 🔴 Rule 1 — 任何决策必须先看 `docs/adr/`

```bash
# 在你写出第一个 import 之前：
ls docs/adr/   # 看完所有现有 ADR
```

- 关键决策已经沉淀的：中文 alias / EventEngine 同步 / Direction 中文 enum / CSV 源-DB 派生 / 沿用 vnpy 字段名
- 看到 ADR 不同意？→ **写一条新的 ADR 反驳**（用 `0000-template.md`），不要绕过
- **没有 ADR 的"破坏性 / 跨模块 / ≥3 文件影响"决策 = 不允许 commit**

### 🔴 Rule 2 — 任何 commit 必须通过 `.githooks/pre_commit/` 全部守门

7 个守门脚本（`dev_tools/hooks/`）会自动跑：

| # | 守门 | 挡什么 |
|---|---|---|
| 1 | `check_naming.py` | 类名规范 + zh_name + en_name |
| 2 | `check_directory.py` | 新建目录需 ADR |
| 3 | `check_legacy.py` | 禁止引用已废弃模块 |
| 4 | `check_test_required.py` | 新 src/ 文件必须有 test |
| 5 | `check_import_canonical.py` | 禁止绕过统一门面 |
| 6 | `check_file_size.py` | 单文件 > 500 行禁止 |
| 7 | `check_commit_msg.py` | Conventional Commits + issue 关联 |

**守门失败 = commit 失败**。不要 `--no-verify` 绕过。

### 🔴 Rule 3 — 任何代码改动必须关联一个 issue

```
<type>(<scope>): <subject> (#<issue_number>)
```

- 例：`fix(scoring): 修复 FundFlowScorer 缺 zh_name (#78)`
- 例：`refactor(legacy): 删 stock_screener 死引用 (#79)`
- **无 `(#XX)` 的 commit = 守门拒绝**

---

## 2. 目录地图（X 类代码必须放 Y 目录）

```
quant-trading-system/
├── src/                       # 业务核心代码
│   ├── strategy/              # ⚠️ vnpy 模板（AlphaStrategy 已 deprecated，见 ADR-0006）
│   ├── strategies/            # 兼容层（v3.0 删除；新代码进 business_strategies/）
│   ├── business_strategies/   # 业务策略（v2.1 新增位置）
│   ├── scoring/               # 评分器（7 个 scorer 都继承 BaseScorer）
│   ├── backtest/              # 回测引擎 + 选股基类
│   ├── event/                 # EventEngine（真在用，保留）
│   ├── engine/                # BaseEngine / OmsEngine（真在用）
│   ├── gateway/               # MainEngine（真在用）；BaseGateway 0 子类（观察中）
│   ├── data/                  # 数据层（manager + datafeed + providers/）
│   ├── db/                    # ORM 实体 + repository
│   ├── research/              # 研究层（dataset / model / lab）
│   ├── optimization/          # 参数扫描、Walk-Forward
│   ├── web/                   # FastAPI 路由 + 模板
│   ├── utils/                 # 通用工具
│   ├── risk/                  # 风控
│   ├── metrics/               # 绩效指标
│   └── indicator/             # 指标（Protocol，0 isinstance 引用，观察中）
├── tests/                     # 测试（拆 unit/ integration/ regression/ e2e/）
│   └── conftest.py            # 公共 fixture
├── scripts/                   # 运维/数据/回测入口
│   ├── active/                # 当前在用
│   ├── archive/               # 过期但保留 6 个月
│   ├── _deprecated/           # 已废弃，1 个月可回滚
│   └── dev/                   # 开发工具（gen_changelog / gen_code_wiki / classify_scripts）
├── docs/                      # 文档
│   ├── adr/                   # ⭐ 决策记录（事实单源）
│   ├── dev-notes/             # 临时调研/实施小结
│   └── CODE_WIKI.md           # 代码结构事实单源
├── dev_tools/                 # 开发期工具（pre-commit 守门脚本本体）
│   └── hooks/                 # 7 个守门脚本
├── .githooks/                 # git hook 入口（被 core.hooksPath 指向）
├── data/                      # ⚠️ 模型/缓存/分析报告（不进 git）
├── market_data/               # ⚠️ CSV 源数据（LFS，不进普通 git）
├── database/                  # ⚠️ SQLite 派生（不进 git）
├── output/                    # ⚠️ 仪表盘/审计产物（不进 git）
└── backups/                   # ⚠️ DB 备份（不进 git）
```

**禁止开新顶层目录**（除非写 ADR）。如果需要新子目录（如 `src/scoring/registry/`），OK；但 `src2/` `common/` `utils_v2/` 一律禁止。

---

## 3. 命名规范（强制）

### 3.1 类后缀

| 类型 | 后缀 | 例子 |
|---|---|---|
| 策略 | `Strategy` | `V6ReversalStrategy` |
| 评分器 | `Scorer` | `FundFlowScorer` |
| 引擎 | `Engine` | `BacktestEngine` |
| 数据源 | `Provider` | `WestockProvider` |
| 模型 | `Model` | `XGBoostModel` |
| 仓库 | `Repository` | `DataRepository` |
| 路由 | `Router` | `DiagnoseRouter` |

### 3.2 中英 alias（所有业务类必填）

```python
class V6ReversalStrategy(BaseSelectionStrategy):
    zh_name = "V6超卖反转"           # 必填 — Web 界面、README 显示
    en_name = "V6 Reversal"          # 必填 — 日志、API
    description = "RSI14≤38 + BB≤0.10 + DD60≤-8% + 反转放量"
```

`check_naming.py` 会扫所有 `BaseStrategy` / `BaseScorer` / `BaseEngine` / `BaseProvider` 子类，缺 alias 拒绝。

### 3.3 文件名

- 全小写下划线：`fund_flow_scorer.py`（不是 `fundFlowScorer.py`，不是 `FundFlowScorer.py`）
- 与主类同名（去掉后缀）：`scoring/fund_flow_scorer.py` → `class FundFlowScorer`
- 禁止日期戳后缀：`fill_klines_0626.py` ❌ → `fill_klines.py`（日期信息在 commit 里）

---

## 4. 黑名单（禁止引用 / 禁止新建）

> 这些名字已废弃、已归档、已合并、已替代。看到 → 不要 import、不要新建同类。

| 名称 | 原因 | 替代 |
|---|---|---|
| `src/strategies/stock_screener/` | 已删（v2.1 #79） | `src/scoring/` + `src/selection/` |
| `src/strategy/alpha_strategy.AlphaStrategy` | 0 业务继承（v2.1 #80） | 直接继承 `BaseSelectionStrategy` |
| `src/data/westock_downloader.py` | stub，抛 NotImplementedError | `src/data/westock.py` |
| `src/models/{three_factor,shield_spear,extreme_small_cap}_strategy.py` 路径下 | models/ 不再装业务策略 | `src/business_strategies/` |
| `scripts/optimize_db.py.legacy.bak` | 备份文件 | 已删 |
| `v5_hybrid` / `v7_bull_wave` / `bull_8d_monthly_fixed` / `v3_reversal` | ROADMAP 已归档 | 物理删除（如果还在） |
| `from src.models.database import *` 绕过 ORM 边界 | 强制走 Repository | `DataRepository` |
| 单文件 > 500 行（除 `requirements.txt` 等纯数据） | 强制模块化 | 拆分 |

`check_legacy.py` 会扫所有 `import` / `from ... import` / 字符串引用，命中黑名单拒绝。

---

## 5. 强制流程（Process）

### 5.1 新增模块流程

```
1. 读 AGENTS.md（本文件）
2. 查 docs/adr/ 是否有相关决策
3. 如果是"破坏性 / 跨模块 / ≥3 文件影响" → 先写 ADR
4. git checkout -b feat/issue-N-description
5. 写代码 + 写测试（tests/ 必有对应 test_*.py）
6. pre-commit run --all-files  （必须全绿）
7. git commit -m "feat(scope): subject (#N)"
8. git push → 开 PR → 等 CI 全绿 → merge
9. 合并后 GitHub auto-close issue
```

### 5.2 修改已有模块流程

```
1. 读 AGENTS.md（本文件）
2. 如果是"破坏性 / 重命名 / ≥3 文件影响" → 先写 ADR + 兼容性 shim
3. 不允许 silent 改动 — 即使只是 docstring 也要在 commit message 写明
```

### 5.3 修 bug 流程

```
1. 先写一个能复现的 test（红）
2. 改代码让 test 绿
3. pre-commit run --all-files
4. commit "fix(scope): 描述根因 (#N)"
```

### 5.4 反对现有决策流程

```
1. 写 ADR "NNNN-oppose-XXX.md"，引用被反对的 ADR
2. 在 PR 描述里 @owner
3. 不要直接 commit 反向代码
```

---

## 6. Conventional Commits 强制格式

```
<type>(<scope>): <subject> [TICKET-123]

<body>

<footer>
```

| type | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `refactor` | 重构（既不修 bug 也不加功能） |
| `perf` | 性能优化 |
| `test` | 加测试 / 改测试 |
| `docs` | 只改文档 |
| `style` | 格式（不改语义） |
| `chore` | 构建/工具/CI 变更 |
| `revert` | 回滚 |

| scope | 说明 |
|---|---|
| `scoring` | 评分器 |
| `strategies` | 业务策略 |
| `backtest` | 回测引擎 |
| `data` | 数据层 |
| `web` | Web 路由/模板 |
| `db` | 数据库/ORM |
| `gateway` / `engine` / `event` | vnpy 借鉴层 |
| `legacy` | 清理废弃代码 |
| `docs` | 文档 |
| `ci` | CI/CD |
| `hooks` | 守门脚本本身 |

`check_commit_msg.py` 强制执行。

---

## 7. 决策查阅（Where to find what）

| 我想知道... | 去看 |
|---|---|
| 项目目标 / 当前阶段 | [README.md](README.md) + [LIVE_TRADING_ROADMAP.md](LIVE_TRADING_ROADMAP.md) |
| 为什么这样设计 | [docs/adr/](docs/adr/) — 决策记录 |
| 代码结构 | [docs/CODE_WIKI.md](docs/CODE_WIKI.md) |
| 现在该做什么 | GitHub Projects 看板 + `LIVE_TRADING_ROADMAP.md` §十 |
| 变更日志 | [CHANGELOG.md](CHANGELOG.md) |
| 团队角色 | [docs/项目总览.md](docs/项目总览.md) §六 |
| 临时调研笔记 | [docs/dev-notes/](docs/dev-notes/) |
| 守门脚本规则 | [dev_tools/hooks/README.md](dev_tools/hooks/README.md) |

---

## 8. AI 会话启动协议（每次开新 AI 必读）

**第 1 步**：读本文件（`AGENTS.md`）— 5 分钟

**第 2 步**：读 `docs/adr/` 所有现有 ADR — 10 分钟

**第 3 步**：读 `docs/CODE_WIKI.md` 的目录树 — 5 分钟

**第 4 步**：读 `LIVE_TRADING_ROADMAP.md` §一 + §十 — 5 分钟

**第 5 步**：`git log --oneline -30` — 看最近做了什么

**第 6 步**：开始工作。**任何不确定** → 优先写一条 ADR 或问 owner，不要"先 commit 再说"。

---

## 9. 紧急联系 / 升级路径

| 情况 | 行动 |
|---|---|
| 守门脚本有 bug / 误报 | [开 issue](../../issues/new) 标签 `hook-bug` |
| 不同意某条 ADR | 写反向 ADR（见 §5.4） |
| 不知道 X 该放哪 | 看 §2 目录地图；还不懂就问 owner |
| 紧急绕过守门（生产事故） | `git commit --no-verify` + 立刻写 ADR 说明 + 24h 内补合规 |

---

## 10. 守则的守则

> **AGENTS.md 本身也要被守门**。
> 修改本文件需要：
> 1. 提交 PR 描述改动理由
> 2. 至少 1 名 owner review
> 3. 在 ADR 里记录"为什么改"
> 4. `chore(hooks): 更新 AGENTS.md <规则> (#N)`

---

**TL;DR — 每个 AI 必须记住的 3 件事**：

1. **一个目标**：vnpy 架构对齐 + 模拟盘稳定 → 50-100w 实盘化
2. **一条规则**：先看 ADR → 写代码 → 过守门 → 关联 issue
3. **一处查阅**：`docs/adr/` 是事实单源

违反这 3 件事的 commit，**100% 被守门拒绝**。
