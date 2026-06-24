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
from ...data.downloader import DataDownloader
from ...data.westock_downloader import WestockDownloader
from ...backtest.engine import BacktestEngine
from ...backtest.portfolio_engine import PortfolioBacktestEngine
from ..app import download_status as _download_status
from ..app import update_download_status, get_download_status as _get_status
from ..auth import safe_import_strategy, verify_api_key

# 所有 /api/* 端点统一要求 Bearer token
router = APIRouter(dependencies=[Depends(verify_api_key)])


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
    repo = DataRepository()
    try:
        return {"success": True, "data": repo.get_data_coverage()}
    except Exception as e:
        logger.error("data/coverage 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": "数据服务异常"}


@router.get("/data/search")
async def search_stocks(q: str = Query(..., min_length=1)):
    """搜索股票"""
    repo = DataRepository()
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
    repo = DataRepository()
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
    repo = DataRepository()
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

@router.post("/data/download")
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
            # 下载器选择:WestockDownloader 是桩模块(2026-06-21 PR1.1),
            # 会抛 NotImplementedError;捕获后回退到 DataDownloader (AKShare)。
            # 之前的 fallback 逻辑因 WestockDownloader 不抛错而永远不触发,
            # 导致"全量下载"按钮静默无效 — 现在显式检测。
            try:
                from src.data.westock_downloader import WestockDownloaderNotImplemented
                downloader = WestockDownloader()
                source_name = "WeStock-Data (腾讯自选股)"
            except (WestockDownloaderNotImplemented, Exception) as init_err:
                logger.info(f"westock 不可用, 使用 AKShare (DataDownloader): {init_err}")
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

@router.post("/backtest/run")
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
                if s.get("engine") == "stock_screener" or s_type == "stock_screener":
                    raise ValueError(
                        f"策略 {req.strategy_name!r} 走 stock_screener 私有引擎, "
                        f"与主 backtesting.py 引擎不兼容 (需要先将其迁移到 "
                        f"src/backtest/base_strategy.py 的 BaseStrategy 接口). "
                        f"详见 stock_screener 文档。"
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
        repo = DataRepository()
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

@router.post("/backtest/portfolio/run")
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
        repo = DataRepository()
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

@router.post("/backtest/voting/run")
async def run_voting_backtest(req: VotingBacktestRequest):
    """执行技术投票模型回测"""
    try:
        from ...models.technical_voting import TechnicalVotingModel
        from ...models.repository import DataRepository

        repo = DataRepository()

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

    repo = DataRepository()

    if stock_code:
        # 单股多模型对比 (与 models/summary 行为一致)
        strategies_config = load_strategies()
        models = strategies_config.get("strategies", [])
        summaries = repo.get_models_summary_for_stock(models, stock_code)
        return {"success": True, "data": summaries}

    # 无 stock_code: 聚合按策略分组的全量回测
    results = repo.get_recent_backtests(limit=500)

    # 按 strategy_name 分组聚合
    by_strategy: dict = defaultdict(list)
    for r in results:
        name = r.strategy.name if r.strategy else "未知"
        by_strategy[name].append(r)

    strategies_agg = []
    for name, rows in by_strategy.items():
        returns = [r.total_return for r in rows if r.total_return is not None]
        sharpes = [r.sharpe_ratio for r in rows if r.sharpe_ratio is not None]
        win_rates = [r.win_rate for r in rows if r.win_rate is not None]
        trade_counts = [r.total_trades for r in rows if r.total_trades is not None]

        cnt = len(rows)
        # flat_count: 收益接近 0 的回测数(认为无意义)
        flat_count = sum(1 for r in returns if abs(r) < 0.5)
        active = cnt - flat_count
        win_count = sum(1 for r in returns if r > 0)

        avg_return = round(sum(returns) / len(returns), 2) if returns else 0
        avg_sharpe = round(sum(sharpes) / len(sharpes), 2) if sharpes else 0
        avg_win_rate = round(sum(win_rates) / len(win_rates), 2) if win_rates else 0
        best_return = round(max(returns), 2) if returns else 0
        worst_return = round(min(returns), 2) if returns else 0
        avg_trades = round(sum(trade_counts) / len(trade_counts), 1) if trade_counts else 0

        strategies_agg.append({
            "name": name,
            "cnt": cnt,
            "flat_count": flat_count,
            "win_count": win_count,
            "avg_return": avg_return,
            "avg_sharpe": avg_sharpe,
            "avg_win_rate": avg_win_rate,
            "best_return": best_return,
            "worst_return": worst_return,
            "avg_trades": avg_trades,
        })

    # 按 avg_return 降序排
    strategies_agg.sort(key=lambda x: x["avg_return"], reverse=True)

    # 聚合统计
    total_stocks = len({r.stock_code for r in results if r.stock_code})

    return {
        "success": True,
        "total_records": len(results),
        "total_stocks": total_stocks,
        "strategies": strategies_agg,
        "data": strategies_agg,  # 别名 (兼容其他可能的调用方)
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

    repo = DataRepository()
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
    repo = DataRepository()
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

@router.post("/backtest/batch/run")
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


@router.post("/backtest/paramsearch/run")
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
    repo = DataRepository()
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

@router.post("/simulate/run")
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
    repo = DataRepository()
    try:
        coverage = repo.get_data_coverage()
        return {
            "success": True,
            "status": "ok",
            "time": datetime.now().isoformat(timespec="seconds"),
            "coverage": coverage,
            "service": "quant-trading-system",
            "version": "v2.0",
        }
    except Exception as e:
        return {"success": False, "status": "degraded", "error": str(e)[:200]}


@router.get("/data/db-status")
async def api_data_db_status():
    """数据库健康度 — data-monitor.html 用"""
    repo = DataRepository()
    try:
        return {"success": True, "available": True, "data": repo.get_db_status()}
    except Exception as e:
        return {"success": False, "available": False, "error": str(e)[:200]}


# ===== Walk-Forward OOS 验证 API (PR-fix 2026-06-24, LIVE_TRADING 阶段 1) =====

@router.get("/walk_forward/runs")
async def api_walk_forward_runs(limit: int = Query(50, ge=1, le=200)):
    """列出所有 walk_forward OOS 验证运行 (按创建时间倒序)"""
    repo = DataRepository()
    try:
        runs = repo.list_walk_forward_runs(limit=limit)
        return {"success": True, "available": True, "total": len(runs), "data": runs}
    except Exception as e:
        logger.warning("walk_forward/runs 失败: %s", e)
        return {"success": False, "available": False, "error": str(e)[:200]}


@router.get("/walk_forward/runs/{run_id}")
async def api_walk_forward_run_detail(run_id: int):
    """单次 walk_forward 运行详情 (含 N 个窗口明细)"""
    repo = DataRepository()
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
    repo = DataRepository()
    try:
        summary = repo.get_walk_forward_summary()
        return {"success": True, "data": summary}
    except Exception as e:
        logger.warning("walk_forward/summary 失败: %s", e)
        return {"success": False, "error": str(e)[:200]}


@router.get("/backtest/history")
async def api_backtest_history(limit: int = Query(50, ge=1, le=500)):
    """回测历史列表 — backtest-lab.html 用 (兼容旧 URL 命名)"""
    repo = DataRepository()
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
    repo = DataRepository()
    try:
        # 复用 get_recent_backtests 拿最新回测, 前端按 strategy='v5_hybrid' 过滤
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
    """个股行情快照 — leek-fund 风格当前价+今开+最高+最低+成交量"""
    from ...data.westock import get_kline
    wcode = _to_westock_code(code)
    try:
        r = get_kline(wcode, "day", 5, "qfq")
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
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
                "name": latest.get("name", code),
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
    except Exception as e:
        logger.error("stock/quote 失败: %s", e)
        return {"success": False, "error": str(e)[:200]}


@router.get("/stock/kline")
async def stock_kline(
    code: str = Query(..., min_length=4, max_length=10),
    period: str = Query("day", pattern="^(day|week|month)$"),
    limit: int = Query(120, ge=1, le=500),
    fq: str = Query("qfq", pattern="^(qfq|hfq|bfq)$"),
):
    """K线 — 前端 ECharts 直接画"""
    from ...data.westock import get_kline
    wcode = _to_westock_code(code)
    try:
        r = get_kline(wcode, period, limit, fq)
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        if not rows:
            return {"success": False, "error": "无K线数据", "data": {"candles": [], "volumes": []}}

        candles, volumes = [], []
        for r0 in reversed(rows):  # 旧的在前
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
    except Exception as e:
        logger.error("stock/kline 失败: %s", e)
        return {"success": False, "error": str(e)[:200], "data": {"candles": [], "volumes": []}}


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
