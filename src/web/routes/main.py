"""
页面路由 (Jinja2 模板渲染)
"""
from __future__ import annotations
import json
from datetime import date, timedelta


from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ...config import get_config
from ...models.repository import DataRepository
from ...data import get_data_manager
from ..app import TEMPLATES_DIR, get_templates
from pathlib import Path as _Path
PROJECT_ROOT = _Path(__file__).resolve().parents[3]
SPECS_DIR = PROJECT_ROOT / "output" / "ardot_specs"

router = APIRouter()
templates = get_templates()


def _get_repo():
    """统一从 data 层获取数据库访问入口。"""
    return get_data_manager().repository

# 注入全局配置到模板
def _get_global_context() -> dict:
    from ..auth import get_api_key
    config = get_config()
    return {
        "app_name": config["web"]["title"],
        "ai_disclaimer": config["ai_disclaimer"],
        "cdn": config["web"]["cdn"],
        "api_key": get_api_key(),
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页 = 数据总览 (Ardot 设计, 2026-06-26 决策)
    与 /dashboard 等价, 让用户打开根路径直接看到 dashboard 风格的总览页
    """
    return await dashboard_page(request)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """数据总览 — header 动态化 (硬编码 model/cover 部分保留, 见模板注释)"""
    repo = _get_repo()
    try:
        coverage = repo.get_data_coverage()
        total_records = coverage.get("total_records", 0)
        total_stocks = coverage.get("total_stocks", 0)
        date_range = coverage.get("date_range", {})
    except Exception:
        total_records = 0
        total_stocks = 0
        date_range = {"start": None, "end": None}

    ctx = _get_global_context()
    ctx.update({
        "total_records": total_records,
        "total_stocks": total_stocks,
        "date_range": date_range,
        "generated_at": date.today().strftime("%Y-%m-%d"),
    })
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/data", response_class=HTMLResponse)
async def data_page(request: Request):
    """数据管理页"""
    repo = _get_repo()
    try:
        coverage = repo.get_data_coverage()
        download_history = repo.get_download_history()
        stock_count = repo.get_stock_count()
        
        # 计算数据完整性
        total_records = coverage.get("total_records", 0)
        total_stocks = coverage.get("total_stocks", 0)
        avg_records_per_stock = round(total_records / total_stocks, 1) if total_stocks > 0 else 0
        
    except Exception:
        coverage = {}
        download_history = []
        stock_count = 0
        avg_records_per_stock = 0

    ctx = _get_global_context()
    ctx.update({
        "coverage": coverage,
        "download_history": download_history,
        "stock_count": stock_count,
        "avg_records_per_stock": avg_records_per_stock,
    })
    return templates.TemplateResponse(request, "data.html", ctx)


@router.get("/backtest", response_class=HTMLResponse)
async def backtest_page_list(request: Request):
    """回测记录列表页 (首页 /backtest 卡片跳转目标)"""
    repo = _get_repo()
    try:
        all_backtests = repo.get_recent_backtests(limit=50)
    except Exception:
        all_backtests = []

    ctx = _get_global_context()
    ctx.update({"all_backtests": all_backtests})
    return templates.TemplateResponse(request, "backtest_list.html", ctx)


@router.get("/backtest/{result_id}", response_class=HTMLResponse)
async def backtest_detail(request: Request, result_id: int):
    """回测详情页"""
    repo = _get_repo()
    result = repo.get_backtest_result(result_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"backtest result {result_id} not found")

    # 解析 JSON 字段
    equity_curve = json.loads(result.equity_curve) if result.equity_curve else []
    trades = json.loads(result.trades_detail) if result.trades_detail else []
    monthly = json.loads(result.monthly_returns) if result.monthly_returns else {}
    costs = json.loads(result.cost_config) if result.cost_config else {}

    # 计算回撤曲线
    drawdowns = []
    peak = 0
    for point in equity_curve:
        e = point["equity"]
        if e > peak:
            peak = e
        dd = (e - peak) / peak * 100 if peak > 0 else 0
        drawdowns.append({"date": point["date"], "drawdown": round(dd, 2)})

    # 交易统计
    trade_stats = {
        "total": len(trades),
        "wins": sum(1 for t in trades if (t.get("pnl") or 0) > 0),
        "losses": sum(1 for t in trades if (t.get("pnl") or 0) < 0),
        "win_rate": 0.0,
        "avg_pnl": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "profit_factor": 0.0,
        "max_profit": 0.0,
        "max_loss": 0.0,
        "avg_hold_days": 0.0,
    }
    if trades:
        pnls = [(t.get("pnl") or 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        trade_stats["win_rate"] = round(len(wins) / len(trades) * 100, 1)
        trade_stats["avg_pnl"] = round(sum(pnls) / len(pnls), 2)
        trade_stats["avg_win"] = round(sum(wins) / len(wins), 2) if wins else 0
        trade_stats["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else 0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        trade_stats["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        trade_stats["max_profit"] = round(max(pnls), 2) if pnls else 0
        trade_stats["max_loss"] = round(min(pnls), 2) if pnls else 0
        hold_days = [t.get("hold_days") or 0 for t in trades]
        trade_stats["avg_hold_days"] = round(sum(hold_days) / len(hold_days), 1) if hold_days else 0

    ctx = _get_global_context()
    ctx.update({
        "result": result,
        # 传给前端模板后由 |tojson 转 JS 数组 — 不要预先 json.dumps，否则会双重编码成字符串
        "equity_curve": equity_curve,
        "drawdown_curve": drawdowns,
        "trades": trades,
        "monthly_returns": monthly,
        "costs": costs,
        "trade_stats": trade_stats,
        "model_type": "portfolio" if result.stock_code == "PORTFOLIO"
                      else "voting" if result.stock_code == "VOTING"
                      else "signal",
    })
    return templates.TemplateResponse(request, "backtest_detail.html", ctx)


@router.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    """策略管理页 — 数据源: config/strategies.yaml (与 /api/strategies 一致)"""
    try:
        from ...config import load_strategies
        yaml_strategies = load_strategies().get("strategies", [])
        # yaml 里 params 已经是 dict，直接传
        strategies_data = [
            {
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "class_path": s.get("class_path", ""),
                "source": s.get("source", ""),
                "strategy_type": s.get("strategy_type", "signal"),
                "params": s.get("params", {}) or {},
            }
            for s in yaml_strategies
        ]
    except Exception:
        strategies_data = []

    ctx = _get_global_context()
    ctx.update({"strategies": strategies_data})
    return templates.TemplateResponse(request, "strategies.html", ctx)


@router.get("/compare", response_class=HTMLResponse)
async def compare_page(
    request: Request,
    ids: str | None = Query(None, description="逗号分隔的回测ID"),
    type: str = "backtest",  # backtest | strategy  (2026-06-25 合并 strategy-compare)
):
    """多模型对比页 (2026-06-25 合并 strategy-compare)

    Args:
        ids: 回测 ID 列表 (逗号分隔, backtest 模式)
        type: backtest (默认, 多次回测叠加) | strategy (按策略名聚合)
    """
    repo = _get_repo()
    results = []
    if ids:
        for id_str in ids.split(","):
            try:
                r = repo.get_backtest_result(int(id_str.strip()))
                if r:
                    results.append(r)
            except Exception:
                pass

    ctx = _get_global_context()
    ctx.update({
        "results": results,
        "all_backtests": repo.get_recent_backtests(limit=50),
        "compare_type": type,
    })
    return templates.TemplateResponse(request, "compare.html", ctx)


# 2026-06-25 (Phase C3b): strategy-compare 合并到 /compare?type=strategy
# 2026-06-25 (Phase D — Ardot 新设计稿): strategy-compare 恢复独立路由, 走新设计稿
# @router.get("/strategy-compare", response_class=HTMLResponse)
# async def strategy_compare_redirect(request: Request):
#     """重定向到 /compare?type=strategy (Phase D 前兼容)"""
#     from fastapi.responses import RedirectResponse
#     return RedirectResponse(url="/compare?type=strategy", status_code=301)


@router.get("/workbench", response_class=HTMLResponse)
async def workbench_page(request: Request, mode: str = "default"):
    """交互式回测工作台 — 2026-06-25 合并 backtest-lab / backtest-view

    Args:
        mode: default | lab | view
          - default: 标准回测 (原 workbench)
          - lab: 参数调节 (原 backtest-lab)
          - view: 可视化 (原 backtest-view)
    """
    repo = _get_repo()
    # 策略来源: yaml（与 /api/strategies 一致）
    from ...config import load_strategies
    yaml_strategies = load_strategies().get("strategies", [])
    if mode not in ("default", "lab", "view"):
        mode = "default"
    ctx = _get_global_context()
    ctx.update({
        "all_backtests": repo.get_recent_backtests(limit=30),
        "strategies": yaml_strategies,
        "default_start": (date.today() - timedelta(days=365)).strftime("%Y-%m-%d"),
        "default_end": date.today().strftime("%Y-%m-%d"),
        "page_mode": mode,  # 模板用此切 UI
    })
    return templates.TemplateResponse(request, "workbench.html", ctx)


# 2026-06-25 (Phase C3a): backtest-view 合并到 /workbench?mode=view
# 2026-06-25 (Phase D — Ardot 新设计稿): backtest-lab 不再合并, 独立用新设计稿
@router.get("/backtest-view", response_class=HTMLResponse)
async def backtest_view_redirect(request: Request):
    """重定向到 /workbench?mode=view (兼容旧链接, 旧 API 死链已修)"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/workbench?mode=view", status_code=301)


# 2026-06-25 (Phase C3c): research 合并页保留 (Phase D 后未启用, 但不删除以免旧链接坏)
# 注意: Phase D 中 /v5-tuning / /v6-compare / /ic-analysis / /dim-compare / /tuning-panel
#       已被新设计稿覆盖, _STANDALONE_PAGES 优先匹配这些路径.
#       RESEARCH_REDIRECTS 仅在 /v5 重定向到 /research?type=v5 (仍走老 research.html)
RESEARCH_REDIRECTS = {
    "/v5": "/research?type=v5",
}
for _path, _target in RESEARCH_REDIRECTS.items():
    @router.get(_path, response_class=HTMLResponse)
    async def _research_redirect(_t=_target):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=_t, status_code=301)


@router.get("/research", response_class=HTMLResponse)
async def research_page(request: Request, type: str = "v5"):
    """研究类页面 — 2026-06-25 合并 5 个 (v5/v6-compare/tuning/ic/dim-compare)

    Args:
        type: v5 | v6 | tuning | ic | dim
    """
    if type not in ("v5", "v6", "tuning", "ic", "dim"):
        type = "v5"
    ctx = _get_global_context()
    ctx.update({"research_type": type})
    return templates.TemplateResponse(request, "research.html", ctx)


@router.get("/simulate", response_class=HTMLResponse)
async def simulate_page(request: Request):
    """模拟交易工作台"""
    ctx = _get_global_context()
    from ...strategies.trading.config import TradingConfig
    ctx.update({
        "default_config": TradingConfig().to_dict(),
        "default_start": (date.today() - timedelta(days=365)).strftime("%Y-%m-%d"),
        "default_end": date.today().strftime("%Y-%m-%d"),
    })
    return templates.TemplateResponse(request, "simulate.html", ctx)


@router.get("/stock/{code}", response_class=HTMLResponse)
async def stock_detail_page(request: Request, code: str):
    """个股详情页 — leek-fund 风格 (大字当前价 + K线 + 分时 + 简况)"""
    ctx = _get_global_context()
    ctx["stock_code"] = code
    return templates.TemplateResponse(request, "stock_detail.html", ctx)


# ============================================================
# 独立页面 (output/ 移植, 暗色主题, 不继承 base.html)
# ============================================================
# 2026-06-25 (Phase D — Ardot 设计稿落地):
#  旧: 注册了 8 个独立页面 (diagnose/sector/screener/portfolio/data-monitor/fund-flow-report + 4 占位)
#  新: 加上 9 个 Ardot 设计稿独有的页面 (backtest_lab, signal_dashboard, strategy_compare,
#      tuning_panel, v5_tuning, v6_compare, ic_analysis, dim_compare, ...)
#  设计: 全部指向 src/web/templates/<name>.html (Jinja2 模板, 继承 _standalone_head.html)
_STANDALONE_PAGES = {
    "/diagnose": "diagnose.html",
    "/sector": "sector.html",
    "/screener": "screener.html",
    "/portfolio": "portfolio.html",
    # 2026-06-25 (Phase C3a): 合并到 /workbench?mode=lab / mode=view
    # "/backtest-lab": "backtest-lab.html",
    # "/backtest-view": "backtest-view.html",
    # "/strategy-compare": "strategy-compare.html",  # 2026-06-25 合并到 /compare?type=strategy
    "/bull-report": "bull-report.html",
    "/signal": "signal.html",
    "/verify": "verify.html",
    "/predict": "predict.html",
    # "/tuning": "tuning.html",  # 2026-06-26: 删 dead route, 改 /research?type=tuning (旧链接自动 redirect 兼容)
    "/data-monitor": "data-monitor.html",
    # "/v5": "v5.html",                          # 2026-06-25 合并到 /research?type=v5
    # "/v6-compare": "v6-compare.html",          # 2026-06-25 合并到 /research?type=v6
    # "/tuning": "tuning.html",                  # 2026-06-25 合并到 /research?type=tuning
    # "/dim-compare": "dim-compare.html",        # 2026-06-25 合并到 /research?type=dim
    # "/ic": "ic.html",                          # 2026-06-25 合并到 /research?type=ic
    "/walk-forward": "walk_forward.html",
    # 2026-06-25 (Phase D — Ardot 新设计稿)
    "/backtest-lab": "backtest_lab.html",       # 原 workbench?mode=lab 优先
    "/signal-dashboard": "signal_dashboard.html",  # 龙头模型 v2 综合信号
    "/strategy-compare": "strategy_compare.html",  # 7大策略全景
    "/tuning-panel": "tuning_panel.html",       # 七维量化评分参数调优
    "/v5-tuning": "v5_tuning.html",             # v5_hybrid 参数调优
    "/v6-compare": "v6_compare.html",           # v6阈值对比
    "/multi-objective": "multi_objective.html", # Ardot spec 03 多目标优化 (2026-06-26)
    "/ic-analysis": "ic_analysis.html",         # IC分析
    "/dim-compare": "dim_compare.html",         # 维度贡献
    # 2026-06-26: 补齐最后 3 个 Ardot 设计稿
    "/predict-verify": "predict_verify.html",   # 预测验证
    "/predict-dashboard": "predict_dashboard.html",  # 预测面板
    "/bull-backtest-report": "bull_backtest_report.html",  # 多头回测报告
}


def _register_standalone_routes():
    """批量注册独立页面路由 (避免闭包变量捕获问题)"""
    if getattr(_register_standalone_routes, "_done", False):
        return
    _register_standalone_routes._done = True
    for path, tmpl in _STANDALONE_PAGES.items():
        async def _handler(request: Request, _t=tmpl):
            return templates.TemplateResponse(request, _t, _get_global_context())
        router.add_api_route(path, _handler, response_class=HTMLResponse, methods=["GET"])


# 2026-06-26: /tuning 旧路由重定向到 /research?type=tuning
# 原 /tuning 模板已删除(2026-06-25 Phase C3 合并到 research),
# 但 dashboard / TopBar 旧链接仍指向 /tuning → 301 redirect 保持兼容
@router.get("/tuning", response_class=HTMLResponse)
async def tuning_redirect():
    """旧 /tuning 路由 → /research?type=tuning (兼容旧链接)"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/research?type=tuning", status_code=301)


# 2026-06-27: Phase C3 redirect 缺失补齐
# 旧路径仍登记在 _STANDALONE_PAGES 直接渲染, 但 Phase C3 期望 301
# 改法: 让独立页继续工作, 但额外提供 301 alias → 统一入口
_REDIRECT_ALIASES = [
    ("/backtest-lab", "/workbench?mode=lab"),
    ("/backtest-view", "/workbench?mode=view"),
    ("/strategy-compare", "/compare?type=strategy"),
    ("/v5", "/research?type=v5"),
    ("/v6-compare", "/research?type=v6"),
    ("/ic", "/research?type=ic"),
    ("/dim-compare", "/research?type=dim"),
]


def _register_redirect_aliases():
    """为 Phase C3 合并的旧路径提供 301 重定向 (兼容旧链接 + e2e 测试)

    实现策略: 把这些路径从 _STANDALONE_PAGES 移除, 改为 301 重定向;
    旧路径仍然可访问, 只是不再直接渲染内容, 而是跳转到新的统一入口.
    """
    from fastapi.responses import RedirectResponse

    # 幂等性: 多次调用不会重复注册 (FastAPI 不支持重名路由)
    if getattr(_register_redirect_aliases, "_done", False):
        return
    _register_redirect_aliases._done = True

    # 从 _STANDALONE_PAGES 移除这些路径, 避免路由冲突
    for old_path, _ in _REDIRECT_ALIASES:
        _STANDALONE_PAGES.pop(old_path, None)

    for old_path, new_path in _REDIRECT_ALIASES:
        async def _alias(_np=new_path):
            return RedirectResponse(url=_np, status_code=301)
        router.add_api_route(
            old_path, _alias, response_class=HTMLResponse, methods=["GET"],
            include_in_schema=False,
        )


# ============================================================
# Ardot 设计 Spec 浏览器 (2026-06-26 新增)
# 列出并渲染 output/ardot_specs/*.md, 用于在没有 Ardot MCP 时
# 也能查阅已规划的页面设计规格
# ============================================================
@router.get("/ardot-specs", response_class=HTMLResponse)
async def ardot_specs_index(request: Request):
    """列出所有 Ardot 设计 spec"""
    from pathlib import Path
    specs = []
    if SPECS_DIR.exists():
        for md in sorted(SPECS_DIR.glob("*.md")):
            title = md.stem
            if title == "README":
                continue
            # 从文件名推断标题: 01_v5_tuning_detailed → V5 调参详细
            num, name = title.split("_", 1)
            specs.append({
                "filename": md.name,
                "num": num,
                "slug": name,
                "title": name.replace("_", " ").title(),
                "size_kb": round(md.stat().st_size / 1024, 1),
            })
    ctx = _get_global_context()
    ctx.update({
        "specs": specs,
        "readme_exists": (SPECS_DIR / "README.md").exists(),
    })
    return templates.TemplateResponse(request, "ardot_specs_index.html", ctx)


@router.get("/ardot-specs/{slug}", response_class=HTMLResponse)
async def ardot_specs_view(request: Request, slug: str):
    """查看单个 spec 的 Markdown 渲染"""
    from pathlib import Path
    import re as _re
    from fastapi.responses import HTMLResponse as _HTML
    # 防止路径穿越
    if "/" in slug or ".." in slug:
        raise HTTPException(400, "invalid slug")
    # slug 形如 "v5_tuning_detailed"，文件名是 "01_v5_tuning_detailed.md"
    # 先尝试直接匹配，再尝试加数字前缀
    target = SPECS_DIR / f"{slug}.md"
    if not target.exists():
        for prefix in ("01_", "02_", "03_", "04_", "05_", "06_", "07_", "08_", "09_", "10_"):
            candidate = SPECS_DIR / f"{prefix}{slug}.md"
            if candidate.exists():
                target = candidate
                break
    if not target.exists():
        raise HTTPException(404, f"spec not found: {slug}")
    raw = target.read_text(encoding="utf-8")
    # 极简 Markdown → HTML 渲染 (只处理标题/列表/表格/代码块)
    def render_md(md: str) -> str:
        out = []
        in_code = False
        in_table = False
        for line in md.splitlines():
            if line.startswith("```"):
                if in_code:
                    out.append("</pre>")
                    in_code = False
                else:
                    lang = line[3:].strip() or ""
                    out.append(f'<pre class="md-code" data-lang="{lang}">')
                    in_code = True
                continue
            if in_code:
                out.append(_re.sub(r'<', '&lt;', line))
                continue
            # 表格
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not in_table:
                    out.append('<table class="md-table">')
                    out.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr></thead><tbody>")
                    in_table = True
                else:
                    if all(set(c) <= set("-: ") for c in cells):
                        continue
                    out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
                continue
            elif in_table and not line.startswith("|"):
                out.append("</tbody></table>")
                in_table = False
            # 标题
            m = _re.match(r'^(#{1,6})\s+(.*)$', line)
            if m:
                lvl = len(m.group(1))
                out.append(f"<h{lvl}>{m.group(2)}</h{lvl}>")
                continue
            # 列表
            if line.startswith("- ") or line.startswith("* "):
                out.append(f"<li>{line[2:]}</li>")
                continue
            if line.startswith("✅") or line.startswith("⚠️") or line.startswith("⏳"):
                out.append(f'<div class="md-callout">{line}</div>')
                continue
            if line.strip():
                out.append(f"<p>{line}</p>")
        if in_table:
            out.append("</tbody></table>")
        return "\n".join(out)
    body = render_md(raw)
    return _HTML(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{slug} · Ardot Spec</title>
<style>
body{{background:#0F172A;color:#E2E8F0;font-family:'Fira Sans',system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:32px;line-height:1.6;}}
h1{{color:#F59E0B;border-bottom:2px solid #334155;padding-bottom:12px;font-size:32px;}}
h2{{color:#8B5CF6;font-size:24px;margin-top:32px;}}
h3,h4,h5,h6{{color:#3B82F6;font-size:18px;margin-top:24px;}}
.md-table{{width:100%;border-collapse:collapse;margin:16px 0;font-family:'Fira Code',monospace;font-size:13px;}}
.md-table th,.md-table td{{border:1px solid #334155;padding:6px 10px;text-align:left;}}
.md-table th{{background:#1E293B;color:#F1F5F9;}}
.md-table tr:nth-child(even){{background:#1E293B;}}
.md-code{{background:#0B1120;color:#22C55E;padding:12px;border-radius:6px;overflow-x:auto;font-family:'Fira Code',monospace;font-size:13px;}}
.md-callout{{background:#1E293B;border-left:4px solid #F59E0B;padding:12px;margin:12px 0;border-radius:4px;}}
p{{margin:8px 0;}}
li{{margin:4px 0;}}
a{{color:#3B82F6;}}
.back{{display:inline-block;margin-bottom:24px;color:#3B82F6;text-decoration:none;}}
.back:hover{{text-decoration:underline;}}
</style></head><body>
<a class="back" href="/ardot-specs">← 返回 spec 列表</a>
{body}
</body></html>"""
    )


@router.get("/console", response_class=HTMLResponse)
async def console_page(request: Request):
    """AI QuantX 风格统一控制台 — 7 tabs (总览/回测/对比/持仓/行情/风控/日志) A股涨红跌绿"""
    return templates.TemplateResponse(request, "console.html", _get_global_context())


@router.get("/fund-flow-report", response_class=HTMLResponse)
async def fund_flow_report_page(request: Request):
    """资金面融合回测 — 实时从 database/fund_flow_report_data.json 读"""
    from pathlib import Path
    import json

    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    data_path = PROJECT_ROOT / "database" / "fund_flow_report_data.json"

    ctx = _get_global_context()
    ctx["data_available"] = False
    ctx["data"] = {"scenarios": [], "metrics": {}, "period": {}}
    ctx["baseline"] = {}
    ctx["best"] = {}
    ctx["best_name"] = "—"
    ctx["error"] = None

    if not data_path.exists():
        ctx["error"] = f"数据文件不存在: {data_path.name}"
    else:
        try:
            with open(data_path, encoding="utf-8") as f:
                payload = json.load(f)
            ctx["data_available"] = True
            ctx["data"] = payload
            ctx["baseline"] = next(
                (s for s in payload["scenarios"] if s["id"] == "v6_only"),
                payload["scenarios"][0] if payload["scenarios"] else {},
            )
            ctx["best"] = next(
                (s for s in payload["scenarios"] if s["id"] == payload.get("best_id")),
                payload["scenarios"][0] if payload["scenarios"] else {},
            )
            ctx["best_name"] = ctx["best"].get("name", "—")
        except Exception as e:
            ctx["error"] = f"数据解析失败: {e}"

    return templates.TemplateResponse(request, "fund-flow-report.html", ctx)


# 2026-06-27: Phase C3 redirect 必须比 standalone 优先注册, 否则 FastAPI 按顺序匹配
# 策略: 先去掉 _STANDALONE_PAGES 里的旧路径, 再注册 301, 最后再注册剩余的 standalone
_register_redirect_aliases()  # 这会从 _STANDALONE_PAGES 移除旧路径并注册 301
_register_standalone_routes()  # 注册剩余的 standalone 页面
# 2026-06-25: 删 _register_placeholder_routes / _placeholder_html / _PLACEHOLDER_PAGES 死代码
# 死代码从 509-558 行, 原本就是空 list + 不调用, 全删. 减 50 行 + 减少加载时间
