# T3.2 行业暴露控制 — 概览

## 目标
防止组合过度集中在单一行业，降低尾部风险。

## 实现

### 新增模块
| 文件 | 角色 |
|------|------|
| `src/selection/sector_constraint.py` | 核心: `SectorConstraint` 配置 + `apply_sector_cap` 算法 + `load_industry_map` DB 加载 |
| `tests/test_sector_constraint.py` | 16 个单元测试 (排序/缺列/补全) |
| `tests/test_sector_engine_integration.py` | 7 个集成测试 (BaseSelectionStrategy + PortfolioBacktestEngine) |

### 修改模块
| 文件 | 变更 |
|------|------|
| `src/backtest/base_selection_strategy.py` | 加 `sector_cap_pct` (0.30) + `sector_max_count` (None) 类属性 + `apply_sector_constraint()` 方法 |
| `src/backtest/portfolio_engine.py` | 加 `_apply_sector_constraint()` 钩子, 在 `_simulate_portfolio` 调仓时自动应用 |
| `src/selection/__init__.py` | 导出新 API |

## 设计要点

1. **数据源**：`stock_basic.industry`（5,065 只有效），非 stock_profile（空）
2. **算法**：贪心 — 按评分降序遍历候选, 行业累计超限则跳过, 候选不足时从 universe 跨行业补全
3. **兜底**：完全无 industry 信息（测试/合成数据）→ 退化为保留 selected 原样（向后兼容）
4. **SQL**：SQLAlchemy `bindparam + expanding=True` 让 `IN :codes` 正确展开 tuple

## 验证

### 单元测试
- `tests/test_sector_constraint.py`: **16/16 ✅**
- `tests/test_sector_engine_integration.py`: **7/7 ✅**
- 全测: **874 passed, 1 skipped** (新增 23 测试, 0 fail)

### A 股端到端回测 (SmallCapStrategy, 2025-06 ~ 2025-12, 6 个月)

| 配置 | 收益 | 夏普 | 回撤 |
|------|------|------|------|
| 无约束 | +22.37% | 2.14 | -9.19% |
| 30% cap | +22.71% | 2.20 | -8.94% |
| 20% cap | +21.06% | 2.09 | -9.64% |
| **15% cap** | **+25.08%** | **2.52** | **-8.22%** |
| 20% cap + max3 | +25.08% | 2.52 | -8.22% |

**结论**: 15% cap 是甜点 — 收益 +2.71pp, 夏普 +0.38, 回撤 +0.97pp。

## 下次任务
- T5.1 参数扫描框架 (网格搜索自动找最优 cap_pct + max_count)
- VNPY-1 V6继承EquityStrategy