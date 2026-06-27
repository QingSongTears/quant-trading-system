"""
系统状态 / Walk-Forward / v5 路由 (v2.1.2 #85 拆 api.py)

endpoint:
  GET  /status                  — 通用服务状态 (data-monitor 用)
  GET  /data/db-status          — 数据库健康度
  GET  /walk_forward/runs       — 列出所有 WF 运行
  GET  /walk_forward/runs/{id}  — 单次 WF 详情
  GET  /walk_forward/summary    — WF 全局汇总
  POST /v5/run                  — v5 混合策略扫描触发 (v5_tuning.html)
  GET  /v5/scan-results         — v5 扫描结果 (v5.html)
"""
from __future__ import annotations
import logging
from datetime import datetime
from fastapi import APIRouter, Query
from ._helpers import _safe_float, get_repo
logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/status")
async def api_status():
    """通用服务状态 — data-monitor.html 用"""
    repo = get_repo()
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
    repo = get_repo()
    try:
        status = repo.get_db_status()
        return {"success": True, "available": True, "data": status, **status}
    except Exception as e:
        return {"success": False, "available": False, "error": str(e)[:200]}

@router.get("/walk_forward/runs")
async def api_walk_forward_runs(limit: int = Query(50, ge=1, le=200)):
    """列出所有 walk_forward OOS 验证运行 (按创建时间倒序)"""
    repo = get_repo()
    try:
        runs = repo.list_walk_forward_runs(limit=limit)
        return {"success": True, "available": True, "total": len(runs), "data": runs}
    except Exception as e:
        logger.warning("walk_forward/runs 失败: %s", e)
        return {"success": False, "available": False, "error": str(e)[:200]}

@router.get("/walk_forward/runs/{run_id}")
async def api_walk_forward_run_detail(run_id: int):
    """单次 walk_forward 运行详情 (含 N 个窗口明细)"""
    repo = get_repo()
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
    repo = get_repo()
    try:
        summary = repo.get_walk_forward_summary()
        return {"success": True, "data": summary}
    except Exception as e:
        logger.warning("walk_forward/summary 失败: %s", e)
        return {"success": False, "error": str(e)[:200]}

@router.post("/v5/run")
async def v5_run(
    top_n: int = Query(default=20, ge=1, le=100, description="选 Top N"),
):
    """v5 混合策略扫描 — v5_tuning.html 用 (POST 触发)
    简化实现: 复用 backtest_result 取最近 v5 策略的回测, 取 top_n
    """
    from sqlalchemy import text
    repo = get_repo()
    try:
        with repo.engine.connect() as conn:
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

@router.get("/v5/scan-results")
async def api_v5_scan_results(limit: int = Query(20, ge=1, le=200)):
    """v5 扫描结果 — v5.html 用 (兼容旧版移植)"""
    repo = get_repo()
    try:
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
