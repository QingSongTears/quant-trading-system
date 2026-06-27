# _legacy/ — ADR-0009 BacktestEngine 重构观察期

| 字段 | 值 |
|---|---|
| **创建日期** | 2026-06-27 |
| **观察期** | 7 天 (至 2026-07-04) |
| **关联 ADR** | [ADR-0009](../../../../docs/adr/0009-backtest-engine-refactor.md) |
| **删除触发** | 观察期内无回归 → `git rm -r src/backtest/_legacy/` → commit `chore(backtest): _legacy 清理 (#80)` |

## 1. 为何存在

`src/backtest/engine.py` (809 行) 和 `src/backtest/portfolio_engine.py` (732 行)
是 P0 关键路径（每天生产回测任务跑），行数严重超 AGENTS.md §2 的 500 行限制。
ADR-0009 决策将其水平拆分为 5 个新模块：
- `metrics.py` / `data_loader.py` / `runner.py` / `portfolio_runner.py` / `optimizer.py`

为快速回滚，原文件**仅重命名**（`.py` → `.py.legacy`）后搬至本目录，
观察 7 天内：

1. 生产回测任务无回归
2. 现有 97 个测试仍全绿
3. 公开 API（BacktestEngine / PortfolioBacktestEngine / BacktestReport）路径零变化

## 2. 文件清单

| 文件 | 原行数 | 状态 |
|---|---|---|
| `engine.py.legacy` | 809 | 备份中 — 重构前单股回测引擎 + BacktestReport + 参数搜索 + walk_forward |
| `portfolio_engine.py.legacy` | 732 | 备份中 — 重构前组合回测引擎 + 因子计算 + 调仓循环 |

## 3. 注意事项

- **不要 import**: 本目录文件**不能**被新代码 `from src.backtest._legacy import ...` 引用（会触发 `check_legacy.py`）
- **不要修改**: 这些文件已被新模块替代，只读备份
- **快速回滚**: 出现回归时，`git revert <重构 commit>` 即恢复 + `git checkout HEAD~3 -- src/backtest/`

## 4. 清理流程 (Step 13)

观察期 7 天后：
```bash
# 1. 确认无引用
grep -rn "_legacy" src/ tests/ --include="*.py"
# (期望: 0 命中)

# 2. 删除目录
git rm -r src/backtest/_legacy/

# 3. 提交
git commit -m "chore(backtest): _legacy 清理 (ADR-0009 观察期满) (#80)"
```

## 5. 相关链接

- ADR-0009 §5 Step 1 (备份观察期)
- ADR-0009 §5 Step 13 (清理)
- AGENTS.md §2 (单文件 ≤ 500 行)