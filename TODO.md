# 剩余任务清单 — 夏普比率冲刺 v1.0

> 目标: 年化>15%, 夏普>1.0, 回撤<-25%, 交易≥50笔  
> 当前: v6年化+15.22% ✅ / 回撤-21.99% ✅ / 交易132笔 ✅ / 夏普0.54 ❌

---

## 阶段一: 提升v6信号量（让多维融合有操作空间）

### T1.1 放宽v6超卖阈值
- **问题**: RSI14≤30 + RSI6≤20 + BB≤0.08 + DD60≤-15% 四条件太严，40%日有信号、平均仅3只
- **行动**: 分档测试
  - 档A: RSI14≤40, RSI6≤25
  - 档B: RSI14≤35, RSI6≤20  
  - 档C: 放宽MAX_DRAWDOWN_60D到-10%
  - 对比各档信号量、收益、夏普变化
- **预期**: 信号量从32→80+，基本面融合可见效果
- **文件**: `src/strategies/v6_reversal_selection.py`

### T1.2 v6信号评分优化
- **问题**: 当前评分权重没有经过系统性优化
- **行动**: 
  - 调整SCORE_RSI_WEIGHT/SCORE_BB_WEIGHT/SCORE_DD_WEIGHT
  - 添加反转强度评分（当日涨幅×量比归一化）
- **文件**: `src/strategies/v6_reversal_selection.py`

---

## 阶段二: 接入更多正交维度（提升夏普的核心）

### T2.1 资金面评分器(fund_flow_scorer)接入
- **数据**: fund_flow_data表已就绪(268,418行, 2025-10~2026-06)
- **行动**:
  - 实现FundFlowScorer: 主力净流入、超大单净流入、连续流入天数等指标
  - 接入ScorerRegistry
  - v6+fund_flow融合回测
- **文件**: `src/scoring/fund_flow_scorer.py`（需新建）
- **优先级**: 🔴 高 — 资金面与技术面天然正交

### T2.2 机构面评分器(lhb_institutional)接入
- **数据**: tdrive有lhb_institutional.csv(275KB, 龙虎榜机构席位)
- **行动**:
  - 导入lhb_institutional到quant.db
  - 实现InstitutionalScorer: 机构买入占比、净买入额、上榜频率
- **文件**: 新建`scripts/import_lhb_data.py` + `src/scoring/institutional_scorer.py`

### T2.3 新闻情绪评分器(news_event)接入
- **数据**: tdrive有em_global_news.csv(750KB, 东方财富全球新闻)
- **行动**:
  - 导入新闻数据
  - 实现简单的关键词情绪评分
- **优先级**: 🟡 中 — 文本数据处理复杂度高

---

## 阶段三: 组合优化

### T3.1 动态仓位分配
- **问题**: 当前等权分配，波动率更高的股票拖累夏普
- **行动**: 基于ATR/波动率的动态仓位（低波动多配）
- **文件**: `src/backtest/portfolio_engine.py`

### T3.2 行业暴露控制
- **问题**: v6可能集中在特定行业（如地产、银行），集中风险大
- **行动**: 添加行业分散约束，单行业不超过30%
- **依赖**: 需要行业分类数据（可从stock_basic或外部获取）

---

## 阶段四: 补充数据

### T4.1 导入tdrive剩余CSV
- lhb_institutional.csv → lhb_institutional表
- dragon_tiger.csv → dragon_tiger表  
- margin_trading.csv → margin_trading表
- technical_indicators.csv (263MB) → 评估是否有增量价值
- combined_3d_scores.csv (51MB) → 评估是否可用
- block_trade.csv → 大宗交易表
- shareholder_count.csv → 股东人数表
- **优先级**: 🟡 — lhb_institutional最优先(机构面评分需要)

### T4.2 数据质量修复
- **问题**: finance_snapshot_v2的total_liabilities列数据异常(col_8 = 10×预期值)
- **行动**: 重新检查CSV列映射，或从其他数据源交叉验证
- **影响**: debt_ratio计算可能被扭曲（已通过clip缓解）

---

## 阶段五: 回测基础设施

### T5.1 参数扫描框架
- **问题**: 手动调参效率低
- **行动**: 实现网格搜索/贝叶斯优化参数扫描
- **文件**: 新建`scripts/param_grid_search.py`

### T5.2 Walk-Forward回测
- **问题**: 固定回测窗口可能过拟合
- **行动**: 实现滚动窗口Walk-Forward验证（36个月训练→12个月测试→滚动）

---

## 优先级排序

| 优先级 | 任务 | 预期夏普提升 | 工作量 |
|--------|------|-------------|--------|
| 🔴 P0 | T1.1 放宽v6阈值 | +0.1~0.2 | 小 |
| 🔴 P0 | T2.1 资金面评分器 | +0.2~0.3 | 中 |
| 🟡 P1 | T3.1 动态仓位分配 | +0.1~0.2 | 中 |
| 🟡 P1 | T2.2 机构面评分器 | +0.1~0.2 | 中 |
| 🟢 P2 | T1.2 评分权重优化 | +0.05~0.1 | 小 |
| 🟢 P2 | T5.1 参数扫描 | 系统化提升 | 中 |
| 🟢 P2 | T3.2 行业暴露控制 | 降低尾部风险 | 中 |
| 🔵 P3 | T4.1 补充数据 | 数据基础 | 中 |
| 🔵 P3 | T5.2 Walk-Forward | 验证稳健性 | 中 |

---

## 夏普提升路线图

```
当前夏普 0.54
    │
    ├─ T1.1 放宽v6阈值 (+0.10) ──→ 0.64
    │   └─ T2.1 资金面融合 (+0.20) ──→ 0.84
    │       └─ T3.1 动态仓位 (+0.10) ──→ 0.94
    │           └─ T2.2 机构面 (+0.10) ──→ 1.04 ✅ 达标
    │
    └─ 备选: T5.1 参数扫描 → 系统化突破
```

---

*最后更新: 2026-06-17*
