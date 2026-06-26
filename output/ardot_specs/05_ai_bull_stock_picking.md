# Ardot Page Spec · AI 牛市选股报告页 (ai_bull_stock_picking)

> **目的**: AI 智能选股报告单页 — 牛市主线识别 + 多策略共振 + 候选股池 + 实时榜单
> **对应源**: 需新建 (当前 `bull-report.html` / `bull_backtest_report.html` 是回测视角，缺 AI 选股入口)
> **数据源**: `quant.db` 的 `stock_score` / `prediction_record` / `strategy_backtest` + `scripts/bull_resonance/*.json`

---

## 📐 基础信息

| 项目 | 值 |
|------|---|
| 节点 ID 范围 | `16:381` ~ `16:480`（约 100 节点）|
| 画布位置 (x, y) | **(40800, 0)** ← 接 walk_forward_validation 右侧 |
| 页面尺寸 | 1700 × 2400 px（5 个 row）|
| 设计语言 | Dark Mode OLED + Fira Code/Sans（沿用前 19 页）|
| 主题色 | bg `#0F172A` / card `#1E293B` / 红涨 `#EF4444` / 绿跌 `#22C55E` |
| 主色调 | 金色 `#FFD700`（AI 选股标识色）|

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
  accent_ai: '#FFD700'   # AI 金色（页面 24 主色）
  accent_blue: '#3B82F6' # 策略共振蓝
  accent_pink: '#EC4899' # 牛股标识
  bull_zone: 'rgba(255,215,0,0.15)' # 牛市高亮背景

typography:
  hero_kpi: 'Fira Code, 96px, Bold'
  section_title: 'Fira Sans, 36px, SemiBold'
  body: 'Fira Sans, 18px, Regular'
  data_label: 'Fira Sans, 14px, Medium, uppercase'
  data_value: 'Fira Code, 22px, Regular'
  stock_code: 'Fira Code, 28px, Bold'
  signal_tag: 'Fira Sans, 12px, Medium, uppercase'
```

---

## 🗂 布局（5 个 Row，Bento Grid）

```
┌─────────────────────────────────────────────────────────────┐
│  ROW 1: Top Bar (60px tall, full width)                     │
│  [🤖 AI 牛市选股] [主线: 七维共振] [更新: 2026-06-26 11:30]   │
├─────────────────────────────────────────────────────────────┤
│  ROW 2: 牛市状态 Hero (200px)                                │
│  [状态: 🐂 偏多] [主线强度 78] [候选股 32] [预期年化 22%]      │
├─────────────────────────────────────────────────────────────┤
│  ROW 3: 策略共振矩阵 (560px)                                  │
│  左 (40%): 13 策略 × 当前命中表（红绿热力图）                  │
│  右 (60%): 七维共振雷达图 (top 3 候选股)                      │
├─────────────────────────────────────────────────────────────┤
│  ROW 4: Top 10 候选股池 (700px)                              │
│  表: 代码|名称|现价|涨跌|量比|夏普|共振|AI评分|信号            │
│  可点行 → 弹出 K线+研报摘要+一键加自选                       │
├─────────────────────────────────────────────────────────────┤
│  ROW 5: AI 研报摘要 + 操作建议 (600px)                        │
│  左: 行业主线 + 龙头识别                                       │
│  右: 风险提示 + 调仓建议                                       │
├─────────────────────────────────────────────────────────────┤
│  ROW 6: Footer (40px)                                        │
│  [AI 免责] [数据源] [刷新频率 5min]                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧩 详细组件清单

### Row 1 · Top Bar (`16:381`, full width × 60)
- 背景：`#0F172A` (border-bottom 1px `#FFD700`)
- 左：`🤖 AI 牛市选股报告 · 七维共振主线` (28px, white)
- 中：`● 实时同步 · 下一波信号 5min` (金色 dot + 14px text)
- 右：`⏱ 2026-06-26 11:30:08 · 全市场 5,569 只 · 命中 32` (14px, muted)

### Row 2 · 牛市状态 Hero (`16:382`-`16:385`, 4 张卡片)
- 4 张等宽卡 (414×180)
  - **状态卡**: 🐂 偏多（绿底金边 + 金色大字）
  - **主线强度**: 78 / 100 (圆形进度环 + 数字)
  - **候选股池**: 32 只 (带迷你 sparkline)
  - **预期年化**: +22% (3 月回测均值)

### Row 3 · 策略共振矩阵 (`16:386`-`16:400`)
- **左 (40%)**: 13 策略 × 4 时段命中热力图
  - 行：13 个策略名（V5/V6/七维共振/均线金叉/综合多信号/...）
  - 列：5d/10d/20d/60d 命中数
  - 单元格：红 `#EF4444` 涨 / 绿 `#22C55E` 跌 / 灰 `#475569` 无信号
  - hover: 弹 tooltip 显示具体命中股票列表
- **右 (60%)**: Top 3 候选股七维雷达图
  - 3 个雷达图叠加（不同颜色：gold/pink/blue）
  - 维度：动量/趋势/资金/情绪/估值/质量/技术
  - 中心显示股票代码 + 现价 + 当日涨跌

### Row 4 · Top 10 候选股池表 (`16:401`-`16:420`)
- 表头：`代码|名称|现价|涨跌|量比|夏普|共振分|AI 评分|信号`
- 行高 48px，hover 高亮金色
- 点击行 → 展开抽屉 (K线 + 研报 + 同业)
- 涨跌列：涨红跌绿
- 信号列：彩色 chip（🟢买入 / 🟡观望 / 🔴回避）
- 底部汇总：候选股池总览统计

### Row 5 · AI 研报摘要 (`16:421`-`16:435`)
- **左 (50%)**: 行业主线识别
  - 当前主线：AI 算力 / 高股息 / 出海链（3 个 chip + 强度条）
  - 龙头识别：每条主线 2 只代表股
  - 主线轮动预警（vs 上一周期）
- **右 (50%)**: 操作建议
  - 总仓位建议：65% (绿色 + 进度条)
  - 行业配置：30/30/20/20 (饼图)
  - 风控阈值：单股 ≤ 8%，止损 -7%
  - 一键调仓按钮（模拟盘）

### Row 6 · Footer (`16:436`, full width × 40)
- 居中：`AI 建议仅供参考，不构成投资依据 · 数据源: quant.db · 5min 刷新`

---

## 📊 数据接口（待后端落地）

```python
# 已有
GET /api/strategies                  # 13 个策略列表
GET /api/strategies/compare          # 策略表现对比
GET /api/stock/screener              # 筛选器

# 待新增 (优先级 P0)
GET /api/ai/bull/status              # 牛市状态 + 主线强度
GET /api/ai/bull/resonance-matrix    # 13策略 × 4时段 命中矩阵
GET /api/ai/bull/top-picks?n=10      # Top N 候选股池
GET /api/ai/bull/radar/{code}        # 单股七维雷达数据
GET /api/ai/bull/mainline            # 行业主线 + 龙头股
GET /api/ai/bull/advice              # 仓位建议
```

**数据格式样例** (Top picks):
```json
{
  "success": true,
  "data": [
    {
      "code": "300750",
      "name": "宁德时代",
      "price": 245.6,
      "change_pct": 3.21,
      "volume_ratio": 1.8,
      "sharpe": 0.45,
      "resonance_score": 0.87,
      "ai_score": 0.92,
      "signals": ["七维共振", "均线金叉", "主力净流入"]
    }
  ]
}
```

---

## 🧠 核心算法（待实现）

### 主线强度评分
```python
def mainline_strength(market_data):
    # 1. 7 维度加权（动量/趋势/资金/情绪/估值/质量/技术）
    # 2. 当日涨停数 + 板块联动 + 北向流入
    # 3. 输出 0-100 分（≥70 强 / 40-70 中 / <40 弱）
```

### 候选股筛选
```python
def top_picks(n=10):
    # 1. 七维共振策略最新信号
    # 2. AI 评分 ≥ 0.8
    # 3. 量比 ≥ 1.5 (资金活跃)
    # 4. 夏普 ≥ 0.3
    # 5. 按 AI 评分降序，取 Top N
```

### 行业龙头识别
```python
def mainline_leader(sector):
    # 1. 板块内市值 Top 20%
    # 2. 近 20d 涨幅 Top 20%
    # 3. 北向净流入 Top 20%
    # 4. 三项交集 → 龙头候选
```

---

## 🔄 Ardot MCP 落地流程

每次 Ardot MCP 适配器可用时，按以下顺序执行：

### Step 1: open_design
- File ID: `697128059547574`
- Page name: `24. AI 牛市选股报告 (ai_bull_stock_picking)`
- 父节点：`16:380` (接 walk_forward_validation 末尾)

### Step 2: create_frame × 5
- Frame 1 (Top Bar): `16:381` 1700×60 @ (40800, 0)
- Frame 2 (Hero KPI): `16:382-385` 4×(414×180) @ (40800, 80)
- Frame 3 (Resonance): `16:386-400` 1700×560 @ (40800, 280)
- Frame 4 (Top 10): `16:401-420` 1700×700 @ (40800, 860)
- Frame 5 (Advice): `16:421-435` 1700×600 @ (40800, 1580)
- Frame 6 (Footer): `16:436` 1700×40 @ (40800, 2200)

### Step 3: batch_edit (每组件约 5-10 节点)
- 共 100 节点，分 4 批执行 (25 节点/批)

### Step 4: 验证
- 截图 `screenshots/ardot/page_24_*.png`
- 对比本 spec 的设计 token + 布局

---

## ✅ 验收标准

- [ ] 5 个 row 全部对齐
- [ ] 13 策略 × 4 时段热力图渲染正常（红绿）
- [ ] Top 10 表 hover 高亮 + 点击展开抽屉
- [ ] 雷达图 3 只股票叠加清晰
- [ ] AI 研报 + 操作建议完整可读
- [ ] 后端 API 全部 200
- [ ] 5min 自动刷新
- [ ] 涨红跌绿严格遵守

---

**Spec 生成时间**: 2026-06-26
**状态**: ⏳ 等 Ardot MCP 落地
**依赖**: 后端 6 个 API 端点（详见上方接口列表）
