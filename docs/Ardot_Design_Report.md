# Ardot 设计稿报告 — 19 页 + 4 新页

> **生成时间**: 2026-06-26 22:05  
> **生成方式**: 聚合 `output/ardot_specs/` 下所有 spec markdown  
> **目标 Ardot File**: `https://ardot.tencent.com/file/697128059547574`

---

## 📊 总览

| 指标 | 值 |
|------|---|
| 页面数 | **6** |
| 节点数 | **~580** |
| 画布宽度 | **10,200 px** (34000 → 44200) |
| 设计语言 | Dark Mode OLED + Fira Code/Sans |
| 配色规范 | A股 红涨 `#EF4444` / 绿跌 `#22C55E` |

## 🎨 页面清单

| # | 页面 | 文件 | x 坐标 | ID 范围 | 节点 | 主色 |
|---|------|------|--------|----------|------|------|
| 1 | V5 调参详细页 (v5_tuning_detailed) | `01_v5_tuning_detailed.md` | 34000 | 16:1 ~ 16:90 | ~90 | #F59E0B |
| 2 | V6 vs V5 深度对比页 (v6_v5_deep_compare) | `02_v6_v5_deep_compare.md` | 35700 | 16:91 ~ 16:180 | ~90 | - |
| 3 | 多目标优化页 (multi_objective) | `03_multi_objective_optimization.md` | 37400 | 16:181 ~ 16:280 | ~100 | - |
| 4 | Walk-Forward OOS 验证页 (walk_forward_validation) | `04_walk_forward_validation.md` | 39100 | 16:281 ~ 16:380 | ~100 | - |
| 5 | AI 牛市选股报告页 (ai_bull_stock_picking) | `05_ai_bull_stock_picking.md` | 40800 | 16:381 ~ 16:480 | ~100 | #FFD700 |
| 6 | 回测工作台 (backtest_workbench) | `06_backtest_workbench.md` | 42500 | 16:481 ~ 16:580 | ~100 | #06B6D4 |

## 📝 各页目的

### 1. V5 调参详细页 (v5_tuning_detailed)

**文件**: `01_v5_tuning_detailed.md`  
**目的**: 单页深度呈现 v5_hybrid 模型参数滑块 + 实时回测 + Top 组合对比  
**画布位置**: (34000, 0), 尺寸 1700×2400 px  
**主色调**: `#F59E0B`  
**节点 ID**: 16:1 ~ 16:90  

### 2. V6 vs V5 深度对比页 (v6_v5_deep_compare)

**文件**: `02_v6_v5_deep_compare.md`  
**目的**: V6 阈值放宽 vs V5 严苛阈值的逐档对比，强调 V6 在卡玛/胜率上的优势  
**画布位置**: (35700, 0), 尺寸 1700×2400 px  
**节点 ID**: 16:91 ~ 16:180  

### 3. 多目标优化页 (multi_objective)

**文件**: `03_multi_objective_optimization.md`  
**目的**: 在夏普/收益/回撤三个目标上做帕累托前沿 + 网格扫描，让用户交互选择最优组合  
**画布位置**: (37400, 0), 尺寸 1700×2600 px  
**节点 ID**: 16:181 ~ 16:280  

### 4. Walk-Forward OOS 验证页 (walk_forward_validation)

**文件**: `04_walk_forward_validation.md`  
**目的**: 滚动窗口 OOS 验证，避免过拟合；展示策略在时间外样本上的真实表现  
**画布位置**: (39100, 0), 尺寸 1700×2400 px  
**节点 ID**: 16:281 ~ 16:380  

### 5. AI 牛市选股报告页 (ai_bull_stock_picking)

**文件**: `05_ai_bull_stock_picking.md`  
**目的**: AI 智能选股报告单页 — 牛市主线识别 + 多策略共振 + 候选股池 + 实时榜单  
**画布位置**: (40800, 0), 尺寸 1700×2400 px  
**主色调**: `#FFD700`  
**节点 ID**: 16:381 ~ 16:480  

### 6. 回测工作台 (backtest_workbench)

**文件**: `06_backtest_workbench.md`  
**目的**: 单页集成回测三阶段（参数配置 → 实时进度 → 结果分析），支持多策略对比 + 历史回溯  
**画布位置**: (42500, 0), 尺寸 1700×2400 px  
**主色调**: `#06B6D4`  
**节点 ID**: 16:481 ~ 16:580  

## 🎨 设计语言统一规范

| Token | 值 | 用途 |
|-------|-----|------|
| `bg_primary` | `#0F172A` | 主背景 |
| `bg_card` | `#1E293B` | 卡片背景 |
| `bg_elevated` | `#334155` | 浮层/分隔 |
| `text_primary` | `#F1F5F9` | 主文字 |
| `text_muted` | `#94A3B8` | 次要文字 |
| `up_red` | `#EF4444` | A股涨（红）|
| `down_green` | `#22C55E` | A股跌（绿）|
| `accent_v5` | `#F59E0B` | V5 琥珀 |
| `accent_v6` | `#8B5CF6` | V6 紫 |
| `accent_blue` | `#3B82F6` | 验证蓝 |
| `accent_gold` | `#FFD700` | 选中态金色 |

字体: Fira Code（数据）+ Fira Sans（标签）

## 🔄 Ardot MCP 落地步骤

### Step 1: 打开设计文件
```python
mcp__ardot__open_design(
    fileUrl="https://ardot.tencent.com/file/697128059547574"
)
```

等 3 秒, 确认 `fetch_file_info` 返回成功后继续.

### Step 2: 串行创建所有页面 (按 x 坐标递增)

每页 4-6 个 batch, 按 spec 中的 "Ardot MCP 操作提示" 小节分批:

```python
for spec in all_specs:  # 按 x 坐标排序
    for batch in spec.batches:
        mcp__ardot__batch_edit(operations=batch.ops)
        mcp__ardot__capture_layout(parentId=batch.parent_id, problemsOnly=True)
        if problems: fix and retry (max 2 rounds)
```

### Step 3: 验证截图

```python
for spec in all_specs:
    mcp__ardot__capture_screenshot(nodeIds=[spec.root_id])
```

### Step 4: 推送交付

- 在 File 697128059547574 添加 changelog 节点 (右下角小卡)
- 输出本报告链接到 FastAPI 文档

## ⚠️ 当前状态

**Ardot MCP 适配器**: 当前 WorkBuddy 会话中不可用
- 错误: `NO_ADAPTER: No adapter found for routeKey`
- 重试 3 次后仍未加载

**已完成的替代交付物**:
- ✅ 6 个详细 spec markdown 文件
- ✅ 本报告 (`docs/Ardot_Design_Report.md`)
- ✅ Git commit 推送至 `origin/develop`

**下次会话操作建议**:
1. 打开 WorkBuddy Desktop 客户端并加载 File 697128059547574
2. 新会话会分配新的 session_id, Ardot MCP 通常会自动连接
3. 让 agent 按本报告 + 6 个 spec 文件落地

---

**生成时间**: 2026-06-26 22:05  
**关联 FastAPI 路由**: 全部已落地 (28 独立页 + 6 关键 API 健康 100%)  
**关联 Tag**: `v2.0-ardot-landing` (2026-06-26 01:13)  
