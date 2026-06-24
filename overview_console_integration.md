# AI QuantX 控制台整合 — 完成

## 用户需求
> 把 `Downloads/code (6).html` 这个生成的设计接入到我们这里使用，把相似的功能页签都替换成这些，并接入到我们的数据库内容，不用这里美股显示方式，改成 A 股显示方式，和涨是红，跌是绿。

## 改动摘要

### 新增
- `src/web/templates/console.html` — 自包含的 AI QuantX 风格控制台（850+ 行）
  - 7 个 sidebar tab: 总览 / 回测 / 对比 / 持仓 / A股行情 / 风控 / 回测记录
  - 4 个 KPI 卡片（每页）+ 多种 ECharts 图 + 数据表
  - **A股涨红跌绿**（`.pos=red/.neg=green`）
  - **K 线蜡烛**: 红涨/绿跌 + 成交量柱 + 60日默认缩放
- `src/web/routes/main.py` — `@router.get("/console")` 路由
- `base.html` — 顶部导航加 "⚡ 控制台" 入口
- `scripts/audit_console.py` — headless 自动化审计（7 tabs 切换 + 截图 + 错误捕获）
- `scripts/debug_market.py` / `debug_market2.py` — 排障脚本（已留作参考）

### 数据接入
| Tab | 数据源 API | 说明 |
|-----|-----------|------|
| 总览 | `/api/status` + `/api/backtest/results` | 95 笔回测聚合 |
| 回测 | `/api/strategies` | 13 个策略下拉 + 跳转 `/workbench` |
| 对比 | `/api/strategy/compare` | 14 个策略综合排名 + 柱状图 + 散点 |
| 持仓 | `/api/simulate/list` | run 列表 + 跳转 `/simulate` |
| A股行情 | `/api/stock/quote` + `/api/stock/kline` | 6 只主流股 watchlist + K线 + 热力图 + 价格走势 |
| 风控 | `/api/backtest/results` | 收益/回撤散点 + 回撤分布 |
| 日志 | `/api/backtest/results` | 完整 95 笔 + 策略筛选 + 仅盈利筛选 |

## 关键技术挑战

### 1. westock 并发锁
**症状**: 6+1 个并发 quote 请求卡死服务
**根因**: 每个 westock 调用 ~2s，服务端 westock 进程级锁
**解决**: `initMarket` 改成 `for ... await fetchQuote()` 串行预取；`selectStock` 等待 `_prefetching` 标志

### 2. 模板变量 `api_key` 注入
**症状**: `tojson` 报 `Object of type Undefined is not JSON serializable`
**根因**: `_get_global_context` 没注入 `api_key`
**解决**: `main.py:21-29` 加 `api_key` 注入

### 3. K线 蜡烛颜色翻转
**症状**: 原参考是绿涨红跌
**解决**: ECharts `itemStyle: {color:'#ef4444', color0:'#10b981'}` —— red=上涨, green=下跌（A 股惯例）

## 验证结果

### 路由审计 (audit_all_routes.py)
```
[bad] 0/25   ← 所有 25 个页面 200 / 0 错误
```

### Console 7 tabs 审计 (audit_console.py)
```
overview  canvases=2  errors=0
backtest  canvases=0  errors=0
compare   canvases=3  errors=0
positions canvases=0  errors=0
market    canvases=3  errors=0   ← K线 + 热力图 + 走势
risk      canvases=2  errors=0
logs      canvases=0  errors=0   ← 75 条记录表格
all_console_errors: {}
global_pageerrors: []
```

### 视觉验证（截图）
- 总览: 95 总回测 / +386.57% 最优 / 14 策略 / 0.61 平均夏普
- 收益分布柱状: 红色>0 / 绿色<0 ✓
- 最近回测表: +203.36% 红 / -25.10% 绿 ✓
- A股行情: 贵州茅台 1,207.68 -1.21%（绿色，因为下跌）✓
- K线: 120根日K，红涨绿跌 + 成交量
- 风控散点: 红色点=正收益，绿色点=负收益 ✓
- 日志表: 12条最新记录，涨红跌绿 ✓

## 访问入口
- 主页: http://localhost:5054/console
- 顶部导航新增: ⚡ 控制台（位于"工作台"前）
