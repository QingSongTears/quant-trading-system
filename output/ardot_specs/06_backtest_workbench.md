# Ardot Page Spec · 回测工作台 (backtest_workbench)

> **目的**: 单页集成回测三阶段（参数配置 → 实时进度 → 结果分析），支持多策略对比 + 历史回溯
> **对应源**: 已有 `src/web/templates/workbench.html` (已开发)，Ardot 出设计稿做视觉对齐
> **数据源**: `quant.db` 的 `strategy_backtest` / `backtest_history` + `scripts/backtest/*.json`

---

## 📐 基础信息

| 项目 | 值 |
|------|---|
| 节点 ID 范围 | `16:481` ~ `16:580`（约 100 节点）|
| 画布位置 (x, y) | **(42500, 0)** ← 接 AI 牛市选股报告右侧 |
| 页面尺寸 | 1700 × 2400 px（5 个 row）|
| 设计语言 | Dark Mode OLED + Fira Code/Sans（沿用前 19 页）|
| 主题色 | bg `#0F172A` / card `#1E293B` / 红涨 `#EF4444` / 绿跌 `#22C55E` |
| 主色调 | 青色 `#06B6D4`（回测工作台标识色）|

---

## 🎨 设计 Token

```yaml
colors:
  bg_primary: '#0F172A'
  bg_card: '#1E293B'
  bg_elevated: '#334155'
  text_primary: '#F1F5F9'
  text_muted: '#94A3B8'
  up_red: '#EF4444'      # A股红 = 涨
  down_green: '#22C55E'  # A股绿 = 跌
  accent_workbench: '#06B6D4'  # 工作台青色
  accent_purple: '#8B5CF6'     # 进度条
  accent_emerald: '#10B981'    # 完成态
  warn_amber: '#F59E0B'        # 警告
  error_rose: '#F43F5E'        # 错误
  progress_bg: '#1E293B'

typography:
  hero_kpi: 'Fira Code, 96px, Bold'
  section_title: 'Fira Sans, 36px, SemiBold'
  body: 'Fira Sans, 18px, Regular'
  data_label: 'Fira Sans, 14px, Medium, uppercase'
  data_value: 'Fira Code, 22px, Regular'
  log_line: 'Fira Code, 13px, Regular'
  progress_pct: 'Fira Code, 28px, Bold'
```

---

## 🗂 布局（5 个 Row，Bento Grid）

```
┌─────────────────────────────────────────────────────────────┐
│  ROW 1: Top Bar (60px tall, full width)                     │
│  [⚙️ 回测工作台] [模式: Lab|View|Compare] [运行 ID: 2026...123]│
├─────────────────────────────────────────────────────────────┤
│  ROW 2: 配置区 Hero (240px)                                  │
│  [策略▾] [股票池▾] [日期▾] [资金] [滑块] [▶ 开始回测] [⏸ 暂停] │
├─────────────────────────────────────────────────────────────┤
│  ROW 3: 实时进度 + 日志 (560px)                              │
│  左 (50%): 多任务进度卡片 (4-6 个并发回测)                    │
│  右 (50%): 实时日志流 (滚动 tail -f)                          │
├─────────────────────────────────────────────────────────────┤
│  ROW 4: 结果对比 (700px)                                     │
│  上 (40%): 4 策略净值曲线 (叠加)                              │
│  下 (60%): 4 策略指标对比表 (夏普/年化/回撤/胜率/盈亏比)       │
├─────────────────────────────────────────────────────────────┤
│  ROW 5: 历史回测 + 一键复用 (500px)                          │
│  上: 最近 10 次回测记录 (table)                              │
│  下: 一键复用按钮组 (复制参数 → 新建回测)                     │
├─────────────────────────────────────────────────────────────┤
│  ROW 6: Footer (40px)                                        │
│  [并发数] [CPU 占用] [内存] [已运行 12h 30min]                │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧩 详细组件清单

### Row 1 · Top Bar (`16:481`, full width × 60)
- 背景：`#0F172A` (border-bottom 1px `#06B6D4`)
- 左：`⚙️ 回测工作台 · 三阶段一体化` (28px, white)
- 中：`[Lab|View|Compare]` 模式切换（3 个 chip，当前态青色填充）
- 右：`🆔 RUN-2026-06-26-001 · 4 任务并发` (14px, muted)

### Row 2 · 配置区 Hero (`16:482`-`16:490`, full width × 240)
- 卡片背景 `#1E293B` + 青色左边框 4px
- 表单元素 (左到右排列):
  - **策略下拉**: 13 策略（带颜色 chip：V5/V6/七维/均线/...）
  - **股票池**: 全市场 / 自定义 / Top100 / 行业
  - **日期范围**: 双日期选择器 (默认近 3 年)
  - **初始资金**: 数字输入 (默认 100万)
  - **滑块**: 7 维权重（仅特定策略显示）
- 操作按钮:
  - **[▶ 开始回测]** (青色填充, 28px 高)
  - **[⏸ 暂停]** (次要)
  - **[⏹ 停止]** (次要)

### Row 3 · 实时进度 + 日志 (`16:491`-`16:510`)
- **左 (50%)**: 多任务进度卡片网格 (2 列 × N 行)
  - 每卡片：策略名 + 进度条 + 已用时间 + 当前股票
  - 状态: 🟢 完成 / 🟡 运行中 (脉冲) / ⚪ 等待 / 🔴 失败
  - hover: 显示详细参数
- **右 (50%)**: 实时日志流
  - tail -f 风格，自动滚到底部
  - 不同级别用颜色 (info=灰/warn=琥珀/error=玫红)
  - 顶部 filter chips (All/Info/Warn/Error)

### Row 4 · 结果对比 (`16:511`-`16:530`)
- **上 (40%)**: 净值曲线图 (ECharts line, 4 条线叠加)
  - 4 策略分别用 4 种颜色 (青/紫/琥珀/粉)
  - 基准 (沪深 300) 用灰色虚线
  - hover 十字光标 + tooltip
- **下 (60%)**: 指标对比表
  - 列：策略|年化|夏普|最大回撤|胜率|盈亏比|交易次数|评分
  - 行：4 个策略
  - 最佳值用 🏆 金色高亮
  - 点击行 → 展开交易明细

### Row 5 · 历史回测 + 复用 (`16:531`-`16:555`)
- **上 (50%)**: 历史回测记录表
  - 列：时间|策略|股票池|年化|夏普|操作
  - 行：最近 10 次
  - 操作列：[查看] [复用] [删除]
- **下 (50%)**: 一键复用面板
  - 选中的历史记录参数预览
  - 按钮：[📋 复制并新建] [📊 加入对比] [📥 导出 CSV]

### Row 6 · Footer (`16:556`, full width × 40)
- 状态条：4 个监控指标
  - `[🟢 并发 4/4] [CPU 67%] [内存 4.2G] [⏱ 12h 30min]`

---

## 📊 数据接口（待后端确认）

```python
# 已有
GET  /api/strategies                  # 13 策略
GET  /api/backtest/history?limit=10   # 历史回测
GET  /api/backtest/results            # 当前结果
POST /api/backtest/run                # 启动回测
POST /api/backtest/pause              # 暂停
POST /api/backtest/stop               # 停止
GET  /api/backtest/status             # 实时状态

# 待新增 (P0)
GET  /api/workbench/progress          # 多任务进度 (SSE 流)
GET  /api/workbench/logs?run_id=X     # 实时日志 (SSE 流)
GET  /api/workbench/compare?n=4       # 多策略对比
POST /api/workbench/reuse             # 一键复用历史参数
```

**进度推送样例 (SSE)**:
```
event: progress
data: {"run_id":"...","strategy":"V5","pct":45,"current_stock":"600519","eta":"12min"}

event: log
data: {"ts":"2026-06-26 11:30:08","level":"info","msg":"开始回测 600519 ...","strategy":"V5"}

event: status
data: {"running":4,"queued":2,"completed":12,"failed":1}
```

---

## 🧠 核心交互流程

### 启动回测
```
1. 用户配置参数 → 点 [开始回测]
2. POST /api/backtest/run → 返回 run_id
3. 前端跳转 Row 3，订阅 SSE /api/workbench/progress
4. 日志流开始刷新，进度条更新
5. 单个策略完成 → 写入历史
4 个全部完成 → 自动跳到 Row 4 对比
```

### 一键复用
```
1. Row 5 点 [复用] 某历史记录
2. POST /api/workbench/reuse 复制参数
3. 自动填到 Row 2 配置区
4. 用户可微调 → 点 [开始回测] 重新跑
```

---

## 🔄 Ardot MCP 落地流程

### Step 1: open_design
- File ID: `697128059547574`
- Page name: `25. 回测工作台 (backtest_workbench)`
- 父节点：`16:480` (接 AI 牛市选股报告末尾)

### Step 2: create_frame × 6
- Frame 1 (Top Bar): `16:481` 1700×60 @ (42500, 0)
- Frame 2 (Config): `16:482-490` 1700×240 @ (42500, 80)
- Frame 3 (Progress+Logs): `16:491-510` 1700×560 @ (42500, 340)
- Frame 4 (Result Compare): `16:511-530` 1700×700 @ (42500, 920)
- Frame 5 (History+Reuse): `16:531-555` 1700×500 @ (42500, 1640)
- Frame 6 (Footer): `16:556` 1700×40 @ (42500, 2160)

### Step 3: batch_edit (每组件约 5-10 节点)
- 共 100 节点，分 4 批执行

### Step 4: 验证
- 截图 `screenshots/ardot/page_25_*.png`
- 对比 workbench.html 实际渲染

---

## 🎯 与现有 workbench.html 对齐

| 项目 | 现有 workbench.html | Ardot 设计稿 |
|------|---------------------|--------------|
| 模式切换 | ✅ Lab/View/Compare | ✅ 同 |
| 实时进度 | ✅ 并行卡片 | ✅ 同 + 颜色更鲜明 |
| 日志流 | ✅ tail -f | ✅ + 级别 filter |
| 净值对比 | ✅ ECharts | ✅ + 4 策略固定配色 |
| 历史复用 | ❌ 缺 | ✅ 新增 |
| Footer 监控 | ❌ 缺 | ✅ 新增 |

**对齐原则**: 保留功能 + 视觉精修（用 Ardot 青色 `#06B6D4` 统一品牌色）

---

## ✅ 验收标准

- [ ] 6 个 row 全部对齐
- [ ] 模式切换 chip 高亮清晰
- [ ] 并行进度卡片 4-6 个网格展示
- [ ] 日志流 tail 风格 + 颜色分级
- [ ] 净值曲线 4 线叠加 + 基准虚线
- [ ] 指标对比表 🏆 高亮最佳值
- [ ] 历史记录 [复用] 按钮工作
- [ ] Footer 4 监控指标实时刷新
- [ ] 后端 2 个新 API (progress SSE / reuse) 落地
- [ ] 涨红跌绿严格遵守

---

**Spec 生成时间**: 2026-06-26
**状态**: ⏳ 等 Ardot MCP 落地
**依赖**: 后端 2 个新 API (SSE progress / reuse)
**对齐文件**: `src/web/templates/workbench.html` (现有实现)
