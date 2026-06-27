"""
data / sector / screener / stockpool / models / paramsearch 路由 (v2.1.2 #85 拆 api.py)

endpoint:
  GET  /data/coverage
  GET  /data/search
  GET  /stock/search        (alias /api/data/search)
  GET  /sector
  GET  /stock/screener
  POST /data/download
  GET  /data/download/status
  GET  /models/summary
  GET  /stockpool/list
  POST /backtest/paramsearch/run
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ._helpers import get_repo

logger = logging.getLogger(__name__)

from ...data.downloader import DataDownloader
from ..app import update_download_status, get_download_status as _get_status
from ..auth import require_permission


router = APIRouter()


# ===== 请求模型 =====
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


# ===== 数据 API =====

@router.get("/data/coverage")
async def get_data_coverage():
    """获取数据覆盖概览"""
    repo = get_repo()
    try:
        return {"success": True, "data": repo.get_data_coverage()}
    except Exception as e:
        logger.error("data/coverage 失败: %s\n%s", e, traceback.format_exc())
        return {"success": False, "error": "数据服务异常"}


@router.get("/data/search")
async def search_stocks(q: str = Query(..., min_length=1)):
    """搜索股票"""
    repo = get_repo()
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
    repo = get_repo()
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
    repo = get_repo()
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
    # 检查是否有正在运行的下载
    current_status = _get_status()
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
        "result": None, "started_at": datetime.now().isoformat(),
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

    repo = get_repo()
    # PR3.3: 用 repo 封装方法替代直 ORM 查询
    summaries = repo.get_models_summary_for_stock(models, stock_code)
    return {"success": True, "data": summaries}


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
    repo = get_repo()
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


# ===== 参数搜索 API =====

@router.post("/backtest/paramsearch/run", dependencies=[Depends(require_permission("run_batch_backtest"))])
async def run_param_search(req: ParamSearchRequest):
    """参数搜索: 网格/随机/贝叶斯"""
    try:
        from ...config import load_strategies
        from ...backtest.engine import BacktestEngine

        strategies_config = load_strategies()
        strategy_class = None
        for s in strategies_config.get("strategies", []):
            if s["name"] == req.strategy_name and s.get("strategy_type", "signal") == "signal":
                from ..auth import safe_import_strategy
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
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="参数搜索服务异常")
