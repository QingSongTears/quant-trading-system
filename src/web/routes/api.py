"""
API 路由 (JSON 响应)
"""
import json
import threading
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...models.repository import DataRepository
from ...data.downloader import DataDownloader
from ...backtest.engine import BacktestEngine
from ...backtest.portfolio_engine import PortfolioBacktestEngine
from ..app import download_status as _download_status

router = APIRouter()


# ===== 请求模型 =====

class BacktestRequest(BaseModel):
    strategy_name: str
    stock_code: str
    start_date: str          # YYYY-MM-DD
    end_date: str
    initial_capital: float = 100000
    commission: Optional[float] = None
    stamp_duty: Optional[float] = None
    slippage: Optional[float] = None


class BatchBacktestRequest(BaseModel):
    strategy_names: List[str]
    stock_codes: List[str]
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
        return {"success": False, "error": str(e)}


@router.get("/data/search")
async def search_stocks(q: str = Query(..., min_length=1)):
    """搜索股票"""
    repo = DataRepository()
    try:
        df = repo.get_stock_list()
        mask = df["name"].str.contains(q) | df["code"].str.contains(q)
        results = df[mask].head(20).to_dict("records")
        return {"success": True, "data": results}
    except Exception as e:
        return {"success": False, "error": str(e)}


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
    if _download_status["running"]:
        return {
            "success": False,
            "error": "下载任务正在运行中，请等待完成",
            "current": _download_status["current"],
            "progress": _download_status["progress"],
            "total": _download_status["total"],
        }

    # 重置全局状态
    _download_status.update({
        "running": True, "mode": mode, "progress": 0,
        "total": 0, "current": "准备中...", "error": None,
        "result": None, "started_at": dt.now().isoformat(),
    })

    def _run():
        try:
            downloader = DataDownloader()
            if mode == "full":
                result = downloader.download_full(
                    progress_callback=lambda c, t, code, name: _download_status.update(
                        {"progress": c, "total": t, "current": f"{code} {name}"}
                    )
                )
            else:
                result = downloader.download_incremental(
                    progress_callback=lambda c, t, code, name: _download_status.update(
                        {"progress": c, "total": t, "current": f"{code} {name}"}
                    )
                )
            _download_status["running"] = False
            _download_status["result"] = result
        except Exception as e:
            _download_status["running"] = False
            _download_status["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"success": True, "message": "下载已启动", "mode": mode}


@router.get("/data/download/status")
async def get_download_status():
    """查询下载进度（使用全局状态管理器）"""
    return {
        "success": True,
        "running": _download_status["running"],
        "mode": _download_status["mode"],
        "progress": _download_status["progress"],
        "total": _download_status["total"],
        "current": _download_status["current"],
        "error": _download_status["error"],
        "result": _download_status["result"],
        "started_at": _download_status["started_at"],
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
                import importlib
                module_path, class_name = s["class_path"].rsplit(".", 1)
                module = importlib.import_module(module_path)
                strategy_class = getattr(module, class_name)
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

        # 持久化
        repo = DataRepository()
        session = repo.get_session()
        try:
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

            # 查找策略ID
            from ...models.database import StrategyConfig
            strategy_record = session.query(StrategyConfig).filter_by(name=req.strategy_name).first()
            strategy_id = strategy_record.id if strategy_record else None

            result_dict = report.to_db_dict(strategy_id)
            result_dict["stock_name"] = report.stock_name
            result_id = repo.save_backtest_result(session, result_dict)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

        return {
            "success": True,
            "result_id": result_id,
            "report": {
                "total_return": report.total_return,
                "annual_return": report.annual_return,
                "sharpe_ratio": report.sharpe_ratio,
                "max_drawdown": report.max_drawdown,
                "win_rate": report.win_rate,
                "total_trades": report.total_trades,
                "benchmark_return": report.benchmark_return,
                "excess_return": report.excess_return,
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===== 组合回测 API (选股策略) =====

@router.post("/backtest/portfolio/run")
async def run_portfolio_backtest(req: PortfolioBacktestRequest):
    """执行组合回测 (选股策略)"""
    try:
        from ...config import load_strategies
        import importlib

        strategies_config = load_strategies()
        strategy_class = None
        strategy_meta = None

        for s in strategies_config.get("strategies", []):
            if s["name"] == req.strategy_name and s.get("strategy_type") == "portfolio":
                module_path, class_name = s["class_path"].rsplit(".", 1)
                module = importlib.import_module(module_path)
                strategy_class = getattr(module, class_name)
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

        # 持久化
        repo = DataRepository()
        session = repo.get_session()
        try:
            for s in strategies_config.get("strategies", []):
                if s["name"] == req.strategy_name:
                    repo.save_strategy_config(
                        session, s["name"], s["class_path"],
                        json.dumps(s.get("params", {})),
                        s.get("description", ""),
                        s.get("source", "")
                    )
                    break

            from ...models.database import StrategyConfig
            strategy_record = session.query(StrategyConfig).filter_by(name=req.strategy_name).first()
            strategy_id = strategy_record.id if strategy_record else None

            result_dict = report.to_db_dict(strategy_id)
            result_dict["stock_name"] = report.stock_name
            result_id = repo.save_backtest_result(session, result_dict)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

        return {
            "success": True,
            "result_id": result_id,
            "report": {
                "total_return": report.total_return,
                "annual_return": report.annual_return,
                "sharpe_ratio": report.sharpe_ratio,
                "max_drawdown": report.max_drawdown,
                "win_rate": report.win_rate,
                "total_trades": report.total_trades,
                "benchmark_return": report.benchmark_return,
                "excess_return": report.excess_return,
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

        # 持久化
        session = repo.get_session()
        try:
            repo.save_strategy_config(
                session, "技术指标投票模型",
                "src.models.technical_voting.TechnicalVotingModel",
                "{}",
                "5策略投票委员会: 双均线+MACD+RSI+布林带+海龟",
                "综合投票模型"
            )

            from ...models.database import StrategyConfig
            strategy_record = session.query(StrategyConfig).filter_by(
                name="技术指标投票模型"
            ).first()
            strategy_id = strategy_record.id if strategy_record else None

            result_dict = report.to_db_dict(strategy_id)
            result_dict["stock_name"] = report.stock_name
            result_id = repo.save_backtest_result(session, result_dict)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

        return {
            "success": True,
            "result_id": result_id,
            "report": {
                "total_return": report.total_return,
                "annual_return": report.annual_return,
                "sharpe_ratio": report.sharpe_ratio,
                "max_drawdown": report.max_drawdown,
                "win_rate": report.win_rate,
                "total_trades": report.total_trades,
                "benchmark_return": report.benchmark_return,
                "excess_return": report.excess_return,
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===== 策略 API =====

@router.get("/strategies")
async def get_strategies():
    """获取所有已注册策略"""
    from ...config import load_strategies
    config = load_strategies()
    return {"success": True, "data": config.get("strategies", [])}


# ===== 回测结果 API =====

@router.get("/backtest/results")
async def get_backtest_results(limit: int = 20):
    """获取最近的回测结果"""
    repo = DataRepository()
    results = repo.get_recent_backtests(limit)
    data = []
    for r in results:
        data.append({
            "id": r.id,
            "strategy_name": r.strategy.name if r.strategy else "未知",
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "total_return": r.total_return,
            "sharpe_ratio": r.sharpe_ratio,
            "max_drawdown": r.max_drawdown,
            "created_at": str(r.created_at),
        })
    return {"success": True, "data": data}
