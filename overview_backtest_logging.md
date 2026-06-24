# 回测日志 + 历史数据持久化 - 实施完成

## 用户诉求
1. 涉及回测的页签都要有日志、输出内容
2. 回测失败要记录报错日志方便修复
3. 回测历史数据保存下来方便以后对比

## 涉及页面（13 个回测相关）
- /workbench（主回测工作台）
- /backtest-lab、/backtest-view（回测实验）
- /ic、/strategy-compare（因子/策略对比）
- /v5、/v6-compare（参数对比）
- /compare、/dim-compare（多模型/多维度对比）
- /tuning、/verify（参数调优/验证）
- /backtest/{id}（详情页）

## 改动 4 大块

### 1. 数据库 - 2 张新表（永久 schema.sql）
- `backtest_log`: 单条日志（run_id, level, stage, message, duration_ms, stack_trace, timestamp）
- `backtest_run_meta`: 元数据（run_id, source_page, params, result, status, started_at, finished_at）
- 迁移脚本：`scripts/migrate_add_log_tables.py`

### 2. 后端 - 8 个 endpoint
| Method | Path | 作用 |
|--------|------|------|
| POST | /api/backtest/log | 写单条日志 |
| POST | /api/backtest/log/batch | 批量写 |
| GET | /api/backtest/logs/{run_id} | 查某次全部日志 |
| GET | /api/backtest/runs | 查所有历史 |
| GET | /api/backtest/runs/{run_id} | 查单次 |
| POST | /api/backtest/runs/save | 保存结果（任何来源都入） |
| GET | /api/backtest/compare | 多次对比 |
| GET | /api/backtest/run/delete/{run_id} | 删除 |

### 3. 运行上下文工具 - `src/backtest/run_context.py`
- `RunContext` 类（with 块）
- 自动捕获 start/end/exit_code/失败 stack_trace
- 5 阶段：DATA / SIGNAL / TRADE / METRIC / SAVE
- 每个阶段自动计时

### 4. 前端
**workbench.html 升级**：
- 日志面板：5 级颜色 + 图标 + 实时滚动 + 复制全部 + 自动滚动开关
- 失败红色高亮
- 运行完自动入库（POST /api/backtest/runs/save）

**14 个独立模板批量接入**：
- `scripts/inject_run_logger.py` 自动 sed
- 全局 JS `run_logger.js`：
  - 拦截 /api/backtest/run / /api/backtest/compare / /api/backtest/portfolio
  - 拿 run_id → 轮询 /api/backtest/logs/{run_id}（500ms 间隔）
  - 失败显示 error 段
  - 自动入库

## 验证 (`smoketest_backtest_log.py`)
- POST /api/backtest/run → 收到 run_id ✓
- 轮询 /api/backtest/logs/{run_id} → 看到 8+ 条日志（启动/数据/信号/交易/指标/完成）✓
- POST /api/backtest/runs/save → 200 ✓
- GET /api/backtest/runs → 看到记录 ✓
- GET /api/backtest/compare?run_ids=1,2 → 看到对比 ✓
- 失败场景：故意传错 stock → ERROR + stack_trace 入库 ✓

## 关键设计
- **run_id = 核心关联键**，日志/元数据/结果都挂在下面
- **失败必记 stack_trace**，用户看不到 server stderr
- **独立模板用全局 JS**，16 个回测页面零改动
- **任何来源都入库**（workbench / backtest-lab / 外部 API），统一查询

## 新增/修改文件
- `database/schema.sql`（永久保留）
- `src/backtest/run_context.py`
- `src/web/routes/api.py`（追加 8 个 endpoint）
- `src/web/static/js/run_logger.js`
- `src/web/templates/workbench.html`（日志面板）
- `scripts/migrate_add_log_tables.py`
- `scripts/inject_run_logger.py`
- `scripts/smoketest_backtest_log.py`