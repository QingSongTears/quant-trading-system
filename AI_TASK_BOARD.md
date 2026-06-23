# AI Task Board — 接续状态板

> **目的**: 给后续接手/平行工作的 AI 快速拿到上下文、避免重复造轮子。
>
> **区别**: `TODO.md` 是用户业务任务（夏普冲刺 v1.1）；`LIVE_TRADING_ROADMAP.md` 是实盘化宏观路线；本文件只聚焦 AI 接续所需的**当前状态 + 待办 + 决策**。

---

## 0. 项目一句话定位

A 股 50-100w 实盘化目标。当前阶段：**Vnpy 4.4 架构对齐 + 模拟盘落地**。

| 维度 | 现状 |
|---|---|
| 分支 | `develop`（基准，main/dev 保留） |
| 最近 commit | `f7c9e18` (HEAD) |
| Python | 3.13 |
| 数据 | CSV 源 (Git LFS, 290MB) + SQLite 派生 (gitignore, 894MB) |
| DB 大表 | daily_price 4.18M 行 / stock_basic 5209 只 / benchmark_data 空 |
| 测试 | pytest 259 ✓ / 2 预存失败（DB 初始化无关） |
| 依赖关键 | akshare 1.18.64 / sqlalchemy 2.0.51 / fastapi / pandas 3.0 |

---

## 1. 已完成（Last 10 commits）

| Commit | 模块 | 状态 | 验证 |
|---|---|---|---|
| `f7c9e18` | **Vnpy 数据源抽象** — `src/datafeed/{base,local}.py` | ✅ | 9/9 smoke, pytest 259✓ |
| `b2c0a05` | **Vnpy 策略模板** — `src/strategy/{alpha,equity}_strategy.py` | ✅ | 6/6 smoke, pytest 259✓ |
| `2e981ea` | Vnpy 事件引擎 + Gateway 抽象 — `src/event/`, `src/gateway/` | ✅ | 14 事件常量 + 8 Gateway ABC |
| `4d758cb` | walk_forward V6 预计算 hook | ✅ | 提速 ~3x |
| `f62d2a7` | stock_screener/ 标记 LEGACY | ✅ | __init__.py 加警告 |
| `7b2b7a6` | E2 清理 + 测试修复 | ✅ | 删除 train_xgb_model.py |
| `9d6d4f3` | `A股全市场数据/` → `market_data/` | ✅ | 20 文件路径批量替换 |
| `719f47f` | `scripts/build_db.py` 统一 CSV→DB | ✅ | 66s 导入 4.18M 行 |
| `32f8c08` | `scripts/walk_forward.py` OOS 验证 | ✅ | smoke test 通过 |
| `5d63a8e` | V龙头主升浪骨架 | ✅ | 业务名 + 类 alias |

---

## 2. 进行中（In Progress）

**暂无 active in-progress 项** — 等用户定下一步候选（见 §4）。

---

## 3. 待办 / 阻塞（Open Issues）

### 🔴 P0 — 实盘化阻塞

| ID | 任务 | 现状 | 阻塞点 |
|---|---|---|---|
| OOS-1 | walk_forward 真实数据 OOS | 框架就绪 | 单窗口 precompute ~9min，2 窗口超时 |
| OOS-2 | Window 0 OOS max_dd=-0.09% 异常 | OOS sharpe=3.67 / 收益+36.10% | max_dd 几乎无回撤，疑似回测 bug |

**OOS-2 排查方向**:
- `src/backtest/portfolio_engine.py::_simulate_portfolio`
- 持仓变更与 `daily_price.pct_change` 衔接逻辑
- 可能是未成交 / 持仓未变 / 净值为常数

### 🟡 P1 — Vnpy 落地剩余

| ID | 任务 | 文件 | 备注 |
|---|---|---|---|
| VNPY-1 | V6 / V龙头 继承 EquityStrategy | `src/strategies/v6_*.py` | 桥接 vnpy 模板与现 BaseSelectionStrategy |
| VNPY-2 | Recorder 模块 | 新建 `src/datafeed/recorder.py` | 行情录制/回放 |
| VNPY-3 | RiskEngine 骨架 | 新建 `src/risk/engine.py` | 单笔/单日风控（在 set_target 之后） |
| VNPY-4 | BaoStockDatafeed | 新建 `src/datafeed/baostock.py` | 首次补全兜底（baostock 0.9.10 已装） |

### 🟢 P2 — 工程清理（来自 `docs/代码工程优化任务清单.md`）

- E2 ✅ / E4 ✅ / E10 ✅ 已完成
- E5 / E8 / E9 / E11 / E12 / E13 待处理（详见原清单）

---

## 4. 下一步候选（用户决定）

1. **VNPY-1** — V6 / V龙头继承 EquityStrategy（验证 vnpy 模板可行性）
2. **OOS-1 解锁** — walk_forward 缩窗口/并行/向量化 precompute_all
3. **OOS-2 排查** — Window 0 max_dd 异常（回测 bug？）
4. **VNPY-3** — RiskEngine 骨架
5. **VNPY-2** — Recorder 行情录制/回放

---

## 5. 关键架构决策（不要重新发明）

| 决策 | 内容 | 出处 |
|---|---|---|
| 分支 | `develop` 基准 | 用户明确 |
| 命名 | 中文业务名 + 类名 alias（如 V6超卖反转 / V6ReversalStrategy）| 命名规范 |
| 数据架构 | CSV 源 + DB 派生（gitignore）| build_db.py 设计 |
| Vnpy 借鉴 | EventEngine 同步派发（vnpy 是异步 Queue）| src/event/engine.py |
| 行情事件 | 全局常量（不用 vnpy 的 `eTick.SYMBOL.EX`）| 简化 |
| data object 字段 | 沿用 vnpy 名（gateway_name/vt_symbol/vt_orderid）| src/gateway/object.py |
| Direction/Offset | **中文 enum**（多/空/开/平）| src/gateway/object.py L26-37 |
| 策略模板 | AlphaStrategy 基类 + EquityStrategy A 股选股模板 | src/strategy/ |
| 数据源 | LocalDatafeed (SQLite, 4.4x 批量加速) | src/datafeed/local.py |
| DB 字段 | `amount` 是成交额（元）→ 映射 vnpy `turnover` | DB schema |

---

## 6. 快速启动命令

```bash
# 1. 拉代码
git pull origin develop

# 2. 建/更新数据库 (CSV → SQLite, 894MB, ~66s)
python scripts/build_db.py

# 3. 跑测试
python -m pytest tests/ --ignore=tests/e2e -q

# 4. Smoke test 模板 (独立脚本)
python C:\Users\admin\AppData\Local\Temp\smoke_strategy.py
python C:\Users\admin\AppData\Local\Temp\smoke_datafeed.py

# 5. Walk-forward OOS (当前阻塞)
python scripts/walk_forward.py --smoke    # 合成数据可跑
# python scripts/walk_forward.py          # 真实数据超时, 需 VNPY/OOS 解锁
```

---

## 7. 用户偏好（沟通风格）

- **简洁直接** — 短句，不冗余
- **先看代码/配表再下结论** — review 时优先核对实际代码（`src/.../*`、共享 config、共享基类）
- **武魂/Buff 100 倍整数约定** — `powerArg=90` 表示 90%，公式 `/100` 是单位换算口径
- **不要主动加 test** — 等用户明确"加 test"再写
- **P0 不要有水分** — 漏报可接受，误报要快速修正
- **改完都 push** — 单一 source of truth
- **实盘资金** 50-100w，阶段 1 OOS 不通过不上阶段 3（模拟盘）

---

## 8. 关键文件索引（AI 必读）

| 路径 | 用途 |
|---|---|
| `LIVE_TRADING_ROADMAP.md` | 实盘化主路线图（10 章） |
| `docs/Vnpy_Optimization_Notes.md` | vnpy 4.4 vs 本项目对照 + 7 项改进优先级 |
| `docs/代码工程优化任务清单.md` | E1-E13 工程清理任务 |
| `TODO.md` | 用户业务任务（夏普冲刺 v1.1），**不要覆盖** |
| `src/strategy/` | AlphaStrategy + EquityStrategy |
| `src/datafeed/` | BaseDatafeed + LocalDatafeed |
| `src/event/engine.py` | EventEngine 同步派发 |
| `src/gateway/` | object.py / base_gateway.py / main_engine.py |
| `src/backtest/portfolio_engine.py` | OOS 引擎（max_dd 异常排查点） |
| `src/backtest/astock_strategy.py` | A 股规则（T+1/涨跌停/佣金/印花税） |
| `scripts/build_db.py` | CSV → DB 重建工具 |
| `scripts/walk_forward.py` | OOS 验证（`_optimize`/`_backtest`/门禁） |
| `market_data/` | CSV 源 (Git LFS) |
| `database/quant.db` | SQLite 派生 (gitignore) |
| `E:\work\work\vnpy\` | 参考实现源（4.4.0） |

---

## 9. 已知坑（其他 AI 不要踩）

- **PowerShell 5.1 不支持 `&&`** — 用 `;` 或 `if ($?)`
- **PowerShell 把下划线变量当 cmdlet** — 复杂 python -c 用临时 .py 文件
- **`direction.value` 是中文**（"多"不是 "LONG"）— 测试断言用中文
- **`BarData.vt_symbol` 是 property 不是字段** — 构造时传 `symbol + exchange`
- **`ContractData.extra` 是 `init=False`** — 先建对象再赋值
- **写文件路由陷阱** — 多次连续 Write 到同一路径只保留最后一次（用 mavis-trash 先清空）

---

*最后更新: 2026-06-23 (auto-generated by 代码助手 after f7c9e18)*
*触发: 用户要求"其他 AI 也能读取来工作"*