# 剩余任务清单 — 夏普比率冲刺 v1.1

> 目标: 年化>15%, 夏普>1.0, 回撤<-25%, 交易≥50笔  
> 当前: v6年化+15.22% ✅ / 回撤-21.99% ✅ / 交易132笔 ✅ / 夏普0.54 ❌

---

## 🔥 数据升级 (2026-06-18)

20个CSV已上传Git LFS → `A股全市场数据/` 目录 (637MB)

| 关键新增 | 大小 | 内容 |
|----------|------|------|
| **finance_summary** | 84MB | 63,008行季度财报: ROE/ROETTM/EPS/NAPS/DebtAssetsRatio/**TotalShareholderEquity** 等100字段，**100%覆盖** |
| **stock_profile** | 2MB | 5,066只股票: industry/sector/listedDate/regCapital |
| **margin_trading** | 5.7MB | 76,072行: 融资融券(融资余额/买入额/融券余额) |
| **chip_distribution** | 312KB | 5,250行: 筹码获利比例/平均成本/集中度 |
| **block_trade** | 13MB | 大宗交易明细 |
| **dragon_tiger** | 2.9MB | 龙虎榜明细 |
| **technical_indicators** | 252MB | 技术指标全量(MA/MACD/KDJ/RSI/BOLL等) |

**影响**: 之前 finance_snapshot_v2 手工计算的 ROE/EPS/BPS/debt_ratio 全部可替换为 finance_summary 真实数据。基本面评分器质量大幅提升。

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

## 优先级排序 (v1.1 数据升级后)

| 优先级 | Issue | 任务 | 预期夏普提升 | 工作量 | 备注 |
|--------|-------|------|-------------|--------|------|
| 🔴 P0 | [#53](https://github.com/QingSongTears/quant-trading-system/issues/53) | **0.1 重建quant.db** (finance_summary替代finance_snapshot) | 数据基础 | 中 | 100字段→真正基本面 |
| 🔴 P0 | [#54](https://github.com/QingSongTears/quant-trading-system/issues/54) | T1.1 放宽v6阈值 | +0.10~0.20 | 小 | 信号量32→80+ |
| 🔴 P0 | [#55](https://github.com/QingSongTears/quant-trading-system/issues/55) | T2.1 资金面评分器 | +0.20~0.30 | 中 | margin_trading/fund_flow双数据源 |
| 🟡 P1 | [#56](https://github.com/QingSongTears/quant-trading-system/issues/56) | T2.0 基本面评分v3 (基于finance_summary真实数据) | +0.15~0.25 | 中 | ROE/EPS/BPS/行业分组 |
| 🟡 P1 | [#57](https://github.com/QingSongTears/quant-trading-system/issues/57) | T3.1 动态仓位分配 | +0.10~0.20 | 中 | ATR/波动率加权 |
| 🟢 P2 | [#58](https://github.com/QingSongTears/quant-trading-system/issues/58) | T2.2 筹码面评分器 | +0.05~0.15 | 小 | chip_distribution数据就绪 |
| 🟢 P2 | [#59](https://github.com/QingSongTears/quant-trading-system/issues/59) | T3.2 行业暴露控制 | 降低尾部风险 | 中 | stock_profile提供行业分类 |
| 🟢 P2 | [#60](https://github.com/QingSongTears/quant-trading-system/issues/60) | T5.1 参数扫描框架 | 系统化提升 | 中 | 网格搜索/贝叶斯 |
| 🔵 P3 | [#61](https://github.com/QingSongTears/quant-trading-system/issues/61) | T2.3 龙虎榜机构评分 | +0.05~0.10 | 中 | dragon_tiger数据就绪 |
| 🔵 P3 | [#62](https://github.com/QingSongTears/quant-trading-system/issues/62) | T4.1 技术指标表导入 | 加速回测 | 中 | 252MB预计算指标 |
| 🔵 P3 | [#63](https://github.com/QingSongTears/quant-trading-system/issues/63) | T5.2 Walk-Forward | 验证稳健性 | 中 | 滚动窗口验证 |

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
