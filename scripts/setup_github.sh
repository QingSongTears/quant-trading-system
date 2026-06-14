#!/bin/bash
# ================================================================
# A股量化交易模型系统 — 一键推送 + 创建 GitHub Issues
# 在项目根目录 (quant-trading-system/) 下运行此脚本
# ================================================================
set -e

REPO="QingSongTears/quant-trading-system"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 开始推送代码到 GitHub..."
cd "$PROJECT_DIR"

# 1. 初始化 Git（如未初始化）
if [ ! -d ".git" ]; then
    git init
    git config user.email "qingsong@quant.dev"
    git config user.name "QingSongTears"
fi

# 2. 添加所有文件并提交
git add -A
git commit -m "初始化: A股量化交易模型系统 v1.0

- 5个Phase/19项研发任务的完整项目框架
- 数据层: AKShare全量下载 + SQLite本地存储 + WeStock集成
- 回测引擎: Backtesting.py封装 + 5个预设策略(含学术来源)
- Web可视化: FastAPI + Jinja2 + ECharts 6页仪表盘
- 文档: 需求规划书 + PRD v1.1 + 研发任务拆解 + 评审材料
- 数据真实性: 所有来源可考证, AI内容标注规范" 2>/dev/null || echo "已经是最新提交"

# 3. 推送到 GitHub（使用用户本地凭证）
echo ""
echo "📤 推送到 GitHub..."
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${REPO}.git"
git branch -M main
git push -u origin main

echo ""
echo "✅ 代码推送完成！"
echo ""

# 4. 批量创建 19 个 Issues
echo "📋 创建 19 项研发任务 Issues..."

issues=(
  'P0|setup|phase-0|T0: 项目环境初始化|搭建 Python 3.10+ 虚拟环境，安装所有依赖，验证项目结构可运行。'
  'P0|database|phase-1|T1: 数据库表结构创建与验证|验证和完善 6 张 SQLAlchemy 表，确保索引和约束正确。'
  'P0|data|phase-1|T2: AKShare 全量下载引擎|实现全市场 ~5500 只A股近 3 年日线数据一键下载。含断点续传和进度回调。'
  'P1|data|phase-1|T3: 增量更新与数据完整性校验|仅下载缺失的最新交易日数据。下载后自动校验完整性。'
  'P1|data|phase-1|T4: WeStock Data 集成|集成腾讯自选股 CLI 作为补充数据源，用于行情查询和交叉校验。'
  'P0|backtest|phase-2|T5: 策略基类实现|完善 BaseStrategy，验证 SMA/EMA/RSI/MACD/布林带/ATR 等指标正确性。'
  'P0|backtest|phase-2|T6: 回测引擎封装|封装 Backtesting.py，输出标准化 BacktestReport。含交易成本和基准对比。'
  'P0|strategies|phase-2|T7: 5个预设策略实现与验证|双均线/MACD/RSI/布林带/海龟，每个标注学术来源。'
  'P1|backtest|phase-2|T8: 回测报告生成与持久化|报告→JSON→数据库持久化，支持 ECharts 前端渲染。'
  'P0|web|phase-3|T9: FastAPI 基础框架搭建|验证 FastAPI + Jinja2 + CDN 静态资源可正常运行。'
  'P0|web|phase-3|T10: 首页仪表盘|数据概览卡片、最近回测、数据源声明、AI 免责标注。'
  'P0|web|phase-3|T11: 数据管理页|下载控制(全量/增量)、进度条、下载历史、数据源详情表。'
  'P0|web|phase-3|T12: 回测执行与详情页|策略选择→回测→净值曲线/回撤曲线/交易明细 ECharts 渲染。'
  'P1|web|phase-3|T13: 多模型对比页|多策略净值叠加图、指标对比表、五维雷达图。'
  'P1|strategies|phase-4|T14: 策略注册与动态加载|YAML 注册 + importlib 动态导入 + Web 策略管理页。'
  'P1|backtest|phase-4|T15: 批量回测|多策略 × 多股票并行回测，完成后自动跳转对比页。'
  'P2|backtest|phase-4|T16: 参数网格搜索|笛卡尔积遍历参数组合，按指标排序展示最优参数。'
  'P0|testing|phase-5|T17: 端到端集成测试|全流程: 下载→存储→回测→展示，3 个测试场景。'
  'P1|docs|phase-5|T19: 文档与 README|README 快速开始、策略开发指南、AI 标注补充。'
)

count=0
for issue in "${issues[@]}"; do
  IFS='|' read -r priority label phase title body <<< "$issue"
  
  resp=$(curl -s -X POST \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Content-Type: application/json" \
    "https://api.github.com/repos/${REPO}/issues" \
    -d "{\"title\":\"[${priority}] ${title}\",\"body\":\"${body}\n\n📎 详见 docs/研发任务拆解.md\n📎 PRD: docs/PRD.md\",\"labels\":[\"${priority}\",\"${label}\",\"${phase}\"]}" 2>/dev/null)
  
  num=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('number','FAIL'))" 2>/dev/null)
  
  if [ "$num" != "FAIL" ] && [ -n "$num" ]; then
    echo "  ✅ #${num} [${priority}] ${title}"
    count=$((count+1))
  else
    err=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message','?'))" 2>/dev/null)
    echo "  ❌ ${title} — ${err}"
  fi
done

echo ""
echo "=========================================="
echo "✅ 完成！创建了 ${count}/19 个 Issues"
echo "🔗 https://github.com/${REPO}/issues"
echo "=========================================="
