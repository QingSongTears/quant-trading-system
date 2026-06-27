"""
模拟交易 API 路由 (v2.1.2 #85 拆 api.py)

endpoint:
  POST /simulate/run           — 触发模拟交易
  GET  /simulate/positions     — 持仓状态
  GET  /simulate/trades        — 交易明细 (分页)
  GET  /simulate/performance   — 绩效指标
  GET  /simulate/list          — 列出所有模拟运行
  GET  /simulate/equity        — 净值曲线
"""
from __future__ import annotations
import logging, traceback
from fastapi import APIRouter, Depends, HTTPException, Query
from ..auth import require_permission
logger = logging.getLogger(__name__)
router = APIRouter()

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
