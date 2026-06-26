# Ardot V5/V6 调参增强页 · 设计 Spec

> **状态**: ⏳ 等待 Ardot MCP 连接后落地到 File 697128059547574
> **生成时间**: 2026-06-26 11:21
> **生成方式**: 基于现有 output/*.html 反向推导 + 真实数据

---

## 🎯 4 个新页面（接续现有 19 页 + Index Navigation）

| 顺序 | 页面 | 文件 | 画布 x 坐标 | 节点 ID 范围 | 估算节点 |
|------|------|------|-------------|--------------|----------|
| 20 | V5 调参详细 | `01_v5_tuning_detailed.md` | **34000** | 16:1 ~ 16:90 | ~90 |
| 21 | V6 vs V5 深度对比 | `02_v6_v5_deep_compare.md` | **35700** | 16:91 ~ 16:180 | ~90 |
| 22 | 多目标优化 | `03_multi_objective_optimization.md` | **37400** | 16:181 ~ 16:280 | ~100 |
| 23 | Walk-Forward OOS 验证 | `04_walk_forward_validation.md` | **39100** | 16:281 ~ 16:380 | ~100 |

**总新增节点**: 约 380 节点  
**总新增画布宽度**: 5100 px（34000 → 39100）  
**接续位置**: 现有 Index Navigation 页面右侧（x=32300 + 1700 间距 = 34000）

---

## 📐 设计语言统一规范

所有 4 个页面严格沿用前 19 页的 Dark Mode OLED + Fira Code/Sans：

| Token | 值 | 用途 |
|-------|-----|------|
| `bg_primary` | `#0F172A` | 主背景 |
| `bg_card` | `#1E293B` | 卡片背景 |
| `bg_elevated` | `#334155` | 浮层/分隔 |
| `text_primary` | `#F1F5F9` | 主文字 |
| `text_muted` | `#94A3B8` | 次要文字 |
| `up_red` | `#EF4444` | A股涨（红）|
| `down_green` | `#22C55E` | A股跌（绿）|
| `accent_v5` | `#F59E0B` | V5 琥珀（页面 20 主色）|
| `accent_v6` | `#8B5CF6` | V6 紫（页面 21-22 主色）|
| `accent_blue` | `#3B82F6` | 验证蓝（页面 23 主色）|
| `accent_gold` | `#FFD700` | 选中态金色 |

字体：Fira Code（数据）+ Fira Sans（标签），与前 19 页完全一致。

---

## 🔄 Ardot MCP 落地流程（待 MCP 可用）

每次 Ardot MCP 适配器可用时，按以下顺序执行：

### Step 1: open_design
```
mcp__ardot__open_design(fileUrl="https://ardot.tencent.com/file/697128059547574")
```
等 3 秒，确认 fetch_file_info 返回成功后继续。

### Step 2: 串行 4 个 batch_edit 创建 4 个页面

每页 4-6 个 batch，按 spec 中的"Ardot MCP 操作提示"小节分批：

```python
# 伪代码（实际通过 mcp__ardot__batch_edit 调用）
for page_spec in [v5_tuning_detailed, v6_v5_deep_compare, multi_objective, walk_forward]:
    for batch in page_spec.batches:
        batch_edit(operations=batch.ops)
        capture_layout(parentId=batch.parent_id, problemsOnly=True)
        # 如有问题 → 修复 → 重测（最多 2 轮）
```

### Step 3: 验证
```python
# 4 张整体截图
for page in pages:
    capture_screenshot(nodeIds=[page.root_id])
```

### Step 4: 推送交付
- 在 File 697128059547574 添加 changelog 节点（右下角小卡）
- 输出 README 链接到 FastAPI 文档

---

## 📊 预期产出

完成落地后，Ardot File 697128059547574 将包含：
- **23 个完整页面**（原 19 + 新 4）
- **~1,400 个节点**（原 ~1,000 + 新 ~380）
- **画布宽度**: 0 → 40800 px
- **统一设计语言**: Dark Mode OLED + Fira 字体 + A股红绿

可作为：
- 完整 A 股量化系统的视觉交付物
- 给团队 / 投资人演示的 deck
- 后续 Web 前端实现的视觉源

---

## ⚠️ 当前状态

**Ardot MCP 适配器**: 本会话（`99a93053-6ec0-4bbe-b6c5-3200f18ee38f`）中不可用
- 错误: `NO_ADAPTER: No adapter found for routeKey "99a93053..."`
- 重试 3 次后仍未加载

**已完成的替代交付物**:
- ✅ 4 个详细 spec markdown 文件（含精确坐标/尺寸/真实数据）
- ✅ 本 README 索引
- ✅ Git commit 推送至 origin/develop

**下次会话操作建议**:
1. 打开 WorkBuddy Desktop 客户端并加载 File 697128059547574
2. 新会话会分配新的 session_id，Ardot MCP 通常会自动连接
3. 让 agent 按本 README + 4 个 spec 文件落地

---

## 📁 文件结构

```
output/ardot_specs/
├── README.md                            # 本文件
├── 01_v5_tuning_detailed.md             # V5 调参详细页 spec (90 节点)
├── 02_v6_v5_deep_compare.md            # V6 vs V5 深度对比 spec (90 节点)
├── 03_multi_objective_optimization.md   # 多目标优化 spec (100 节点)
└── 04_walk_forward_validation.md        # Walk-Forward OOS spec (100 节点)
```

---

**生成时间**: 2026-06-26 11:21  
**关联 FastAPI 路由**: `/v5-tuning`, `/v6-compare`, `/tuning-panel`, `/walk-forward`（已落地）  
**关联 Tag**: v2.0-ardot-landing (2026-06-26 01:13)
