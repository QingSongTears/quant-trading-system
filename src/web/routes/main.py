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
from .app import TEMPLATES_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 注入全局配置到模板
def _get_global_context(request: Request) -> dict:
    config = get_config()
    return {
        "request": request,
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
    except Exception:
        coverage = {"total_stocks": 0, "total_records": 0, "date_range": {"start": None, "end": None}}
        recent = []

    ctx = _get_global_context(request)
    ctx.update({
        "coverage": coverage,
        "recent_backtests": recent,
        "data_sources": [
            {"name": "AKShare", "url": "https://akshare.readthedocs.io", "desc": "东方财富/新浪财经公开接口"},
            {"name": "WeStock Data", "url": "https://gu.qq.com", "desc": "腾讯自选股行情数据接口"},
            {"name": "Baostock", "url": "http://baostock.com", "desc": "免费证券数据（备选）"},
        ]
    })
    return templates.TemplateResponse("index.html", ctx)


@router.get("/data", response_class=HTMLResponse)
async def data_page(request: Request):
    """数据管理页"""
    repo = DataRepository()
    try:
        coverage = repo.get_data_coverage()
        download_history = repo.get_download_history()
    except Exception:
        coverage = {}
        download_history = []

    ctx = _get_global_context(request)
    ctx.update({
        "coverage": coverage,
        "download_history": download_history,
    })
    return templates.TemplateResponse("data.html", ctx)


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

    ctx = _get_global_context(request)
    ctx.update({
        "strategies": strategies,
        "stock_list": stock_list.to_dict("records") if hasattr(stock_list, "to_dict") else [],
        "default_start": (date.today() - timedelta(days=365 * 3)).strftime("%Y-%m-%d"),
        "default_end": date.today().strftime("%Y-%m-%d"),
    })
    return templates.TemplateResponse("backtest.html", ctx)


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

    ctx = _get_global_context(request)
    ctx.update({
        "result": result,
        "equity_curve": json.dumps(equity_curve),
        "drawdown_curve": json.dumps(drawdowns),
        "trades": trades,
        "monthly_returns": json.dumps(monthly),
        "costs": costs,
    })
    return templates.TemplateResponse("backtest_detail.html", ctx)


@router.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    """策略管理页"""
    repo = DataRepository()
    try:
        strategies = repo.get_all_strategies()
    except Exception:
        strategies = []

    ctx = _get_global_context(request)
    ctx.update({"strategies": strategies})
    return templates.TemplateResponse("strategies.html", ctx)


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

    ctx = _get_global_context(request)
    ctx.update({
        "results": results,
        "all_backtests": repo.get_recent_backtests(limit=50),
    })
    return templates.TemplateResponse("compare.html", ctx)
