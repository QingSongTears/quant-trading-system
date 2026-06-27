"""
API 路由 (JSON 响应)
===================

所有 /api/* 端点需要 Bearer token 认证 (HTTPBearer)
策略 class_path 走白名单加载 (auth.safe_import_strategy)
"""
from __future__ import annotations
import json
import logging
import threading
import traceback
from datetime import date, datetime


from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ...models.repository import DataRepository
from ...data import get_data_manager
from ...data.downloader import DataDownloader
# 2026-06-27: westock_downloader 桩模块已删,统一用 DataDownloader
from ...backtest.engine import BacktestEngine
from ...backtest.portfolio_engine import PortfolioBacktestEngine
from ..app import download_status as _download_status
from ..auth import require_permission  # 2026-06-25 (Phase P4): RBAC
from ..app import update_download_status, get_download_status as _get_status
from ..auth import safe_import_strategy, verify_api_key

# 2026-06-26: predict 4 端点依赖
import math  # sigmoid
from pathlib import Path as _Path
from sqlalchemy.exc import OperationalError

# 所有 /api/* 端点统一要求 Bearer token
router = APIRouter(dependencies=[Depends(verify_api_key)])


def _get_repo():
    """统一从 data 层获取数据库访问入口。"""
    return get_data_manager().repository


# ===== 序列化辅助函数 (处理 np.nan / np.float64) =====

def _safe_float(v):
    """numpy float / NaN / None → 原生 Python"""
    if v is None:
        return None
    try:
        import math
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _safe_json(v):
    """numpy 类型 + NaN 安全序列化为 JSON 字符串"""
    if v is None:
        return "[]"
    if isinstance(v, str):
        return v  # 已经是 JSON 字符串
    try:
        import numpy as np
        # 转 list 时把 numpy 类型替换成原生类型
        def _conv(o):
            if isinstance(o, dict):
                return {k: _conv(val) for k, val in o.items()}
            if isinstance(o, (list, tuple)):
                return [_conv(x) for x in o]
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                f = float(o)
                if math.isnan(f) or math.isinf(f):
                    return None
                return f
            if isinstance(o, float):
                if math.isnan(o) or math.isinf(o):
                    return None
                return o
            return o
        import math
        cleaned = _conv(v)
        return json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
    except Exception:
        return "[]"


# ===== 请求模型 =====

class BacktestRequest(BaseModel):
    strategy_name: str
    stock_code: str
    start_date: str          # YYYY-MM-DD
    end_date: str
    initial_capital: float = 100000
    commission: float | None = None
    stamp_duty: float | None = None
    slippage: float | None = None


class BatchBacktestRequest(BaseModel):
    strategy_names: list[str]
    stock_codes: list[str]
    start_date: str
    end_date: str
    initial_capital: float = 100000


class PortfolioBacktestRequest(BaseModel):
    """组合回测请求 — 选股策略专用 (不需要 stock_code)"""
    strategy_name: str
    start_date: str
    end_date: str
    initial_capital: float = 1000000


class VotingBacktestRequest(BaseModel):
    """投票模型回测请求 — 技术投票模型专用"""
    start_date: str
    end_date: str
    initial_capital: float = 1000000
    max_stocks: int = 50  # 最多扫描50只 (性能考虑)


# ===== 数据 API =====

@router.get("/data/coverage")
async def get_data_coverage():
    """获取数据覆盖概览"""
    repo = _get_repo()
    try:
        return {"success": True, "data": repo.get_data_coverage()}
    except Exception as e:
        logger.error("data/coverage 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": "数据服务异常"}


@router.get("/data/search")
async def search_stocks(q: str = Query(..., min_length=1)):
    """搜索股票"""
    repo = _get_repo()
    try:
        df = repo.get_stock_list()
        # 兼容 NaN: name 可能含 NaN
        df["name"] = df["name"].fillna("")
        df["industry"] = df["industry"].fillna("")
        mask = df["name"].str.contains(q, na=False) | df["code"].str.contains(q, na=False)
        results = df[mask].head(20).to_dict("records")
        # 处理 NaT/NaN → None/空串
        for r in results:
            for k, v in list(r.items()):
                if v is None:
                    continue
                # pandas NaT
                if hasattr(v, '__class__') and v.__class__.__name__ == 'NaTType':
                    r[k] = None
                # float NaN
                elif isinstance(v, float) and v != v:
                    r[k] = None
        return {"success": True, "data": results}
    except Exception as e:
        logger.error("data/search 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": "搜索服务异常"}


# ===== 数据接口统一: 兼容旧版路径 (screener.html 用 /api/stock/* 而非 /api/data/*) =====

@router.get("/stock/search")
async def stock_search_alias(q: str = Query(..., min_length=1)):
    """兼容别名: /api/stock/search → /api/data/search (数据接口统一)
    旧版 screener.html 与 param_server.py 用的是 /api/stock/search,新路径 /api/data/search"""
    return await search_stocks(q)


@router.get("/sector")
async def get_sector_distribution(
    sort_by: str = Query("stock_count"),
    min_stocks: int = Query(1, ge=1),
):
    """行业板块分布 — 兼容 param_server.py /api/sector

    数据接口统一: 基于 stock_basic.industry 字段聚合,前端可直接使用
    """
    repo = _get_repo()
    try:
        df = repo.get_stock_list()
        if df.empty or "industry" not in df.columns:
            return {"success": True, "data": []}

        # 按行业聚合
        industry_counts = (
            df["industry"]
            .fillna("其他")
            .value_counts()
            .reset_index()
        )
        industry_counts.columns = ["industry", "stock_count"]

        # 按请求排序
        if sort_by == "name":
            industry_counts = industry_counts.sort_values("industry")
        else:  # 默认按 stock_count 降序
            industry_counts = industry_counts.sort_values("stock_count", ascending=False)

        # 过滤最小股票数
        industry_counts = industry_counts[industry_counts["stock_count"] >= min_stocks]

        return {"success": True, "data": industry_counts.to_dict("records")}
    except Exception as e:
        logger.error("sector 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": "行业数据服务异常"}


@router.get("/stock/screener")
async def stock_screener(
    industry: str | None = Query(None),
    exclude_st: bool = Query(True),
    max_stocks: int = Query(100, ge=1, le=500),
):
    """股票筛选 — 兼容 param_server.py /api/stock/screener

    数据接口统一: 行业筛选 + 排除 ST/退市,返回基础数据
    注: 完整评分筛选需要 scoring 管线,这里只做基础筛选
    """
    repo = _get_repo()
    try:
        df = repo.get_stock_list()
        if df.empty:
            return {"success": True, "data": []}

        if exclude_st and "name" in df.columns:
            df = df[~df["name"].str.contains("ST|退市", na=False)]

        if industry and industry != "全部" and "industry" in df.columns:
            df = df[df["industry"] == industry]

        df = df.head(max_stocks)

        # 安全序列化
        results = []
        for _, row in df.iterrows():
            results.append({
                "code": str(row.get("code", "")),
                "name": " ".join(str(row.get("name", "")).split()),  # 清洗双空格
                "industry": str(row.get("industry", "") or ""),
                "market": str(row.get("market", "") or ""),
                "list_date": str(row.get("list_date", "") or ""),
            })

        return {"success": True, "total": len(results), "data": results}
    except Exception as e:
        logger.error("stock/screener 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": "筛选服务异常"}


# ===== 下载 API =====

@router.post("/data/download", dependencies=[Depends(require_permission("download_data"))])
async def trigger_download(mode: str = "incremental"):
    """
    触发数据下载
    mode: "full" (全量) 或 "incremental" (增量)
    
    🔵 下载状态和进度通过 /api/data/download/status 查询
    """
    from datetime import datetime as dt

    # 检查是否有正在运行的下载
    current_status = get_download_status()
    if current_status["running"]:
        return {
            "success": False,
            "error": "下载任务正在运行中，请等待完成",
            "current": current_status["current"],
            "progress": current_status["progress"],
            "total": current_status["total"],
        }

    # 重置全局状态
    update_download_status({
        "running": True, "mode": mode, "progress": 0,
        "total": 0, "current": "准备中...", "error": None,
        "result": None, "started_at": dt.now().isoformat(),
    })

    def _run():
        try:
            # 2026-06-27: westock 桩模块已删,统一用 DataDownloader (AKShare)
            downloader = DataDownloader()
            source_name = "AKShare"
            update_download_status({"current": f"使用 {source_name} 下载中..."})

            if mode == "full":
                result = downloader.download_full(
                    progress_callback=lambda c, t, code, name: update_download_status(
                        {"progress": c, "total": t, "current": f"{code} {name}"}
                    )
                )
            else:
                result = downloader.download_incremental(
                    progress_callback=lambda c, t, code, name: update_download_status(
                        {"progress": c, "total": t, "current": f"{code} {name}"}
                    )
                )
            result["source"] = result.get("source", source_name)
            update_download_status({"running": False, "result": result})
        except Exception as e:
            logger.error("data/download 失败: %s\n%s", e, traceback.format_exc())
            update_download_status({"running": False, "error": "下载失败，详查日志"})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"success": True, "message": "下载已启动", "mode": mode}


@router.get("/data/download/status")
async def get_download_status():
    """查询下载进度（使用线程安全的状态管理器）"""
    status = _get_status()
    return {
        "success": True,
        **status,
    }


# ===== 回测 API =====

@router.post("/backtest/run", dependencies=[Depends(require_permission("run_backtest"))])
async def run_backtest(req: BacktestRequest):
    """执行单次回测"""
    try:
        # 动态加载策略
        from ...config import load_strategies
        strategies_config = load_strategies()
        strategy_class = None

        for s in strategies_config.get("strategies", []):
            if s["name"] == req.strategy_name:
                # 投票/组合/stock_screener 类策略需要专用 endpoint
                s_type = s.get("strategy_type")
                if s_type in ("voting", "portfolio"):
                    raise ValueError(
                        f"策略 {req.strategy_name!r} 是 {s_type} 类型, "
                        f"请使用 /api/backtest/{s_type}/run 端点"
                    )
                # P1-4 (2026-06-26): stock_screener 子系统已删, 不再识别该类型
                if s.get("engine") == "stock_screener" or s_type == "stock_screener":
                    raise ValueError(
                        f"策略 {req.strategy_name!r} 配置为 stock_screener 私有引擎, "
                        f"但该子系统已于 2026-06-25 删除 (commit 8d871b9). "
                        f"请改用主引擎 portfolio 类型 (见 strategies.yaml 该条目注释)."
                    )
                strategy_class = safe_import_strategy(s["class_path"])
                break

        if strategy_class is None:
            raise ValueError(f"未找到策略: {req.strategy_name}")

        engine = BacktestEngine()
        report = engine.run(
            strategy_class=strategy_class,
            stock_code=req.stock_code,
            start_date=date.fromisoformat(req.start_date),
            end_date=date.fromisoformat(req.end_date),
            initial_capital=req.initial_capital,
            commission=req.commission,
            stamp_duty=req.stamp_duty,
            slippage=req.slippage,
        )

        # 持久化 (PR3.3: 简化 session 生命周期,用 repo 封装方法替代直 ORM query)
        repo = _get_repo()
        with repo.get_session() as session:
            # 先保存策略配置
            for s in strategies_config.get("strategies", []):
                if s["name"] == req.strategy_name:
                    repo.save_strategy_config(
                        session, s["name"], s["class_path"],
                        json.dumps(s.get("params", {})),
                        s.get("description", ""),
                        s.get("source", "")
                    )
                    break

            # 查找策略ID (PR3.3: 用 repo 封装,不再直 query)
            strategy_record = repo.get_strategy_by_name(req.strategy_name)
            strategy_id = strategy_record.id if strategy_record else None

            result_dict = report.to_db_dict(strategy_id)
            result_dict["stock_name"] = report.stock_name
            result_id = repo.save_backtest_result(session, result_dict)
        # session 自动 commit + close

        return {
            "success": True,
            "result_id": result_id,
            "report": {
                "stock_code": report.stock_code,
                "stock_name": report.stock_name,
                "start_date": str(report.start_date),
                "end_date": str(report.end_date),
                "initial_capital": report.initial_capital,
                "final_equity": report.final_equity,
                "total_return": _safe_float(report.total_return),
                "annual_return": _safe_float(report.annual_return),
                "sharpe_ratio": _safe_float(report.sharpe_ratio),
                "max_drawdown": _safe_float(report.max_drawdown),
                "win_rate": _safe_float(report.win_rate),
                "total_trades": report.total_trades,
                "annual_volatility": _safe_float(report.annual_volatility),
                "calmar_ratio": _safe_float(report.calmar_ratio),
                "profit_factor": _safe_float(report.profit_factor),
                "benchmark_return": _safe_float(report.benchmark_return),
                "excess_return": _safe_float(report.excess_return),
                "equity_curve": _safe_json(report.equity_curve),
                "trades_detail": _safe_json(report.trades_detail),
                "monthly_returns": _safe_json(report.monthly_returns),
            }
        }

    except Exception as e:
        logger.error("backtest/run 失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="回测服务异常")


# ===== 组合回测 API (选股策略) =====

@router.post("/backtest/portfolio/run", dependencies=[Depends(require_permission("run_backtest"))])
async def run_portfolio_backtest(req: PortfolioBacktestRequest):
    """执行组合回测 (选股策略)"""
    try:
        from ...config import load_strategies

        strategies_config = load_strategies()
        strategy_class = None
        strategy_meta = None

        for s in strategies_config.get("strategies", []):
            if s["name"] == req.strategy_name and s.get("strategy_type") == "portfolio":
                strategy_class = safe_import_strategy(s["class_path"])
                strategy_meta = s
                break

        if strategy_class is None:
            raise ValueError(f"未找到选股策略: {req.strategy_name}")

        # 用 YAML 参数实例化策略
        strategy_params = strategy_meta.get("params", {})
        strategy = strategy_class()
        for k, v in strategy_params.items():
            if hasattr(strategy, k):
                setattr(strategy, k, v)

        engine = PortfolioBacktestEngine()
        report = engine.run(
            strategy=strategy,
            start_date=date.fromisoformat(req.start_date),
            end_date=date.fromisoformat(req.end_date),
            initial_capital=req.initial_capital,
        )

        # 持久化 (PR3.3)
        repo = _get_repo()
        with repo.get_session() as session:
            for s in strategies_config.get("strategies", []):
                if s["name"] == req.strategy_name:
                    repo.save_strategy_config(
                        session, s["name"], s["class_path"],
                        json.dumps(s.get("params", {})),
                        s.get("description", ""),
                        s.get("source", "")
                    )
                    break

            strategy_record = repo.get_strategy_by_name(req.strategy_name)
            strategy_id = strategy_record.id if strategy_record else None

            result_dict = report.to_db_dict(strategy_id)
            result_dict["stock_name"] = report.stock_name
            result_id = repo.save_backtest_result(session, result_dict)
        # session 自动 commit + close

        return {
            "success": True,
            "result_id": result_id,
            "report": {
                "stock_code": report.stock_code,
                "stock_name": report.stock_name,
                "start_date": str(report.start_date),
                "end_date": str(report.end_date),
                "initial_capital": report.initial_capital,
                "final_equity": report.final_equity,
                "total_return": _safe_float(report.total_return),
                "annual_return": _safe_float(report.annual_return),
                "sharpe_ratio": _safe_float(report.sharpe_ratio),
                "max_drawdown": _safe_float(report.max_drawdown),
                "win_rate": _safe_float(report.win_rate),
                "total_trades": report.total_trades,
                "annual_volatility": _safe_float(report.annual_volatility),
                "calmar_ratio": _safe_float(report.calmar_ratio),
                "profit_factor": _safe_float(report.profit_factor),
                "benchmark_return": _safe_float(report.benchmark_return),
                "excess_return": _safe_float(report.excess_return),
                "equity_curve": _safe_json(report.equity_curve),
                "trades_detail": _safe_json(report.trades_detail),
                "monthly_returns": _safe_json(report.monthly_returns),
            }
        }

    except Exception as e:
        logger.error("backtest/portfolio/run 失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="回测服务异常")


# ===== 投票模型回测 API (技术投票模型) =====

@router.post("/backtest/voting/run", dependencies=[Depends(require_permission("run_backtest"))])
async def run_voting_backtest(req: VotingBacktestRequest):
    """执行技术投票模型回测"""
    try:
        from ...models.technical_voting import TechnicalVotingModel
        repo = _get_repo()

        # 获取股票池: 从 daily_price 中取有足够数据的股票
        stock_list = repo.get_stock_list()
        if hasattr(stock_list, 'code'):
            all_codes = stock_list["code"].tolist() if hasattr(stock_list, "tolist") else list(stock_list.code)
        else:
            all_codes = stock_list["code"].tolist()

        # 限制数量 (性能考虑)
        stock_pool = all_codes[:req.max_stocks]

        model = TechnicalVotingModel()
        report = model.run(
            stock_pool=stock_pool,
            start_date=date.fromisoformat(req.start_date),
            end_date=date.fromisoformat(req.end_date),
            initial_capital=req.initial_capital,
        )

        # 持久化 (PR3.3)
        with repo.get_session() as session:
            repo.save_strategy_config(
                session, "技术指标投票模型",
                "src.models.technical_voting.TechnicalVotingModel",
                "{}",
                "5策略投票委员会: 双均线+MACD+RSI+布林带+海龟",
                "综合投票模型"
            )

            strategy_record = repo.get_strategy_by_name("技术指标投票模型")
            strategy_id = strategy_record.id if strategy_record else None

            result_dict = report.to_db_dict(strategy_id)
            result_dict["stock_name"] = report.stock_name
            result_id = repo.save_backtest_result(session, result_dict)
        # session 自动 commit + close

        return {
            "success": True,
            "result_id": result_id,
            "report": {
                "stock_code": report.stock_code,
                "stock_name": report.stock_name,
                "start_date": str(report.start_date),
                "end_date": str(report.end_date),
                "initial_capital": report.initial_capital,
                "final_equity": report.final_equity,
                "total_return": _safe_float(report.total_return),
                "annual_return": _safe_float(report.annual_return),
                "sharpe_ratio": _safe_float(report.sharpe_ratio),
                "max_drawdown": _safe_float(report.max_drawdown),
                "win_rate": _safe_float(report.win_rate),
                "total_trades": report.total_trades,
                "annual_volatility": _safe_float(report.annual_volatility),
                "calmar_ratio": _safe_float(report.calmar_ratio),
                "profit_factor": _safe_float(report.profit_factor),
                "benchmark_return": _safe_float(report.benchmark_return),
                "excess_return": _safe_float(report.excess_return),
                "equity_curve": _safe_json(report.equity_curve),
                "trades_detail": _safe_json(report.trades_detail),
                "monthly_returns": _safe_json(report.monthly_returns),
            }
        }

    except Exception as e:
        logger.error("backtest/voting/run 失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="回测服务异常")


# ===== 策略 API =====

@router.get("/strategies")
async def get_strategies():
    """获取所有已注册策略"""
    from ...config import load_strategies
    config = load_strategies()
    return {"success": True, "data": config.get("strategies", [])}


@router.get("/strategy/compare")
async def get_strategy_compare(
    stock_code: str | None = Query(None, min_length=6, max_length=6),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """策略对比数据 — 兼容 dashboard.html 的 fetch

    数据接口统一 (PR-fix 2026-06-24):
    - 如果传 stock_code,返回该股票的所有模型摘要 (与 /api/models/summary 一致)
    - 如果不传,返回全量回测的**策略聚合视图**(适配 dashboard.html render 函数):
      strategies: [{name, cnt, flat_count, win_count, avg_return,
                    avg_sharpe, avg_win_rate, best_return, worst_return, avg_trades}]
      total_records: int
      total_stocks: int (用于覆盖卡片)
    """
    from ...config import load_strategies
    from collections import defaultdict

    repo = _get_repo()

    if stock_code:
        # 单股多模型对比 (与 models/summary 行为一致)
        strategies_config = load_strategies()
        models = strategies_config.get("strategies", [])
        summaries = repo.get_models_summary_for_stock(models, stock_code)
        return {"success": True, "data": summaries}

    # 无 stock_code: 聚合按策略分组的全量回测
    # 修复: 始终以 YAML 配置为策略源, 有回测数据则补充, 无数据则显示 0
    strategies_config = load_strategies()
    all_strategy_names = {s.get("name", ""): s for s in strategies_config.get("strategies", [])}
    
    results = repo.get_recent_backtests(limit=500)

    # 按 strategy_name 分组聚合
    by_strategy: dict = defaultdict(list)
    for r in results:
        name = r.strategy.name if r.strategy else "未知"
        by_strategy[name].append(r)

    def _aggregate(rows: list) -> dict:
        """从回测行聚合统计"""
        returns = [r.total_return for r in rows if r.total_return is not None]
        sharpes = [r.sharpe_ratio for r in rows if r.sharpe_ratio is not None]
        win_rates = [r.win_rate for r in rows if r.win_rate is not None]
        trade_counts = [r.total_trades for r in rows if r.total_trades is not None]
        cnt = len(rows)
        flat_count = sum(1 for r in returns if abs(r) < 0.5)
        win_count = sum(1 for r in returns if r > 0)
        avg_return = round(sum(returns) / len(returns), 2) if returns else 0
        avg_sharpe = round(sum(sharpes) / len(sharpes), 2) if sharpes else 0
        avg_win_rate = round(sum(win_rates) / len(win_rates), 2) if win_rates else 0
        best_return = round(max(returns), 2) if returns else 0
        worst_return = round(min(returns), 2) if returns else 0
        avg_trades = round(sum(trade_counts) / len(trade_counts), 1) if trade_counts else 0
        return {
            "cnt": cnt, "flat_count": flat_count, "win_count": win_count,
            "avg_return": avg_return, "avg_sharpe": avg_sharpe,
            "avg_win_rate": avg_win_rate, "best_return": best_return,
            "worst_return": worst_return, "avg_trades": avg_trades,
        }

    empty_agg = _aggregate([])  # 兜底模板 {cnt:0, ...}

    strategies_agg = []
    for name in sorted(all_strategy_names.keys()):
        rows = by_strategy.get(name, [])
        agg = _aggregate(rows) if rows else empty_agg.copy()
        agg["name"] = name
        strategies_agg.append(agg)

    # 有回测的按 avg_return 降序排, 无回测的排在末尾
    strategies_agg.sort(key=lambda x: (x["cnt"] > 0, x["avg_return"]), reverse=True)

    # 聚合统计
    total_stocks = len({r.stock_code for r in results if r.stock_code})

    return {
        "success": True,
        "total_records": len(results),
        "total_stocks": total_stocks,
        "total_strategies": len(all_strategy_names),  # 来自 YAML 的真实策略数
        "strategies": strategies_agg,
        "data": strategies_agg,
    }


# ===== 模型汇总 API =====

@router.get("/models/summary")
async def get_models_summary(
    stock_code: str = Query(..., min_length=6, max_length=6),
    start_date: str | None = Query(None),  # 数据接口统一: 改为可选,与 repo.get_models_summary_for_stock 一致
    end_date: str | None = Query(None),
):
    """
    对指定股票+区间，返回所有模型最近回测结果摘要。
    用于工作台"一键对比"功能。

    注: start_date/end_date 当前由 repo.get_models_summary_for_stock 忽略(取最新结果),
    保留参数仅为 API 向后兼容,前端可不传。
    """
    from ...config import load_strategies
    strategies_config = load_strategies()
    models = strategies_config.get("strategies", [])

    repo = _get_repo()
    # PR3.3: 用 repo 封装方法替代直 ORM 查询
    summaries = repo.get_models_summary_for_stock(models, stock_code)
    return {"success": True, "data": summaries}


# ===== 回测结果 API =====

@router.get("/backtest/results")
async def get_backtest_results(limit: int = 20):
    """获取最近的回测结果

    数据接口统一 (PR-fix 2026-06-24):
    - max_drawdown 永远输出**负数**(与 PR2.2 metrics.performance 约定一致)
      旧数据 (PR2.2 前) 存的是正数,在此处统一转负值
    - 所有 None 字段转 None (前端可直接判断)
    """
    repo = _get_repo()
    results = repo.get_recent_backtests(limit)
    data = []
    for r in results:
        # PR2.2 符号约定:max_drawdown 永远 ≤ 0
        # 旧数据可能存的是绝对值(>0),在此处归一化
        mdd = r.max_drawdown
        if mdd is not None and mdd > 0:
            mdd = -mdd
        data.append({
            "id": r.id,
            "strategy_name": r.strategy.name if r.strategy else "未知",
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "total_return": r.total_return,
            "sharpe_ratio": r.sharpe_ratio,
            "max_drawdown": mdd,
            "created_at": str(r.created_at),
        })
    return {"success": True, "data": data}


# ===== 批量回测 API =====

@router.post("/backtest/batch/run", dependencies=[Depends(require_permission("run_batch_backtest"))])
async def run_batch_backtest(req: BatchBacktestRequest):
    """批量回测: 多策略 × 多股票"""
    try:
        from ...config import load_strategies

        strategies_config = load_strategies()

        # 加载策略类
        strategy_classes = []
        for s_name in req.strategy_names:
            found = False
            for s in strategies_config.get("strategies", []):
                if s["name"] == s_name and s.get("strategy_type", "signal") == "signal":
                    strategy_classes.append(safe_import_strategy(s["class_path"]))
                    found = True
                    break
            if not found:
                raise ValueError(f"未找到策略: {s_name}")

        engine = BacktestEngine()
        reports = engine.run_batch(
            strategy_classes=strategy_classes,
            stock_codes=req.stock_codes,
            start_date=date.fromisoformat(req.start_date),
            end_date=date.fromisoformat(req.end_date),
            initial_capital=req.initial_capital,
        )

        # 排序并返回排名
        ranked = engine.rank_batch_results(reports, sort_by="sharpe_ratio")

        return {"success": True, "total": len(ranked), "data": ranked}

    except Exception as e:
        logger.error("批量回测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="批量回测服务异常")


# ===== 参数搜索 API =====

class ParamSearchRequest(BaseModel):
    strategy_name: str
    stock_code: str
    start_date: str
    end_date: str
    initial_capital: float = 100000
    param_spec: dict  # {"fast_period": [3,5,10], "slow_period": [15,26,40]}
    mode: str = "grid"       # grid / random / bayesian
    metric: str = "sharpe_ratio"
    workers: int = 4
    n_iter: int = 50         # random/bayesian 采样次数


@router.post("/backtest/paramsearch/run", dependencies=[Depends(require_permission("run_batch_backtest"))])
async def run_param_search(req: ParamSearchRequest):
    """参数搜索: 网格/随机/贝叶斯"""
    try:
        from ...config import load_strategies

        strategies_config = load_strategies()
        strategy_class = None
        for s in strategies_config.get("strategies", []):
            if s["name"] == req.strategy_name and s.get("strategy_type", "signal") == "signal":
                strategy_class = safe_import_strategy(s["class_path"])
                break

        if strategy_class is None:
            raise ValueError(f"未找到策略: {req.strategy_name}")

        engine = BacktestEngine()
        start_date = date.fromisoformat(req.start_date)
        end_date = date.fromisoformat(req.end_date)

        if req.mode == "grid":
            results = engine.run_grid_search(
                strategy_class=strategy_class,
                stock_code=req.stock_code,
                start_date=start_date,
                end_date=end_date,
                param_grid=req.param_spec,
                metric=req.metric,
                max_workers=req.workers,
            )
        elif req.mode == "random":
            param_ranges = {}
            for k, vals in req.param_spec.items():
                if all(isinstance(v, int) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "int")
                elif all(isinstance(v, float) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "float")
                else:
                    param_ranges[k] = (vals, vals, "categorical")
            results = engine.run_random_search(
                strategy_class=strategy_class,
                stock_code=req.stock_code,
                start_date=start_date,
                end_date=end_date,
                param_ranges=param_ranges,
                n_iter=req.n_iter,
                metric=req.metric,
                max_workers=req.workers,
            )
        elif req.mode == "bayesian":
            param_ranges = {}
            for k, vals in req.param_spec.items():
                if all(isinstance(v, int) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "int")
                elif all(isinstance(v, float) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "float")
                else:
                    param_ranges[k] = (vals, vals, "categorical")
            results = engine.run_bayesian_search(
                strategy_class=strategy_class,
                stock_code=req.stock_code,
                start_date=start_date,
                end_date=end_date,
                param_ranges=param_ranges,
                n_iter=req.n_iter,
                metric=req.metric,
                max_workers=req.workers,
            )
        else:
            raise ValueError(f"未知搜索模式: {req.mode}")

        return {"success": True, "total": len(results), "data": results}

    except Exception as e:
        logger.error("参数搜索失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="参数搜索服务异常")



# ===== 股票池筛选 API =====

@router.get("/stockpool/list")
async def get_stock_pool(
    exclude_st: bool = Query(True),
    industry: str | None = Query(None),
    min_mcap: float | None = Query(None),
    max_mcap: float | None = Query(None),
    max_stocks: int | None = Query(None),  # 旧参数名,保留兼容
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),  # 新分页参数
):
    """获取符合条件的股票池

    数据接口统一 (PR-fix 2026-06-24):
    - 字段: code / name / industry / market (新增)
    - 分页: 支持 page+page_size (新) 和 max_stocks (旧,二选一)
    - 名称清洗: 全角空格 / 连续空格 → 单空格
    """
    repo = _get_repo()
    df = repo.get_stock_list()

    if exclude_st:
        df = df[~df["name"].str.contains("ST|退市", na=False)]

    if industry and "industry" in df.columns:
        df = df[df["industry"].str.contains(industry, na=False)]

    if min_mcap and "mcap_yi" in df.columns:
        df = df[df["mcap_yi"] >= min_mcap]

    if max_mcap and "mcap_yi" in df.columns:
        df = df[df["mcap_yi"] <= max_mcap]

    # max_stocks 优先 (旧参数),否则 page+page_size 分页
    if max_stocks is not None:
        df = df.head(max_stocks)
    else:
        start_idx = (page - 1) * page_size
        df = df.iloc[start_idx:start_idx + page_size]

    # 安全序列化:NaN/None 一律转 "" (NaN 不可 JSON)
    def _safe_str(v):
        if v is None:
            return ""
        try:
            import math
            if isinstance(v, float) and math.isnan(v):
                return ""
        except (TypeError, ValueError):
            pass
        s = str(v)
        # 清洗名称: 多余空格 (e.g. "万  科Ａ" → "万科Ａ")
        s = " ".join(s.split())
        return s

    data = []
    for _, row in df.iterrows():
        data.append({
            "code": _safe_str(row.get("code")),
            "name": _safe_str(row.get("name")),
            "industry": _safe_str(row.get("industry")),
            "market": _safe_str(row.get("market")),  # 新增字段 (数据接口统一)
        })

    return {
        "success": True,
        "total": len(df),
        "page": page,
        "page_size": page_size,
        "data": data,
    }


# ============================================================
# 模拟交易 API
# ============================================================

@router.post("/simulate/run", dependencies=[Depends(require_permission("run_simulate"))])
async def api_simulate_run(body: dict):
    """运行模拟交易"""
    try:
        from ...strategies.trading.config import TradingConfig
        from ...strategies.simulator import Simulator

        config = TradingConfig.from_dict(body.get("strategy", {}))
        if body.get("initial_capital"):
            config.initial_capital = body["initial_capital"]

        codes = body.get("codes", [])
        if not codes:
            raise HTTPException(400, "codes 不能为空")

        start_date = body.get("start_date", "")
        end_date = body.get("end_date", "")
        strategy_id = body.get("strategy_id", 6)

        sim = Simulator(config)
        result = sim.run(codes, start_date, end_date, strategy_id)
        return {
            "run_id": result.run_id,
            "status": result.status,
            "total_return": result.total_return,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "error": result.error,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("simulate/run 失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="模拟交易服务异常")


@router.get("/simulate/positions")
async def api_simulate_positions(run_id: str = Query(...)):
    """获取持仓状态（运行中或已完成）"""
    from src.data import data_mgr
    try:
        return {"positions": data_mgr.simulation.get_positions(run_id)}
    except Exception as e:
        logger.warning("simulate/positions 失败 (返回空): %s", e)
        return {"positions": [], "available": False}


@router.get("/simulate/trades")
async def api_simulate_trades(
    run_id: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """获取交易明细"""
    from src.data import data_mgr
    try:
        return data_mgr.simulation.get_trades(run_id, page=page, page_size=page_size)
    except Exception as e:
        logger.warning("simulate/trades 失败 (返回空): %s", e)
        return {"trades": [], "total": 0, "page": page, "page_size": page_size, "available": False}


@router.get("/simulate/performance")
async def api_simulate_performance(run_id: str = Query(...)):
    """获取绩效指标"""
    from src.data import data_mgr
    try:
        perf = data_mgr.simulation.get_performance(run_id)
        if not perf:
            return {"available": False, "reason": f"run_id {run_id} 不存在"}
        return perf
    except Exception as e:
        logger.warning("simulate/performance 失败 (返回空): %s", e)
        return {"available": False, "reason": str(e)[:100]}


@router.get("/simulate/list")
async def api_simulate_list(limit: int = Query(20, ge=1, le=100)):
    """列出所有模拟运行记录

    数据接口统一: simulation 表不存在或为空时,返回空列表而非 500。
    这样前端 simulate.html 在 DB 未初始化时也能正常加载页面。
    """
    from src.data import data_mgr
    try:
        return {"simulations": data_mgr.simulation.list_runs(limit=limit)}
    except Exception as e:
        # 表不存在 (OperationalError) 或查询失败 → 返回空列表
        logger.warning("simulate/list 失败 (返回空列表): %s", e)
        return {"simulations": [], "available": False, "reason": str(e)[:100]}


@router.get("/simulate/equity")
async def api_simulate_equity(run_id: str = Query(...)):
    """获取净值曲线"""
    from src.data import data_mgr
    try:
        return {"equity": data_mgr.simulation.get_equity(run_id), "available": True}
    except Exception as e:
        logger.warning("simulate/equity 失败 (返回空): %s", e)
        return {"equity": [], "available": False}


# ============================================================
# 独立模板专用端点 (data-monitor / backtest-lab / v5)
# 这些模板从 output/ 移植过来,前端硬编码了 URL,后端补齐
# ============================================================

@router.get("/status")
async def api_status():
    """通用服务状态 — data-monitor.html 用"""
    from datetime import datetime
    repo = _get_repo()
    try:
        coverage = repo.get_data_coverage()
        db_status = repo.get_db_status()
        tables = db_status.get("tables", [])
        return {
            "success": True,
            "status": "ok",
            "time": datetime.now().isoformat(timespec="seconds"),
            "coverage": coverage,
            "loaded": coverage.get("total_records", 0),
            "dims": [t.get("name") for t in tables if t.get("row_count", 0) > 0],
            "task": {"status": "idle", "progress": 0},
            "service": "quant-trading-system",
            "version": "v2.0",
        }
    except Exception as e:
        return {"success": False, "status": "degraded", "error": str(e)[:200]}


@router.get("/data/db-status")
async def api_data_db_status():
    """数据库健康度 — data-monitor.html 用"""
    repo = _get_repo()
    try:
        status = repo.get_db_status()
        return {"success": True, "available": True, "data": status, **status}
    except Exception as e:
        return {"success": False, "available": False, "error": str(e)[:200]}


# ===== Walk-Forward OOS 验证 API (PR-fix 2026-06-24, LIVE_TRADING 阶段 1) =====

@router.get("/walk_forward/runs")
async def api_walk_forward_runs(limit: int = Query(50, ge=1, le=200)):
    """列出所有 walk_forward OOS 验证运行 (按创建时间倒序)"""
    repo = _get_repo()
    try:
        runs = repo.list_walk_forward_runs(limit=limit)
        return {"success": True, "available": True, "total": len(runs), "data": runs}
    except Exception as e:
        logger.warning("walk_forward/runs 失败: %s", e)
        return {"success": False, "available": False, "error": str(e)[:200]}


@router.get("/walk_forward/runs/{run_id}")
async def api_walk_forward_run_detail(run_id: int):
    """单次 walk_forward 运行详情 (含 N 个窗口明细)"""
    repo = _get_repo()
    try:
        detail = repo.get_walk_forward_run(int(run_id))
        if detail is None:
            return {"success": False, "error": f"run_id {run_id} 不存在"}
        return {"success": True, "data": detail}
    except Exception as e:
        logger.warning("walk_forward/runs/%s 失败: %s", run_id, e)
        return {"success": False, "error": str(e)[:200]}


@router.get("/walk_forward/summary")
async def api_walk_forward_summary():
    """Walk-Forward 全局汇总 (跨 run) — dashboard 顶部卡片用"""
    repo = _get_repo()
    try:
        summary = repo.get_walk_forward_summary()
        return {"success": True, "data": summary}
    except Exception as e:
        logger.warning("walk_forward/summary 失败: %s", e)
        return {"success": False, "error": str(e)[:200]}


@router.get("/backtest/history")
async def api_backtest_history(limit: int = Query(50, ge=1, le=500)):
    """回测历史列表 — backtest-lab.html 用 (兼容旧 URL 命名)"""
    repo = _get_repo()
    try:
        rows = repo.get_recent_backtests(limit=limit)
        out = []
        for r in rows:
            out.append({
                "id": r.id,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "strategy_name": r.strategy.name if r.strategy else "未知",
                "start_date": str(r.start_date) if r.start_date else None,
                "end_date": str(r.end_date) if r.end_date else None,
                "total_return": _safe_float(r.total_return),
                "sharpe_ratio": _safe_float(r.sharpe_ratio),
                "max_drawdown": _safe_float(r.max_drawdown),
                "win_rate": _safe_float(r.win_rate),
                "trade_count": getattr(r, "trade_count", None),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return {"success": True, "data": out, "total": len(out)}
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)[:200]}


@router.get("/v5/scan-results")
async def api_v5_scan_results(limit: int = Query(20, ge=1, le=200)):
    """v5 扫描结果 — v5.html 用 (兼容旧版移植)"""
    repo = _get_repo()
    try:
        # 复用 get_recent_backtests 拿最新回测, 前端按 strategy 名字过滤
        rows = repo.get_recent_backtests(limit=limit * 4)
        out = []
        for r in rows:
            strat_name = r.strategy.name if r.strategy else ""
            if "v5" not in strat_name.lower() and "hybrid" not in strat_name.lower():
                continue
            out.append({
                "id": r.id,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "strategy": strat_name,
                "total_return": _safe_float(r.total_return),
                "sharpe_ratio": _safe_float(r.sharpe_ratio),
                "max_drawdown": _safe_float(r.max_drawdown),
                "win_rate": _safe_float(r.win_rate),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
            if len(out) >= limit:
                break
        return {"success": True, "data": out, "total": len(out), "available": True}
    except Exception as e:
        return {"success": True, "data": [], "total": 0, "available": False, "reason": str(e)[:100]}


# ===== 个股详情页数据接口 (leek-fund 风格) =====

def _parse_md_table(text: str) -> list[dict]:
    """westock CLI 返回 markdown 表格 → list[dict]
    格式: | col1 | col2 |... \\n | --- | --- |...\\n | v1 | v2 |...
    """
    if not text or not isinstance(text, str):
        return []
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for ln in lines[2:]:  # 跳过分隔行
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append({h: cells[i] for i, h in enumerate(header)})
    return rows


def _to_westock_code(code: str) -> str:
    """600519 → sh600519, 000001 → sz000001, 6 位 sh/sz/bj 自动加前缀"""
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if len(code) == 6:
        if code.startswith(("5", "6", "9")):
            return "sh" + code
        if code.startswith(("0", "1", "2", "3")):
            return "sz" + code
        if code.startswith(("4", "8")):
            return "bj" + code
    return code


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


@router.get("/stock/quote")
async def stock_quote(code: str = Query(..., min_length=4, max_length=10)):
    """个股行情快照 — leek-fund 风格当前价+今开+最高+最低+成交量

    2026-06-27 改造: 数据源优先 westock, 失败时降级到本地 DB daily_price
    """
    from ...data.westock import get_kline
    wcode = _to_westock_code(code)
    rows = []
    name = code
    try:
        r = get_kline(wcode, "day", 5, "qfq")
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        if rows:
            name = rows[0].get("name") or code
    except Exception as e:
        logger.warning("stock/quote westock 失败, 降级到 DB: %s", e)

    if not rows:
        try:
            from sqlalchemy import text
            repo = _get_repo()
            with repo.engine.connect() as conn:
                raw = conn.execute(
                    text("SELECT trade_date, open, high, low, close, volume "
                         "FROM daily_price WHERE code=:c ORDER BY trade_date DESC LIMIT 5"),
                    {"c": code.zfill(6)}
                ).fetchall()
            for r0 in raw:
                rows.append({"date": str(r0[0]), "open": r0[1], "high": r0[2],
                             "low": r0[3], "close": r0[4], "volume": r0[5]})
            # 补 name
            sb = conn.execute(
                text("SELECT name FROM stock_basic WHERE code=:c"),
                {"c": code.zfill(6)}
            ).first()
            if sb:
                name = sb[0]
        except Exception as e:
            logger.error("stock/quote DB 兜底失败: %s", e)

    if not rows:
        return {"success": False, "error": "无行情数据"}

    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    price = _f(latest.get("last") or latest.get("close"))
    prev_close = _f(prev.get("last") or prev.get("close")) if prev else None
    open_ = _f(latest.get("open"))
    high = _f(latest.get("high"))
    low = _f(latest.get("low"))
    volume = _f(latest.get("volume"))
    amount = _f(latest.get("amount"))
    change = (price - prev_close) if (price is not None and prev_close is not None) else None
    change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None

    return {
        "success": True,
        "data": {
            "code": wcode,
            "name": name,
            "date": latest.get("date"),
            "price": price,
            "prev_close": prev_close,
            "open": open_,
            "high": high,
            "low": low,
            "volume": volume,
            "amount": amount,
            "change": change,
            "change_pct": change_pct,
            "exchange": latest.get("exchange"),
        },
    }


@router.get("/stock/kline")
async def stock_kline(
    code: str = Query(..., min_length=4, max_length=10),
    period: str = Query("day", pattern="^(day|week|month)$"),
    limit: int = Query(120, ge=1, le=500),
    fq: str = Query("qfq", pattern="^(qfq|hfq|bfq)$"),
):
    """K线 — 前端 ECharts 直接画

    2026-06-27 改造: 数据源优先 westock, 失败时自动降级到本地数据库 daily_price
    修复 P1: 个股诊断页 /portfolio/stock_detail 之前 westock 拉不到时返回空, 失去本地兜底
    """
    from ...data.westock import get_kline
    wcode = _to_westock_code(code)
    rows = []
    try:
        r = get_kline(wcode, period, limit, fq)
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        # 2026-06-27 修复: westock 返回新的在前, 前端按 ASC(旧→新) 渲染, 需 reverse
        if rows and isinstance(rows[0], dict) and "date" in rows[0]:
            # westock 顺序通常为 desc, 反转成 asc(与 DB 兜底段一致)
            rows = list(reversed(rows))
    except Exception as e:
        logger.warning("stock/kline westock 失败, 降级到 DB: %s", e)

    # westock 无数据 → 用本地 DB 兜底
    if not rows:
        try:
            from sqlalchemy import text
            repo = _get_repo()
            with repo.engine.connect() as conn:
                # 取 limit*5 条 (周月线需要原始日线聚合, 但日线直接拿即可)
                raw = conn.execute(
                    text("SELECT trade_date, open, high, low, close, volume "
                         "FROM daily_price WHERE code=:c "
                         "ORDER BY trade_date DESC LIMIT :lim"),
                    {"c": code.zfill(6), "lim": limit}
                ).fetchall()
            for r0 in reversed(raw):  # 旧的在前
                rows.append({
                    "date": str(r0[0]),
                    "open": r0[1], "high": r0[2], "low": r0[3],
                    "close": r0[4], "volume": r0[5],
                })
        except Exception as e:
            logger.error("stock/kline DB 兜底失败: %s", e)

    if not rows:
        return {"success": False, "error": "无K线数据", "data": {"candles": [], "volumes": []}}

    candles, volumes = [], []
    for r0 in rows:  # DB 已按 ASC 排序, westock 的 reversed 已在 DB 段处理过
        o = _f(r0.get("open"))
        c = _f(r0.get("last") or r0.get("close"))
        h = _f(r0.get("high"))
        l = _f(r0.get("low"))
        v = _f(r0.get("volume"))
        d = r0.get("date", "")
        if o is None or c is None or h is None or l is None:
            continue
        candles.append([d, o, c, l, h])
        if v is not None:
            volumes.append({"date": d, "value": v, "dir": 1 if c >= o else -1})
    return {"success": True, "data": {"candles": candles, "volumes": volumes}}


@router.get("/stock/minute")
async def stock_minute(
    code: str = Query(..., min_length=4, max_length=10),
    days: int = Query(1, ge=1, le=5),
):
    """分时数据 — 当日 1 分钟切片"""
    from ...data.westock import _run_westock
    wcode = _to_westock_code(code)
    try:
        r = _run_westock(["minute", wcode, "--days", str(days)])
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        if not rows:
            return {"success": False, "error": "无分时数据", "data": []}
        out = []
        for r0 in rows:
            price = _f(r0.get("price"))
            if price is None:
                continue
            out.append({
                "time": r0.get("time", ""),
                "price": price,
                "volume": _f(r0.get("volume")) or 0,
                "amount": _f(r0.get("amount")) or 0,
            })
        return {"success": True, "data": out, "total": len(out)}
    except Exception as e:
        logger.error("stock/minute 失败: %s", e)
        return {"success": False, "error": str(e)[:200], "data": []}


@router.get("/stock/profile")
async def stock_profile(code: str = Query(..., min_length=4, max_length=10)):
    """公司简况 — 行业、概念、上市日期、注册资本"""
    from ...data.westock import get_profile
    wcode = _to_westock_code(code)
    try:
        r = get_profile(wcode)
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        if not rows:
            return {"success": False, "error": "无公司信息", "data": {}}
        p = rows[0]
        return {
            "success": True,
            "data": {
                "code": p.get("code", wcode),
                "name": p.get("name", code),
                "industry": p.get("industry", ""),
                "sector": p.get("sector", ""),
                "listedDate": p.get("listedDate", ""),
                "business": p.get("business", ""),
                "website": p.get("website", ""),
                "issuePrice": p.get("issuePrice", ""),
                "regCapital": p.get("regCapital", ""),
                "chairman": p.get("chairman", ""),
                "regAddress": p.get("regAddress", ""),
            },
        }
    except Exception as e:
        logger.error("stock/profile 失败: %s", e)
        return {"success": False, "error": str(e)[:200], "data": {}}


@router.get("/stock/finance")
async def stock_finance(
    code: str = Query(..., min_length=4, max_length=10),
    type_: str = Query("", pattern="^$|^(lrb|zcfz|xjll)$"),
    num: int = Query(4, ge=1, le=10),
):
    """财务数据 — 利润表/资产负债表/现金流量表"""
    from ...data.westock import get_finance
    wcode = _to_westock_code(code)
    try:
        r = get_finance(wcode, type_, num)
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        return {"success": True, "data": rows, "total": len(rows)}
    except Exception as e:
        logger.error("stock/finance 失败: %s", e)
        return {"success": False, "error": str(e)[:200], "data": []}


@router.get("/stock/technical")
async def stock_technical(
    code: str = Query(..., min_length=4, max_length=10),
    group: str = Query("all", pattern="^(ma|macd|kdj|rsi|boll|bias|wr|dmi|all)$"),
):
    """技术指标 — 均线/MACD/KDJ/RSI/布林"""
    from ...data.westock import get_technical
    wcode = _to_westock_code(code)
    try:
        r = get_technical(wcode, group)
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        return {"success": True, "data": rows, "total": len(rows)}
    except Exception as e:
        logger.error("stock/technical 失败: %s", e)
        return {"success": False, "error": str(e)[:200], "data": []}


# ===== 2026-06-26 Ardot 落地收尾: 补齐缺失端点 =====


@router.get("/stock/{code}/backtest")
async def stock_backtest_alias(
    code: str,
    strategy: str = Query(..., description="策略 ID 或名称 (兼容前端 dropdown)"),
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    capital: float = Query(100000, description="初始资金"),
):
    """单股回测 — backtest_lab.html 用 (GET 简化形式)

    内部委托: 调用 POST /api/backtest/run 但返回相同格式
    这是 /api/stock/{code}/backtest 的 alias, 方便前端 GET 调用
    """
    # 2026-06-26: 前端 dropdown 的 value 是策略 name, 兼容 — 若是 name 则查 strategy_config 取 id
    from sqlalchemy import text
    repo = _get_repo()
    try:
        with repo.engine.connect() as conn:
            # 解析 strategy 参数: 数字 → 直接用, 字符串 → 查 strategy_config.name → id
            if strategy.isdigit():
                strategy_id = int(strategy)
            else:
                row_sid = conn.execute(
                    text("SELECT id FROM strategy_config WHERE name = :n LIMIT 1"),
                    {"n": strategy}
                ).first()
                if not row_sid:
                    return {"success": False, "error": f"未找到策略: {strategy}", "data": None}
                strategy_id = row_sid[0]
            row = conn.execute(
                text("""SELECT r.id, r.total_return, r.annual_return, r.sharpe_ratio,
                               r.max_drawdown, r.win_rate, r.total_trades, r.final_equity,
                               r.equity_curve, r.trades_detail, r.monthly_returns,
                               s.name AS strategy_name, r.start_date, r.end_date
                        FROM backtest_result r
                        JOIN strategy_config s ON r.strategy_id = s.id
                        WHERE r.stock_code = :c AND r.strategy_id = :s
                        ORDER BY r.created_at DESC LIMIT 1"""),
                {"c": code, "s": strategy_id}
            ).first()
            if not row:
                return {"success": False, "error": f"无 {code} 策略 {strategy} 的回测记录", "data": None}
            import json as _json
            return {
                "success": True,
                "summary": {
                    "stockCode": code,
                    "strategyId": strategy_id,
                    "strategyName": row[11],
                    "totalReturn": float(row[1]) if row[1] is not None else 0,
                    "annualReturn": float(row[2]) if row[2] is not None else 0,
                    "sharpeRatio": float(row[3]) if row[3] is not None else 0,
                    "maxDrawdown": float(row[4]) if row[4] is not None else 0,
                    "winRate": float(row[5]) if row[5] is not None else 0,
                    "totalTrades": row[6] or 0,
                    "finalEquity": float(row[7]) if row[7] is not None else 0,
                    "startDate": str(row[12]) if row[12] else start,
                    "endDate": str(row[13]) if row[13] else end,
                },
                "equity_curve": _json.loads(row[8]) if row[8] else [],
                "trades": _json.loads(row[9]) if row[9] else [],
                "monthly_returns": _json.loads(row[10]) if row[10] else {},
            }
    except Exception as e:
        logger.error("stock/%s/backtest 失败: %s", code, e)
        return {"success": False, "error": str(e)[:200], "data": None}


@router.get("/stock/{code}")
async def stock_basic(code: str):
    """单股基本信息 — diagnose.html / portfolio.html / predict.html 用

    内部委托: 先尝试 /api/stock/search?q=<code>, 找不到再用 stock_basic 表查询
    """
    from sqlalchemy import text
    repo = _get_repo()
    try:
        with repo.engine.connect() as conn:
            row = conn.execute(
                text("SELECT code, name, market, industry, list_date FROM stock_basic WHERE code = :c"),
                {"c": code}
            ).first()
        if row:
            return {
                "success": True,
                "data": {
                    "code": row[0],
                    "name": row[1],
                    "market": row[2],
                    "industry": row[3],
                    "list_date": str(row[4]) if row[4] else None,
                }
            }
        return {"success": False, "error": "股票不存在", "data": None}
    except Exception as e:
        logger.error("stock/%s 失败: %s", code, e)
        return {"success": False, "error": str(e)[:200], "data": None}


# ============================================================
# 预测面板 4 端点 (2026-06-26 新增 / 重写)
# 路由顺序: 先静态 (/predict/{stats,verify,history}) 后参数 (/predict/{code})
#          避免 {code} 贪婪匹配 history/verify/stats 字面量
# 字段命名严格对齐 scripts/param_server.py, 保证两端兼容
# ============================================================


@router.get("/predict/stats")
async def predict_stats():
    """预测效果统计 — predict_dashboard.html / predict_verify.html 用

    Returns:
        {total, hit_rate, bin_stats: [{bin, count, avg_return, up_rate}],
         available?, reason?, message?}
    """
    try:
        rows = get_data_manager().query(
            "SELECT pred_proba, signal, actual_return_20d, actual_return_60d "
            "FROM prediction_record "
            "WHERE verified=1 AND actual_return_20d IS NOT NULL"
        )
    except (OperationalError, Exception) as e:
        if "no such table" in str(e):
            return {"total": 0, "hit_rate": 0, "bin_stats": [],
                    "available": False, "reason": "prediction_record 表不存在"}
        logger.error("predict/stats 失败: %s", e)
        return {"total": 0, "hit_rate": 0, "bin_stats": [], "error": str(e)[:200]}

    total = len(rows)
    if total == 0:
        return {"total": 0, "hit_rate": 0, "bin_stats": [],
                "message": "暂无已验证预测记录，请先调用 /api/predict/verify"}

    # 整体命中率 (预测涨→实际涨 + 预测跌→实际跌)
    hits = sum(1 for r in rows
               if (r.get("pred_proba") or 0) >= 0.55 and (r.get("actual_return_20d") or 0) > 0
               or (r.get("pred_proba") or 0) < 0.4 and (r.get("actual_return_20d") or 0) < 0)

    # 分组统计 (<0.4 / 0.4-0.55 / >=0.55)
    bins: dict[str, list[float]] = {"<0.4": [], "0.4-0.55": [], ">=0.55": []}
    for r in rows:
        p = r.get("pred_proba")
        if p is None:
            continue
        v = float(r.get("actual_return_20d") or 0)
        if p < 0.4:
            bins["<0.4"].append(v)
        elif p >= 0.55:
            bins[">=0.55"].append(v)
        else:
            bins["0.4-0.55"].append(v)

    stats = []
    for label, vals in bins.items():
        if vals:
            avg_ret = sum(vals) / len(vals)
            up_rate = sum(1 for v in vals if v > 0) / len(vals) * 100
            stats.append({"bin": label, "count": len(vals),
                          "avg_return": round(avg_ret, 2), "up_rate": round(up_rate, 1)})

    return {"total": total,
            "hit_rate": round(hits / total * 100, 1) if total else 0,
            "bin_stats": stats}


@router.get("/predict/verify")
async def predict_verify():
    """验证预测准确率 — 用 data/all_7d_scores.json 回填 prediction_record

    Returns:
        {updated, total_pending, available?, reason?}

    注: all_7d_scores.json 不存在时返 available=False (优雅降级)
    """
    scores_path = _Path("data/all_7d_scores.json")
    if not scores_path.exists():
        return {"updated": 0, "total_pending": 0,
                "available": False, "reason": "all_7d_scores.json 不存在"}

    import json as _json
    with open(scores_path, encoding="utf-8") as f:
        all_scores = _json.load(f)

    # code + year_month → actual_return 映射
    ret_map: dict[tuple[str, str], dict] = {}
    for s in all_scores:
        key = (str(s["code"]).zfill(6), s.get("year_month", ""))
        if key not in ret_map or s.get("as_of_date", "") > ret_map[key].get("date", ""):
            ret_map[key] = {
                "ret_20d": s.get("ret_20d"),
                "ret_60d": s.get("ret_60d"),
                "date": s.get("as_of_date", ""),
            }

    try:
        rows = get_data_manager().query(
            "SELECT id, code, pred_month FROM prediction_record "
            "WHERE verified=0 OR verified IS NULL"
        )
    except (OperationalError, Exception) as e:
        if "no such table" in str(e):
            return {"updated": 0, "total_pending": 0,
                    "available": False, "reason": "prediction_record 表不存在"}
        raise

    updated = 0
    data_mgr = get_data_manager()
    for r in rows:
        key = (str(r["code"]).zfill(6), r["pred_month"])
        if key in ret_map:
            data = ret_map[key]
            data_mgr.execute(
                "UPDATE prediction_record SET actual_return_20d=:r20, "
                "actual_return_60d=:r60, verified=1 WHERE id=:id",
                {"r20": data["ret_20d"], "r60": data["ret_60d"], "id": r["id"]},
            )
            updated += 1
    return {"updated": updated, "total_pending": len(rows)}


@router.get("/predict/history")
async def predict_history(code: str = "", limit: int = 50):
    """预测历史记录 — predict_dashboard.html 用

    Args:
        code: 股票代码 (空 = 全部)
        limit: 返回数量

    Returns:
        {total, results: [{code, stock_name, pred_month, as_of_date, pred_proba,
                          signal, auc, dim_scores, logistic_coef, actual_return_20d,
                          actual_return_60d, verified, created_at}]}
    """
    import json as _json
    try:
        if code:
            code = str(code).zfill(6)
            rows = get_data_manager().query(
                "SELECT * FROM prediction_record WHERE code=:code "
                "ORDER BY pred_month DESC LIMIT :limit",
                {"code": code, "limit": int(limit)},
            )
        else:
            rows = get_data_manager().query(
                "SELECT * FROM prediction_record ORDER BY created_at DESC LIMIT :limit",
                {"limit": int(limit)},
            )
    except (OperationalError, Exception) as e:
        if "no such table" in str(e):
            return {"total": 0, "results": [], "available": False,
                    "reason": "prediction_record 表不存在"}
        logger.error("predict/history 失败: %s", e)
        return {"total": 0, "results": [], "error": str(e)[:200]}

    results = []
    for r in rows:
        d = dict(r)
        for k in ("dim_scores", "logistic_coef"):
            if d.get(k) and isinstance(d[k], str):
                try:
                    d[k] = _json.loads(d[k])
                except Exception:
                    pass
        for k in ("created_at", "as_of_date"):
            if d.get(k):
                d[k] = str(d[k])[:19]
        results.append(d)
    return {"total": len(results), "results": results}


@router.get("/predict/{code}")
async def predict_for_stock(code: str):
    """单股预测 — diagnose.html / signal_dashboard.html / predict_dashboard.html 用

    2026-06-26 重写: 返回扁平 schema (与 param_server.py 一致)
    数据流: LogReg(bull_8d_monthly_result.json) + 实时 ScorerRegistry 7-dim
            lh_institutional 复用 InstitutionalScorer (v3 已用 dragon_tiger 数据)
            结果 UPSERT 到 prediction_record 表
    """
    from src.scoring import ScorerRegistry

    code = str(code).zfill(6)
    try:
        # ── 1. 加载 LogReg 模型 ──
        model_path = _Path("data/bull_8d_monthly_result.json")
        if not model_path.exists():
            return {"error": "预测模型文件不存在: data/bull_8d_monthly_result.json",
                    "available": False, "code": code}
        import json as _json
        with open(model_path, encoding="utf-8") as f:
            model = _json.load(f)
        coef = model.get("logistic_coef", {})
        intercept = float(model.get("intercept", 0.0))
        auc = float(model.get("auc", 0.5515))

        # ── 2. 实时计算 7 维评分 ──
        # ScorerRegistry.name → dashboard.dim_key
        DIM_MAP = {
            "technical":   "tech",
            "fundamental": "fundam",
            "fund_flow":   "fund",
            "institutional": "institutional",
            "sentiment":   "sentiment",
            "news_event":  "news_event",
            "chip":        "chip",
        }
        scorer_names = list(DIM_MAP.keys())
        dim_scores: dict[str, float] = {}
        as_of_date = ""
        # 单独 try/except 每个 scorer, 避免一个失败导致全部回退到 0
        # 2026-06-27 修复: TechnicalScorer/FundFlowScorer/SentimentScorer 接受 (code, as_of_date),
        #                ChipScorer 在 __init__ 里就崩(表不存在), 把整个 get 过程也包到 try 内
        from datetime import date as _today_date
        today_str = _today_date.today().isoformat()
        for name in scorer_names:
            try:
                # 2026-06-27 修复: ScorerRegistry.get 也包 try, 处理 __init__ 抛异常的 scorer
                try:
                    scorer = ScorerRegistry.get(name)
                except Exception as e_init:
                    logger.warning("scorer %s 注册/初始化失败: %s", name, e_init)
                    dim_scores[DIM_MAP[name]] = 0.0
                    continue
                # 优先 (code, as_of_date) 签名
                try:
                    r = scorer.score(code, today_str)
                except TypeError:
                    # 不接受 as_of_date 的 (chip / fundamental) 走 (code,)
                    try:
                        r = scorer.score(code)
                    except TypeError:
                        r = scorer.score(code, None)
                dim_scores[DIM_MAP[name]] = float(r.get("weighted") or 0)
                if not as_of_date and r.get("as_of_date"):
                    as_of_date = str(r["as_of_date"])[:10]
            except Exception as e:
                logger.warning("scorer %s(%s) failed: %s", name, code, e)
                dim_scores[DIM_MAP[name]] = 0.0

        # lh_institutional: 复用 InstitutionalScorer (v3 用 dragon_tiger 数据驱动)
        dim_scores["lh_institutional"] = dim_scores.get("institutional", 0.0)

        # ── 3. 构造 LogReg 特征向量 ──
        # coef keys 是 *_weighted 形式 (e.g. tech_weighted), 映射回 dim_scores key
        x = []
        for coef_key in coef.keys():
            base = coef_key.replace("_weighted", "")
            x.append(dim_scores.get(base, 0.0))
        if not x:
            return {"error": "模型系数为空", "available": False, "code": code}

        # ── 4. 计算概率 (sigmoid) ──
        coef_arr = [float(coef[k]) for k in coef.keys()]
        logit = sum(xi * ci for xi, ci in zip(x, coef_arr)) + intercept
        proba = 1.0 / (1.0 + math.exp(-logit))
        proba = max(0.0, min(1.0, proba))
        signal = "买入" if proba >= 0.55 else ("回避" if proba < 0.4 else "中性")

        # ── 5. pred_month / as_of_date ──
        from datetime import date as _date
        pred_month = as_of_date[:7] if as_of_date else _date.today().strftime("%Y-%m")
        if not as_of_date:
            as_of_date = _date.today().strftime("%Y-%m-%d")

        # ── 6. UPSERT prediction_record ──
        try:
            data_mgr = get_data_manager()
            existing = data_mgr.query(
                "SELECT id FROM prediction_record WHERE code=:code AND pred_month=:pm",
                {"code": code, "pm": pred_month},
            )
            dim_scores_json = _json.dumps(dim_scores, ensure_ascii=False)
            coef_json = _json.dumps(coef, ensure_ascii=False)
            from datetime import datetime as _dt
            now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")  # 本地时区足够 (与 utcnow 等价语义)
            if existing:
                data_mgr.execute(
                    "UPDATE prediction_record SET pred_proba=:p, signal=:s, "
                    "dim_scores=:d, logistic_coef=:lc, as_of_date=:ad WHERE id=:id",
                    {"p": round(proba, 4), "s": signal, "d": dim_scores_json,
                     "lc": coef_json, "ad": as_of_date, "id": existing[0]["id"]},
                )
            else:
                data_mgr.execute(
                    "INSERT INTO prediction_record "
                    "(code, stock_name, pred_month, as_of_date, pred_proba, signal, "
                    " auc, dim_scores, logistic_coef, verified, created_at) "
                    "VALUES (:code, '', :pm, :ad, :p, :s, :a, :d, :lc, 0, :ts)",
                    {"code": code, "pm": pred_month, "ad": as_of_date,
                     "p": round(proba, 4), "s": signal, "a": auc,
                     "d": dim_scores_json, "lc": coef_json, "ts": now},
                )
        except Exception as e:
            logger.warning("prediction_record UPSERT 失败 (非致命): %s", e)

        # ── 7. 扁平响应 (与 param_server 一致) ──
        return {
            "code": code,
            "pred_month": pred_month,
            "as_of_date": as_of_date,
            "pred_proba_up": round(proba, 4),
            "signal": signal,
            "dim_scores": dim_scores,
            "auc": auc,
            "model": "logreg_v1",
            "_note": {"lh_institutional": "derived_from=institutional(v3 uses dragon_tiger)"},
        }
    except Exception as e:
        logger.error("predict/%s 失败: %s", code, e)
        return {"error": str(e)[:200], "available": False, "code": code}


# 2026-06-26: 新增 /api/signal/list — signal.html 用
# 返回全市场最新一期 prediction_record + stock_basic 基础信息
# 含 dim_scores (8 维评分 JSON) + avg_score + ret_20d / ret_60d
# 修复 P0-2: 旧 JS 调 /api/stock/screener (无评分字段) → 新调本端点
@router.get("/signal/list")
async def signal_list(
    min_score: float = Query(0, ge=0, le=20, description="最低综合分过滤"),
    sort_by: str = Query("avg_score", description="avg_score | ret_20d | ret_60d"),
    limit: int = Query(500, ge=1, le=2000),
    as_of_date: str | None = Query(None, description="指定数据截止日 (空=最新一期)"),
):
    """全市场综合信号 — signal.html 专用

    Returns:
        {success, total, as_of_date, stocks: [{
            code, name, industry, market,
            dim_scores: {tech_weighted, fundam_weighted, ...},
            avg_score, signal, pred_proba,
            ret_20d, ret_60d, pred_month
        }]}
    """
    import json as _json
    from sqlalchemy import text as _sql_text

    repo = _get_repo()
    try:
        with repo.engine.connect() as conn:
            # 1) 找最新一期 as_of_date (或用传入的)
            if as_of_date:
                target_date = as_of_date
            else:
                latest = conn.execute(_sql_text(
                    "SELECT MAX(as_of_date) AS d FROM prediction_record"
                )).fetchone()
                target_date = str(latest[0]) if latest and latest[0] else None

            if not target_date:
                return {"success": True, "total": 0, "as_of_date": None,
                        "stocks": [], "message": "暂无预测记录"}

            # 2) 拉本期所有 stock + dim_scores + 实际收益
            #    LEFT JOIN stock_basic 拿 industry/market
            rows = conn.execute(_sql_text("""
                SELECT p.code, p.stock_name, p.pred_proba, p.signal, p.dim_scores,
                       p.actual_return_20d, p.actual_return_60d, p.pred_month, p.as_of_date,
                       s.industry, s.market
                FROM prediction_record p
                LEFT JOIN stock_basic s ON p.code = s.code
                WHERE p.as_of_date = :d
            """), {"d": target_date}).fetchall()

        # 3) 组装返回数据
        stocks = []
        for r in rows:
            # 解析 dim_scores JSON
            dim = {}
            raw_dim = r[4]  # dim_scores
            if raw_dim and isinstance(raw_dim, str):
                try:
                    dim = _json.loads(raw_dim)
                except Exception:
                    dim = {}

            # 计算综合 avg_score (8 维加权平均, 0-20 scale)
            # 注意: dim_scores JSON 实际存的 key 是 tech/fundam/fund/... (无 _weighted 后缀)
            # 但 signal.html JS 期望 *_weighted 后缀, 这里加 _weighted 别名
            dim_keys = ["tech", "fundam", "fund", "institutional",
                        "sentiment", "news_event", "chip", "lh_institutional"]
            # 复制 dim 同时加 _weighted 别名, 兼容 signal.html 旧 JS
            dim_aliased = dict(dim)
            for k in dim_keys:
                if k in dim_aliased:
                    dim_aliased[f"{k}_weighted"] = dim_aliased[k]

            vals = [float(dim.get(k, 0) or 0) for k in dim_keys]
            avg_score = sum(vals) / len(vals) if vals else 0

            stocks.append({
                "code": str(r[0]).zfill(6),
                "name": (r[1] or "").strip(),
                "industry": (r[9] or "").strip(),
                "market": (r[10] or "").strip(),
                "pred_proba": float(r[2]) if r[2] is not None else None,
                "signal": r[3] or "中性",
                "dim_scores": dim_aliased,  # 2026-06-26: 包含 _weighted 后缀别名, 兼容 signal.html 旧 JS
                "avg_score": round(avg_score, 2),
                "ret_20d": float(r[5]) if r[5] is not None else None,
                "ret_60d": float(r[6]) if r[6] is not None else None,
                "pred_month": r[7] or "",
            })

        # 4) 过滤 + 排序
        if min_score > 0:
            stocks = [s for s in stocks if s["avg_score"] >= min_score]
        if sort_by in ("ret_20d", "ret_60d"):
            stocks.sort(key=lambda s: s.get(sort_by) or -1e9, reverse=True)
        else:  # avg_score
            stocks.sort(key=lambda s: s["avg_score"], reverse=True)
        stocks = stocks[:limit]

        return {
            "success": True,
            "total": len(stocks),
            "as_of_date": target_date,
            "stocks": stocks,
        }
    except Exception as e:
        logger.error("signal/list 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": str(e)[:200], "stocks": []}


@router.get("/strategy/signal/{code}")
async def strategy_signal(code: str):
    """单股综合信号 — signal_dashboard.html 用

    综合该股所有回测策略的方向, 给出共识信号 + XGBoost 概率 + 仓位建议
    数据结构兼容 signal_dashboard.html JS (d.composite_score / d.xgb_prediction / d.position 等)

    2026-06-26 重构: 补全字段, 修复 P0-3
    """
    from sqlalchemy import text
    import json as _json
    repo = _get_repo()
    try:
        with repo.engine.connect() as conn:
            rows = conn.execute(
                text("""SELECT s.name, r.total_return, r.sharpe_ratio, r.max_drawdown, r.win_rate, r.total_trades
                        FROM backtest_result r
                        JOIN strategy_config s ON r.strategy_id = s.id
                        WHERE r.stock_code = :c
                        ORDER BY r.total_return DESC"""),
                {"c": code}
            ).fetchall()

            if not rows:
                return {
                    "success": False,
                    "available": False,
                    "error": "无策略信号",
                    "code": code,
                    # 给前端一个最小可用结构, 避免 undefined 满天飞
                    "composite_score": 0,
                    "signal": "回避",
                    "position": "空仓",
                    "position_pct": 0,
                    "pred_month": "",
                    "xgb_prediction": {"proba": 0, "signal": "回避"},
                    "strategy_vote": {"for": 0, "total": 0, "total_return": 0, "avg_sharpe": 0},
                    "market_filter": {"hs300_above_ma20": True},
                    "strategy_details": [],
                    "strategies": [],
                }

            total = len(rows)
            up_count = sum(1 for r in rows if (r[1] or 0) > 0)
            avg_return = sum((r[1] or 0) for r in rows) / total
            avg_sharpe = sum((r[2] or 0) for r in rows) / total
            consensus = up_count / total

            # 共识信号
            if consensus >= 0.6:
                signal = "buy"
                signal_zh = "买入"
            elif consensus <= 0.4:
                signal = "sell"
                signal_zh = "回避"
            else:
                signal = "hold"
                signal_zh = "中性"

            # 仓位: 综合共识 + 平均收益 → 重/轻/空
            if consensus >= 0.6 and avg_return > 5:
                position = "重仓"
                position_pct = 80
            elif consensus >= 0.45 and avg_return > 0:
                position = "轻仓"
                position_pct = 30
            else:
                position = "空仓"
                position_pct = 0

            # 大盘过滤 (简化: 默认正常, 实际可查沪深300 < MA20 时减半)
            market_filter = {"hs300_above_ma20": True}

            # XGBoost 概率: 用 consensus 近似 (实际应查 prediction_record.dim_scores)
            xgb_proba = round(consensus, 3)
            xgb_signal = "买入" if xgb_proba >= 0.55 else ("中性" if xgb_proba >= 0.4 else "回避")

            # 综合评分: consensus * 0.6 + (策略胜率均值) * 0.4
            avg_win_rate = sum((r[4] or 50) for r in rows) / total
            composite_score = round(consensus * 0.6 + (avg_win_rate / 100) * 0.4, 3)

            # 找最新 prediction_record 取 pred_month
            pred_row = conn.execute(
                text("SELECT pred_month, pred_proba, dim_scores FROM prediction_record "
                     "WHERE code=:c ORDER BY as_of_date DESC LIMIT 1"),
                {"c": code}
            ).fetchone()
            pred_month = pred_row[0] if pred_row else ""
            if pred_row and pred_row[1] is not None:
                xgb_proba = float(pred_row[1])
                xgb_signal = "买入" if xgb_proba >= 0.55 else ("中性" if xgb_proba >= 0.4 else "回避")

            # 策略投票明细 (10 笔)
            strategy_details = [
                {
                    "name": r[0],
                    "return": round(float(r[1] or 0), 2),
                    "sharpe": round(float(r[2] or 0), 2),
                    "win_rate": round(float(r[4] or 0), 1),
                    "trades": int(r[5] or 0),
                    "vote": "看多" if (r[1] or 0) > 0 else "看空",
                }
                for r in rows[:10]
            ]

            return {
                "success": True,
                "available": True,
                "code": code,
                # signal_dashboard.html JS 期望的字段:
                "composite_score": composite_score,
                "signal": signal_zh,  # 用中文
                "signal_en": signal,
                "position": position,
                "position_pct": position_pct,
                "pred_month": pred_month,
                "xgb_prediction": {"proba": xgb_proba, "signal": xgb_signal},
                "strategy_vote": {
                    "for": up_count,
                    "total": total,
                    "total_return": round(avg_return, 2),
                    "avg_sharpe": round(avg_sharpe, 2),
                },
                "market_filter": market_filter,
                "strategy_details": strategy_details,
                # 旧字段保留兼容:
                "data": {
                    "stock_code": code,
                    "total_strategies": total,
                    "up_count": up_count,
                    "consensus": round(consensus, 3),
                    "signal": signal,
                    "signal_zh": signal_zh,
                    "avg_return": round(avg_return, 2),
                    "avg_sharpe": round(avg_sharpe, 2),
                    "strategies": [
                        {"name": r[0], "total_return": float(r[1]) if r[1] is not None else 0,
                         "sharpe_ratio": float(r[2]) if r[2] is not None else 0}
                        for r in rows[:10]
                    ],
                },
            }
    except Exception as e:
        logger.error("strategy/signal/%s 失败: %s", code, e)
        return {"success": False, "available": False, "error": str(e)[:200], "code": code,
                "composite_score": 0, "signal": "回避", "position": "空仓", "position_pct": 0,
                "xgb_prediction": {"proba": 0, "signal": "回避"},
                "strategy_vote": {"for": 0, "total": 0, "total_return": 0, "avg_sharpe": 0},
                "market_filter": {"hs300_above_ma20": True},
                "strategy_details": []}


@router.post("/v5/run")
async def v5_run(
    top_n: int = Query(default=20, ge=1, le=100, description="选 Top N"),
):
    """v5 混合策略扫描 — v5_tuning.html 用 (POST 触发)

    简化实现: 复用 backtest_result 取最近 v5 策略的回测, 取 top_n
    """
    from sqlalchemy import text
    repo = _get_repo()
    try:
        with repo.engine.connect() as conn:
            # 查 v5 系列策略 ID
            cfg = conn.execute(
                text("SELECT id, name, class_path FROM strategy_config WHERE name LIKE '%v5%' OR name LIKE '%hybrid%' LIMIT 5")
            ).fetchall()
            if not cfg:
                return {"success": False, "error": "未配置 v5 策略", "data": []}

            strategy_ids = [c[0] for c in cfg]
            placeholders = ",".join(f":s{i}" for i in range(len(strategy_ids)))
            params = {f"s{i}": sid for i, sid in enumerate(strategy_ids)}
            params["limit"] = top_n * 5

            sql = f"""SELECT r.stock_code, r.stock_name, r.total_return, r.sharpe_ratio,
                             r.max_drawdown, r.win_rate, s.name AS strategy_name
                      FROM backtest_result r
                      JOIN strategy_config s ON r.strategy_id = s.id
                      WHERE r.strategy_id IN ({placeholders})
                      ORDER BY r.sharpe_ratio DESC NULLS LAST
                      LIMIT :limit"""
            try:
                rows = conn.execute(text(sql), params).fetchall()
            except Exception:
                # SQLite 不支持 NULLS LAST, 重试
                sql2 = sql.replace("DESC NULLS LAST", "DESC")
                rows = conn.execute(text(sql2), params).fetchall()

            out = [
                {
                    "stock_code": r[0], "stock_name": r[1],
                    "total_return": float(r[2]) if r[2] is not None else 0,
                    "sharpe_ratio": float(r[3]) if r[3] is not None else 0,
                    "max_drawdown": float(r[4]) if r[4] is not None else 0,
                    "win_rate": float(r[5]) if r[5] is not None else 0,
                    "strategy_name": r[6],
                }
                for r in rows[:top_n]
            ]
            return {"success": True, "data": out, "total": len(out), "triggered_strategies": [c[1] for c in cfg]}
    except Exception as e:
        logger.error("v5/run 失败: %s", e)
        return {"success": False, "error": str(e)[:200], "data": []}

