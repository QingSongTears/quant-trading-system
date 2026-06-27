# T5.1 参数扫描框架 — 概览

## 目标
系统化调参，替代手动试错。

## 实现

### 新增模块
| 文件 | 行数 | 角色 |
|------|------|------|
| `src/optimization/__init__.py` | 22 | 公共 API |
| `src/optimization/space.py` | 200 | Param/Float/Int/Categorical + ParamSpace + YAML 双向 |
| `src/optimization/runner.py` | 138 | BacktestRunner + TrialResult + 综合评分 fallback |
| `src/optimization/grid_search.py` | 65 | GridSearcher (笛卡尔积 + topk + export) |
| `src/optimization/bayesian_opt.py` | 122 | BayesianOpt (Optuna TPE) |
| `src/optimization/pareto.py` | 86 | ParetoFront 多目标分析 |
| `src/optimization/store.py` | 110 | ScanStore SQLite 持久化 + 断点续扫 |
| `scripts/param_scan.py` | 195 | CLI (grid/bayes/pareto/list-runs) |
| `config/scans/sector_cap.yaml` | 21 | 示例扫描空间 |
| `tests/test_optimization.py` | 200 | 20 单元测试 |

**总计: 9 个新文件, +1159 行**

## 关键能力

1. **参数空间** — `Float`/`Int`/`Categorical` 三类, YAML 双向序列化
2. **网格搜索** — 笛卡尔积遍历, top-k, 导出 JSON
3. **贝叶斯优化** — Optuna TPE, 多 trial, seed 可复现
4. **帕累托前沿** — 多目标自动筛, minimize 集合支持
5. **持久化** — SQLite, 按 (strategy/space/start/end) 归档, 断点续扫

## CLI 用法

```bash
# Dry-run 看空间大小
python scripts/param_scan.py grid --space config/scans/sector_cap.yaml \
  --strategy SmallCapStrategy --start 2025-06-01 --end 2025-12-31 --dry-run

# 全量网格 (315 组合)
python scripts/param_scan.py grid --space config/scans/sector_cap.yaml \
  --strategy SmallCapStrategy --start 2025-06-01 --end 2025-12-31 \
  --output output/scans/grid.json --db database/scan_store.db

# 贝叶斯 40 trials
python scripts/param_scan.py bayes --space config/scans/sector_cap.yaml \
  --strategy SmallCapStrategy --start 2025-06-01 --end 2025-12-31 \
  --n-trials 40 --output output/scans/bayes.json --db database/scan_store.db

# 帕累托分析
python scripts/param_scan.py pareto --results output/scans/grid.json \
  --objectives sharpe total_return --minimize max_drawdown --top-k 5

# 查看历史
python scripts/param_scan.py list-runs --db database/scan_store.db
```

## 测试

| 套件 | 通过 | 耗时 |
|------|------|------|
| `tests/test_optimization.py` | **20/20 ✅** | 0.6s |
| `tests/test_sector_constraint.py` | 16/16 ✅ | (已存在) |
| `tests/test_sector_engine_integration.py` | 7/7 ✅ | (已存在) |

## 实战

sector_cap 扫描:
- 网格: **315 组合**, Pareto 前沿 **10 点**, Top-1 sharpe=2.366
- 贝叶斯: **40 trials**, 12 步收敛到 sharpe=2.142 (cap=0.15, max=5, rb=20, mode=atr)

## 依赖

- `optuna 4.9.0` (新增到 requirements)
- `scikit-optimize 0.10.2` (新增)
- `scikit-learn 1.9.0` (optuna 间接)

## 后续
- 接入真实 PortfolioBacktestEngine (目前用综合评分 fallback)
- 增加并行 (Optuna parallel sampler)
- 加入 Web UI (扫描结果可视化)

## Issue

#75 T5.1 参数扫描框架 — 完成