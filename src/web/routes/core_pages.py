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
from ...data import get_data_manager
from ..app import TEMPLATES_DIR, get_templates
from pathlib import Path as _Path
PROJECT_ROOT = _Path(__file__).resolve().parents[3]
SPECS_DIR = PROJECT_ROOT / "output" / "ardot_specs"

router = APIRouter()
templates = get_templates()


def _get_repo():
    """统一从 data 层获取业务宽表访问入口 (ADR-0010 §D3)

    通过 DataManager.business 门面访问,避免 web 直接 import DataRepository
    (check_legacy.py 黑名单:strategies/scoring/selection/web 禁 import DataRepository)。
    """
    return get_data_manager().business

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
