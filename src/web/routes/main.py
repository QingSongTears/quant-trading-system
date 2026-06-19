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

    ctx = _get_global_context()
    ctx.update({
        "coverage": coverage,
        "recent_backtests": recent,
        "recent_backtests_js": recent_js,
        "total_strategies": total_strategies,
        "best_return": best_return,
        "avg_sharpe": avg_sharpe,
        "win_rate": win_rate,
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
async def backtest_page(request: Request):
    """回测执行页"""
    repo = DataRepository()
    try:
        strategies = repo.get_all_strategies()
        stock_list = repo.get_stock_list()
    except Exception:
        strategies = []
        stock_list = []

    # 加载 strategies.yaml 获取 strategy_type 信息
    from ...config import load_strategies
    yaml_strategies = load_strategies().get("strategies", [])
    yaml_map = {s["name"]: s for s in yaml_strategies}

    ctx = _get_global_context()
    ctx.update({
        "strategies": strategies,
        "strategy_configs": yaml_map,  # 包含 strategy_type 等字段
        "stock_list": stock_list.to_dict("records") if hasattr(stock_list, "to_dict") else [],
        "default_start": (date.today() - timedelta(days=365 * 3)).strftime("%Y-%m-%d"),
        "default_end": date.today().strftime("%Y-%m-%d"),
    })
    return templates.TemplateResponse(request, "backtest.html", ctx)


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

    ctx = _get_global_context()
    ctx.update({
        "result": result,
        "equity_curve": json.dumps(equity_curve),
        "drawdown_curve": json.dumps(drawdowns),
        "trades": trades,
        "monthly_returns": json.dumps(monthly),
        "costs": costs,
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
