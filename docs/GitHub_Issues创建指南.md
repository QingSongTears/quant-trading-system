# GitHub Issues — A股量化交易模型系统 · 19项研发任务

> 通过 GitHub Issues API 批量创建。每项任务对应一个 Issue，含标题、描述、标签、优先级。

---

## Phase 0: 环境与脚手架 [1项]

### Issue #1: [P0] T0: 项目环境初始化
**Labels**: `phase-0` `P0` `setup`  
**Assignee**: 待分配

**描述**:
```
搭建 Python 3.10+ 虚拟环境，安装所有依赖，验证项目结构可运行。

子任务:
- [ ] 创建 Python 虚拟环境，安装 requirements.txt
- [ ] 验证 AKShare 可正常调用（测试下载 1 只股票）
- [ ] 验证 Backtesting.py 可正常导入
- [ ] 运行 `python run.py --init` 确认数据库表创建成功
- [ ] 运行 `python run.py --check` 确认状态检测正常

验收标准:
- pip install -r requirements.txt 无报错
- python run.py --init 创建 6 张表
- python run.py --check 输出正确状态
```

---

## Phase 1: 数据层 [4项]

### Issue #2: [P0] T1: 数据库表结构创建与验证
**Labels**: `phase-1` `P0` `database`

**描述**:
```
验证和完善 src/models/database.py 中定义的 6 张表，确保索引和约束正确。

子任务:
- [ ] 验证表结构与 PRD §5.2 一致
- [ ] 添加 daily_price 复合索引 (code, trade_date)
- [ ] 验证 UNIQUE 约束阻止重复插入
- [ ] 编写 tests/test_database.py

验收标准:
- Base.metadata.create_all() 正确创建 6 张表
- 插入 10 万行耗时 < 5 秒
```

---

### Issue #3: [P0] T2: AKShare 全量下载引擎
**Labels**: `phase-1` `P0` `data`

**描述**:
```
完善 src/data/downloader.py，实现全市场 ~5500 只A股近 3 年日线数据一键下载。

子任务:
- [ ] 完善 fetch_stock_list() 市场分类逻辑
- [ ] 完善 _download_stock_history() 字段映射
- [ ] 实现指数退避重试 (max_retries=3)
- [ ] 实现进度回调 + 请求间隔控制 (0.8s)
- [ ] 实现断点续传
- [ ] 下载沪深300基准数据
- [ ] 编写 tests/test_downloader.py

验收标准:
- 下载覆盖率 ≥ 95%
- 单只失败不影响整体
- 总耗时 < 30 分钟
```

---

### Issue #4: [P1] T3: 增量更新与数据完整性校验
**Labels**: `phase-1` `P1` `data`

**描述**:
```
实现增量更新模式，仅下载缺失的最新交易日数据。下载后自动校验数据完整性。

子任务:
- [ ] 完善 download_incremental() 增量逻辑
- [ ] 实现 UPSERT 写入 (INSERT OR IGNORE)
- [ ] 数据完整性校验: 覆盖率/异常值/重复检测
- [ ] 输出校验报告
- [ ] 编写 tests/test_integrity.py
```

---

### Issue #5: [P1] T4: WeStock Data 集成
**Labels**: `phase-1` `P1` `data`

**描述**:
```
集成 WeStock Data (腾讯自选股 CLI) 作为补充数据源，用于实时行情和技术指标交叉校验。

子任务:
- [ ] 验证 npx westock-data-clawhub CLI 可正常执行
- [ ] 完善 get_kline()/get_technical()/verify_data_consistency()
- [ ] 实现 JSON 输出解析
- [ ] Node.js >= v18 环境检测
- [ ] 编写 tests/test_westock.py
```

---

## Phase 2: 回测引擎 [4项]

### Issue #6: [P0] T5: 策略基类实现
**Labels**: `phase-2` `P0` `backtest`

**描述**:
```
完善 BaseStrategy，确保 SMA/EMA/RSI/MACD/布林带/ATR 等技术指标计算正确。

子任务:
- [ ] 验证所有技术指标计算结果 (用 WeStock 交叉验证)
- [ ] 添加 highest()/lowest() 辅助方法
- [ ] 添加 is_limit_hit() 涨跌停检测
- [ ] 添加 A股最小交易单位检查 (100股)
- [ ] 编写 tests/test_base_strategy.py

验收标准:
- SMA(5) 与 WeStock 输出误差 < 0.1%
```

---

### Issue #7: [P0] T6: 回测引擎封装
**Labels**: `phase-2` `P0` `backtest`

**描述**:
```
完善 BacktestEngine，封装 Backtesting.py，输出标准化 BacktestReport。

子任务:
- [ ] 完善 run() 方法: 取数据→创建 Backtest→运行→收集统计
- [ ] 交易成本模拟: 佣金 + 印花税 + 滑点 + 最低佣金
- [ ] 基准对比: 计算同期沪深300收益率
- [ ] 构建净值曲线/交易明细 JSON
- [ ] 计算月度收益率
- [ ] 编写 tests/test_engine.py

验收标准:
- 双均线 × 000001 × 3年回测成功执行
- 核心指标与 Excel 计算误差 < 0.1%
```

---

### Issue #8: [P0] T7: 5个预设策略实现与验证
**Labels**: `phase-2` `P0` `strategies`

**描述**:
```
实现并验证 5 个经典预设策略，每个策略标注学术来源。

策略清单:
- [ ] 双均线交叉 (Murphy 1999)
- [ ] MACD 金叉死叉 (Appel 1979)
- [ ] RSI 超买超卖 (Wilder 1978)
- [ ] 布林带突破 (Bollinger 2001)
- [ ] 海龟交易法则 (Faith 2007)

验收标准:
- 5个策略均可注册，每个至少产生 1 笔交易
- 学术来源标注完整
- 无 IndexError 或除零错误
```

---

### Issue #9: [P1] T8: 回测报告生成与持久化
**Labels**: `phase-2` `P1` `backtest`

**描述**:
```
完善 report.py，实现报告→JSON→数据库持久化的完整链路。

子任务:
- [ ] 完善 report_to_chart_data() ECharts 格式输出
- [ ] 完善回撤/年度收益/月度热力图计算
- [ ] engine.run() 末尾自动持久化到 backtest_result 表
- [ ] 编写 tests/test_report.py
```

---

## Phase 3: Web 可视化 [5项]

### Issue #10: [P0] T9: FastAPI 基础框架搭建
**Labels**: `phase-3` `P0` `web`

**描述**:
```
验证 FastAPI + Jinja2 + 静态文件可正常运行。

子任务:
- [ ] 确认 run.py 启动 FastAPI 正常
- [ ] 验证 base.html 模板 (Bootstrap + ECharts CDN)
- [ ] 确认 6 个页面路由返回 200
- [ ] 添加全局 404/500 错误处理
```

---

### Issue #11: [P0] T10: 首页仪表盘
**Labels**: `phase-3` `P0` `web`

**描述**:
```
实现首页仪表盘，展示数据概览、最近回测、数据源声明。

子任务:
- [ ] 数据状态卡片 ×4
- [ ] 快捷操作卡片 ×3
- [ ] 最近回测列表 (最近 5 条)
- [ ] 数据源声明区域 (含 AI 免责标注)
- [ ] 空数据状态处理 (引导下载)
```

---

### Issue #12: [P0] T11: 数据管理页
**Labels**: `phase-3` `P0` `web`

**描述**:
```
实现数据管理页，支持触发下载、查看进度、数据源信息展示。

子任务:
- [ ] 数据覆盖概览卡片
- [ ] 下载控制区: 模式选择/按钮/进度条
- [ ] AJAX 触发下载 (/api/data/download)
- [ ] 下载历史列表
- [ ] 数据源详细说明表
- [ ] 错误状态处理
```

---

### Issue #13: [P0] T12: 回测执行与详情页
**Labels**: `phase-3` `P0` `web`

**描述**:
```
实现回测执行页和回测详情页（含 ECharts 图表渲染）。

回测执行页:
- [ ] 策略下拉选择 + 股票代码输入 + 日期选择 + 初始资金
- [ ] AJAX 提交→加载动画→结果摘要
- [ ] 交易成本说明底部展示

回测详情页:
- [ ] 核心指标卡片 (6个)
- [ ] 净值曲线 ECharts + 回撤曲线 ECharts
- [ ] 交易明细 DataTables
- [ ] 交易成本假设 + 基准对比
```

---

### Issue #14: [P1] T13: 多模型对比页
**Labels**: `phase-3` `P1` `web`

**描述**:
```
实现多策略回测结果横向对比页面。

子任务:
- [ ] 净值叠加图 (多策略线 + 基准线)
- [ ] 核心指标对比表 (最优值高亮)
- [ ] 雷达图 (5维: 收益/夏普/抗回撤/胜率/盈亏比)
- [ ] 对比选择器 (URL 参数 ?ids=1,2,3)
- [ ] 空状态处理
```

---

## Phase 4: 多模型管理 [3项]

### Issue #15: [P1] T14: 策略注册与动态加载
**Labels**: `phase-4` `P1` `strategies`

**描述**:
```
实现策略的 YAML 注册和 Python 动态加载机制。

子任务:
- [ ] 完善 strategies.yaml 解析逻辑
- [ ] 实现策略动态导入 (importlib)
- [ ] 策略参数验证 (类型 + 范围检查)
- [ ] 策略管理页 (Web 展示已注册策略)
- [ ] 策略配置同步到数据库
```

---

### Issue #16: [P1] T15: 批量回测
**Labels**: `phase-4` `P1` `backtest`

**描述**:
```
实现 BacktestEngine.run_batch() — 多策略 × 多股票批量回测。

子任务:
- [ ] 完善 run_batch() 循环调用 + 状态管理
- [ ] Web 界面多策略多股票选择
- [ ] 全部完成自动跳转对比页
- [ ] 个别失败不影响其余
```

---

### Issue #17: [P2] T16: 参数网格搜索
**Labels**: `phase-4` `P2` `backtest`

**描述**:
```
实现 BacktestEngine.run_grid_search() — 自动遍历参数组合。

子任务:
- [ ] 笛卡尔积遍历 + 按指标排序
- [ ] Web 参数范围配置表单
- [ ] 搜索结果展示 (表格 + 最优高亮)
- [ ] 进度显示
```

---

## Phase 5: 打磨与测试 [2项]

### Issue #18: [P0] T17: 端到端集成测试
**Labels**: `phase-5` `P0` `testing`

**描述**:
```
全流程端到端测试: 下载 → 存储 → 回测 → 展示。

测试场景:
- [ ] 场景1: 首次使用 → 下载10只 → 回测 → 详情 → 对比
- [ ] 场景2: 已有数据 → 增量更新 → 批量回测 → 多模型对比
- [ ] 场景3: 异常 → 无效代码 → 空数据 → 数据库损坏提示
```

---

### Issue #19: [P1] T19: 文档与 README
**Labels**: `phase-5` `P1` `docs`

**描述**:
```
编写 README.md 和策略开发指南。

子任务:
- [ ] README: 快速开始 / 目录结构 / 技术栈 / FAQ
- [ ] 策略开发指南: 如何新增自定义策略
- [ ] 补充代码注释中的 AI 生成标注
```

---

## 批量创建命令

将以上 19 个 Issue 通过 GitHub Issues API 批量创建。参见下方脚本：

```bash
#!/bin/bash
REPO="YOUR_USERNAME/quant-trading-system"
source get_token.sh github

# Phase 0
create_issue() { curl -s -X POST -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Content-Type: application/json" "https://api.github.com/repos/${REPO}/issues" -d "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  #{d[\"number\"]} {d[\"title\"]}')" ; }

create_issue '{"title":"[P0] T0: 项目环境初始化","body":"搭建 Python 3.10+ 虚拟环境，安装所有依赖，验证项目结构可运行。\n\n详见 docs/研发任务拆解.md","labels":["phase-0","P0","setup"]}'

create_issue '{"title":"[P0] T1: 数据库表结构创建与验证","body":"验证和完善 src/models/database.py 中定义的 6 张表，确保索引和约束正确。\n\n详见 docs/研发任务拆解.md","labels":["phase-1","P0","database"]}'

create_issue '{"title":"[P0] T2: AKShare 全量下载引擎","body":"完善 src/data/downloader.py，实现全市场 ~5500 只A股近 3 年日线数据一键下载。\n\n详见 docs/研发任务拆解.md","labels":["phase-1","P0","data"]}'

create_issue '{"title":"[P1] T3: 增量更新与数据完整性校验","body":"实现增量更新模式，仅下载缺失的最新交易日数据。下载后自动校验数据完整性。\n\n详见 docs/研发任务拆解.md","labels":["phase-1","P1","data"]}'

create_issue '{"title":"[P1] T4: WeStock Data 集成","body":"集成 WeStock Data (腾讯自选股 CLI) 作为补充数据源，用于实时行情和技术指标交叉校验。\n\n详见 docs/研发任务拆解.md","labels":["phase-1","P1","data"]}'

create_issue '{"title":"[P0] T5: 策略基类实现","body":"完善 BaseStrategy，确保 SMA/EMA/RSI/MACD/布林带/ATR 等技术指标计算正确。\n\n详见 docs/研发任务拆解.md","labels":["phase-2","P0","backtest"]}'

create_issue '{"title":"[P0] T6: 回测引擎封装","body":"完善 BacktestEngine，封装 Backtesting.py，输出标准化 BacktestReport。\n\n详见 docs/研发任务拆解.md","labels":["phase-2","P0","backtest"]}'

create_issue '{"title":"[P0] T7: 5个预设策略实现与验证","body":"实现并验证 5 个经典预设策略，每个策略标注学术来源。\n\n详见 docs/研发任务拆解.md","labels":["phase-2","P0","strategies"]}'

create_issue '{"title":"[P1] T8: 回测报告生成与持久化","body":"完善 report.py，实现报告→JSON→数据库持久化的完整链路。\n\n详见 docs/研发任务拆解.md","labels":["phase-2","P1","backtest"]}'

create_issue '{"title":"[P0] T9: FastAPI 基础框架搭建","body":"验证 FastAPI + Jinja2 + 静态文件可正常运行。\n\n详见 docs/研发任务拆解.md","labels":["phase-3","P0","web"]}'

create_issue '{"title":"[P0] T10: 首页仪表盘","body":"实现首页仪表盘，展示数据概览、最近回测、数据源声明。\n\n详见 docs/研发任务拆解.md","labels":["phase-3","P0","web"]}'

create_issue '{"title":"[P0] T11: 数据管理页","body":"实现数据管理页，支持触发下载、查看进度、数据源信息展示。\n\n详见 docs/研发任务拆解.md","labels":["phase-3","P0","web"]}'

create_issue '{"title":"[P0] T12: 回测执行与详情页","body":"实现回测执行页和回测详情页（含 ECharts 图表渲染）。\n\n详见 docs/研发任务拆解.md","labels":["phase-3","P0","web"]}'

create_issue '{"title":"[P1] T13: 多模型对比页","body":"实现多策略回测结果横向对比页面。\n\n详见 docs/研发任务拆解.md","labels":["phase-3","P1","web"]}'

create_issue '{"title":"[P1] T14: 策略注册与动态加载","body":"实现策略的 YAML 注册和 Python 动态加载机制。\n\n详见 docs/研发任务拆解.md","labels":["phase-4","P1","strategies"]}'

create_issue '{"title":"[P1] T15: 批量回测","body":"实现 BacktestEngine.run_batch() — 多策略 × 多股票批量回测。\n\n详见 docs/研发任务拆解.md","labels":["phase-4","P1","backtest"]}'

create_issue '{"title":"[P2] T16: 参数网格搜索","body":"实现 BacktestEngine.run_grid_search() — 自动遍历参数组合。\n\n详见 docs/研发任务拆解.md","labels":["phase-4","P2","backtest"]}'

create_issue '{"title":"[P0] T17: 端到端集成测试","body":"全流程端到端测试: 下载 → 存储 → 回测 → 展示。\n\n详见 docs/研发任务拆解.md","labels":["phase-5","P0","testing"]}'

create_issue '{"title":"[P1] T19: 文档与 README","body":"编写 README.md 和策略开发指南。\n\n详见 docs/研发任务拆解.md","labels":["phase-5","P1","docs"]}'

echo "✅ 19 issues created"
```
