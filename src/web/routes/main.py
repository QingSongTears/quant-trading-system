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
    """首页仪表盘"""
    repo = _get_repo()
    try:
        coverage = repo.get_data_coverage()
        recent = repo.get_recent_backtests(limit=5)
        all_backtests = repo.get_recent_backtests(limit=100)
        
        # 计算仪表盘聚合统计
        # 数据源: config/strategies.yaml (与 /api/strategies 一致)
        from ...config import load_strategies
        total_strategies = len(load_strategies().get("strategies", []))

        # 聚合回测统计
        best_return = None
        avg_sharpe = None
        if all_backtests:
            returns = [r.total_return for r in all_backtests if r.total_return is not None]
            sharpes = [r.sharpe_ratio for r in all_backtests if r.sharpe_ratio is not None]
            if returns:
                best_return = max(returns)
            if sharpes:
                avg_sharpe = round(sum(sharpes) / len(sharpes), 2)

        # 计算回测成功率 (正收益比例)
        win_count = sum(1 for r in all_backtests if r.total_return and r.total_return > 0)
        win_rate = round(win_count / len(all_backtests) * 100, 1) if all_backtests else None

        # 总回测数 (从数据库精确读, 用于卡片描述)
        try:
            backtest_total = repo.count_backtests()
        except Exception:
            backtest_total = len(all_backtests)
        
    except Exception:
        coverage = {"total_stocks": 0, "total_records": 0, "date_range": {"start": None, "end": None}}
        recent = []
        total_strategies = 0
        best_return = None
        avg_sharpe = None
        win_rate = None
        backtest_total = 0

    # 为首页散点图准备 JS 可直接消费的数据（避免前端从 DOM 爬取）
    recent_js = []
    for r in recent:
        recent_js.append({
            "strategy_name": r.strategy.name if r.strategy else "未知",
            "stock_code": r.stock_code,
            "stock_name": r.stock_name or "",
            "total_return": r.total_return,
            "sharpe_ratio": r.sharpe_ratio,
        })

    # 准备"一键对比"用的 top 回测 (用于首页 checkbox 选择)
    # 取每个不同 strategy 最新的一个回测, 最多 6 个
    top_strategy_cards = []
    seen_strategies = set()
    for r in all_backtests:
        strat_name = r.strategy.name if r.strategy else "未知"
        if strat_name in seen_strategies:
            continue
        seen_strategies.add(strat_name)
        top_strategy_cards.append({
            "id": r.id,
            "name": strat_name,
        })
        if len(top_strategy_cards) >= 6:
            break

    ctx = _get_global_context()
    ctx.update({
        "coverage": coverage,
        "recent_backtests": recent,
        "recent_backtests_js": recent_js,
        "total_strategies": total_strategies,
        "backtest_total": backtest_total,
        "best_return": best_return,
        "avg_sharpe": avg_sharpe,
        "win_rate": win_rate,
        "top_strategy_cards": top_strategy_cards,
        "has_data": coverage.get("total_records", 0) > 0,
        "data_sources": [
            {"name": "AKShare", "url": "https://akshare.readthedocs.io", "desc": "东方财富/新浪财经公开接口"},
            {"name": "WeStock Data", "url": "https://gu.qq.com", "desc": "腾讯自选股行情数据接口"},
            {"name": "Baostock", "url": "http://baostock.com", "desc": "免费证券数据（备选）"},
        ]
    })
    return templates.TemplateResponse(request, "index.html", ctx)


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
@router.get("/strategy-compare", response_class=HTMLResponse)
async def strategy_compare_redirect(request: Request):
    """重定向到 /compare?type=strategy"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/compare?type=strategy", status_code=301)


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


# 2026-06-25 (Phase C3a): backtest-lab / backtest-view 合并到 /workbench?mode=
# 保留旧路径做重定向, 避免硬链接坏
@router.get("/backtest-lab", response_class=HTMLResponse)
async def backtest_lab_redirect(request: Request):
    """重定向到 /workbench?mode=lab (兼容旧链接)"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/workbench?mode=lab", status_code=301)


@router.get("/backtest-view", response_class=HTMLResponse)
async def backtest_view_redirect(request: Request):
    """重定向到 /workbench?mode=view (兼容旧链接, 旧 API 死链已修)"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/workbench?mode=view", status_code=301)


# 2026-06-25 (Phase C3c): v5/v6/tuning/ic/dim 合并到 /research?type=
RESEARCH_REDIRECTS = {
    "/v5": "/research?type=v5",
    "/v6-compare": "/research?type=v6",
    "/tuning": "/research?type=tuning",
    "/ic": "/research?type=ic",
    "/dim-compare": "/research?type=dim",
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
    "/tuning": "tuning.html",
    "/data-monitor": "data-monitor.html",
    # "/v5": "v5.html",                          # 2026-06-25 合并到 /research?type=v5
    # "/v6-compare": "v6-compare.html",          # 2026-06-25 合并到 /research?type=v6
    # "/tuning": "tuning.html",                  # 2026-06-25 合并到 /research?type=tuning
    # "/dim-compare": "dim-compare.html",        # 2026-06-25 合并到 /research?type=dim
    # "/ic": "ic.html",                          # 2026-06-25 合并到 /research?type=ic
    "/walk_forward": "walk_forward.html",
}


def _register_standalone_routes():
    """批量注册独立页面路由 (避免闭包变量捕获问题)"""
    for path, tmpl in _STANDALONE_PAGES.items():
        async def _handler(request: Request, _t=tmpl):
            return templates.TemplateResponse(request, _t, _get_global_context())
        router.add_api_route(path, _handler, response_class=HTMLResponse, methods=["GET"])


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


_register_standalone_routes()
# 2026-06-25: 删 _register_placeholder_routes / _placeholder_html / _PLACEHOLDER_PAGES 死代码
# 死代码从 509-558 行, 原本就是空 list + 不调用, 全删. 减 50 行 + 减少加载时间
