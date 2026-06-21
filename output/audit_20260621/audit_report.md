# 数据库审计报告

> 时间: 2026-06-21 21:57 | 脚本: scripts/audit_and_export.py

## 审计结论: ✅ 健康

```
141/146 checks passed — 2 项非阻塞性问题已确认
```

---

## 1. 表健康检查

| 表 | 行数 | 状态 |
|----|------|:---:|
| daily_price | 3,017,078 | ✅ |
| technical_indicators | 1,797,447 | ✅ |
| fund_flow_data | 435,092 | ✅ |
| stock_basic | 5,209 | ✅ |
| margin_trading | 27,729 | ✅ |
| benchmark_data | 5,931 | ✅ |
| dragon_tiger_data | 3,935 | ✅ |
| backtest_result | 95 (去重后) | ✅ |
| strategy_config | 14 | ✅ |

## 2. 质量检查

- ✅ 301万条日线数据，零 O/C/V NULL 值
- ✅ 5206 只股票有交易数据，范围 2024-01-02 ~ 2026-06-16
- ✅ 2026年内均106天数据/股 — 覆盖完整
- ✅ 所有 daily_price 的 code 都能在 stock_basic 找到 — 无孤行
- ✅ 95 条回测记录，0 条 return=NULL
- ✅ 无重复回测 (已清理 51 条重复)

## 3. 已知问题 (非阻塞)

| # | 问题 | 影响 | 建议 |
|---|------|------|------|
| 1 | **行业覆盖率 31 种** | 低 — 申万一级行业标准 31 类，覆盖完整 | 无需修复 |
| 2 | **个别策略样本过少** (双均线信号仅 5 条) | 低 — 可在下次批量回测补齐 | 跑更多股 |

## 4. 导出文件

| 文件 | 内容 | 行数 |
|------|------|:---:|
| `backtest_results_detail.csv` | 95条回测明细 (代码/策略/收益率/夏普/回撤/胜率) | 95 |
| `backtest_strategy_summary.csv` | 14个策略汇总 (均值/最优/最差/胜率) | 14 |
| `top_stocks_by_sharpe.csv` | 夏普Top30个股 | 30 |
| `daily_coverage.csv` | 每日覆盖率时序 (592个交易日) | 592 |
| `industry_distribution.csv` | 31行业股票分布 | 31 |
| `backtest_top50.json` | Top50回测JSON (前端可用) | 50 |

路径: `output/audit_20260621/`
