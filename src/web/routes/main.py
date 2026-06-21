"""
页面路由 (Jinja2 模板渲染)
"""
import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ...config import get_config
from ...models.repository import DataRepository
from ..app import TEMPLATES_DIR, get_templates

router = APIRouter()
templates = get_templates()

# 注入全局配置到模板
def _get_global_context() -> dict:
    config = get_config()
    return {
        "app_name": config["web"]["title"],
        "ai_disclaimer": config["ai_disclaimer"],
        "cdn": config["web"]["cdn"],
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页仪表盘"""
    repo = DataRepository()
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
        
    except Exception:
        coverage = {"total_stocks": 0, "total_records": 0, "date_range": {"start": None, "end": None}}
        recent = []
        total_strategies = 0
        best_return = None
        avg_sharpe = None
        win_rate = None

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


@router.get("/data", response_class=HTMLResponse)
async def data_page(request: Request):
    """数据管理页"""
    repo = DataRepository()
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
    repo = DataRepository()
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
    repo = DataRepository()
    result = repo.get_backtest_result(result_id)
    if not result:
        return HTMLResponse("<h1>404 - 回测记录不存在</h1>", status_code=404)

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
    ids: Optional[str] = Query(None, description="逗号分隔的回测ID")
):
    """多模型对比页"""
    repo = DataRepository()
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
    })
    return templates.TemplateResponse(request, "compare.html", ctx)


@router.get("/workbench", response_class=HTMLResponse)
async def workbench_page(request: Request):
    """交互式回测工作台"""
    repo = DataRepository()
    # 策略来源: yaml（与 /api/strategies 一致）
    from ...config import load_strategies
    yaml_strategies = load_strategies().get("strategies", [])
    ctx = _get_global_context()
    ctx.update({
        "all_backtests": repo.get_recent_backtests(limit=30),
        "strategies": yaml_strategies,
        "default_start": (date.today() - timedelta(days=365)).strftime("%Y-%m-%d"),
        "default_end": date.today().strftime("%Y-%m-%d"),
    })
    return templates.TemplateResponse(request, "workbench.html", ctx)


# ============================================================
# 独立页面 (output/ 移植, 暗色主题, 不继承 base.html)
# ============================================================
_STANDALONE_PAGES = {
    "/diagnose": "diagnose.html",
    "/sector": "sector.html",
    "/screener": "screener.html",
    "/portfolio": "portfolio.html",
    "/backtest-lab": "backtest-lab.html",
    "/backtest-view": "backtest-view.html",
    "/strategy-compare": "strategy-compare.html",
    "/dashboard": "dashboard.html",
    "/data-monitor": "data-monitor.html",
    "/v5": "v5.html",
    "/v6-compare": "v6-compare.html",
    "/fund-flow-report": "fund-flow-report.html",
    "/dim-compare": "dim-compare.html",
    "/ic": "ic.html",
}


def _register_standalone_routes():
    """批量注册独立页面路由 (避免闭包变量捕获问题)"""
    for path, tmpl in _STANDALONE_PAGES.items():
        async def _handler(request: Request, _t=tmpl):
            return templates.TemplateResponse(request, _t, _get_global_context())
        router.add_api_route(path, _handler, response_class=HTMLResponse, methods=["GET"])


_register_standalone_routes()
