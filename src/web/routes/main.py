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
        all_backtests = repo.get_recent_backtests(limit=100)
        
        # 计算仪表盘聚合统计
        total_strategies = len(repo.get_all_strategies())
        
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

    ctx = _get_global_context(request)
    ctx.update({
        "coverage": coverage,
        "recent_backtests": recent,
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
    return templates.TemplateResponse("index.html", ctx)


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

    ctx = _get_global_context(request)
    ctx.update({
        "coverage": coverage,
        "download_history": download_history,
        "stock_count": stock_count,
        "avg_records_per_stock": avg_records_per_stock,
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

    # 加载 strategies.yaml 获取 strategy_type 信息
    from ...config import load_strategies
    yaml_strategies = load_strategies().get("strategies", [])
    yaml_map = {s["name"]: s for s in yaml_strategies}

    ctx = _get_global_context(request)
    ctx.update({
        "strategies": strategies,
        "strategy_configs": yaml_map,  # 包含 strategy_type 等字段
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
        # 预处理策略，解析 JSON 参数字段供模板使用
        strategies_data = []
        for s in strategies:
            params = {}
            if s.params:
                try:
                    params = json.loads(s.params) if isinstance(s.params, str) else s.params
                except (json.JSONDecodeError, TypeError):
                    params = {}
            strategies_data.append({
                "name": s.name,
                "description": s.description,
                "class_path": s.class_path,
                "source": s.source,
                "params": params,
            })
    except Exception:
        strategies_data = []

    ctx = _get_global_context(request)
    ctx.update({"strategies": strategies_data})
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
