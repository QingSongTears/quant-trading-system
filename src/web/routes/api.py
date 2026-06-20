"""
API 路由 (JSON 响应)
"""
import json
import logging
import threading
import traceback
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ...models.repository import DataRepository
from ...data.downloader import DataDownloader
from ...data.westock_downloader import WestockDownloader
from ...backtest.engine import BacktestEngine
from ...backtest.portfolio_engine import PortfolioBacktestEngine
from ..app import download_status as _download_status

router = APIRouter()


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
            # 优先使用 westock-data 下载器 (批量快速, 不限流)
            # 仅在 westock 不可用时回退到 AKShare
            try:
                downloader = WestockDownloader()
                source_name = "WeStock-Data (腾讯自选股)"
            except Exception as init_err:
                logger.warning(f"westock 初始化失败, 回退 AKShare: {init_err}")
                downloader = DataDownloader()
                source_name = "AKShare"

            _download_status["current"] = f"使用 {source_name} 下载中..."

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
            result["source"] = result.get("source", source_name)
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
        raise HTTPException(status_code=500, detail=str(e))


# ===== 策略 API =====

@router.get("/strategies")
async def get_strategies():
    """获取所有已注册策略"""
    from ...config import load_strategies
    config = load_strategies()
    return {"success": True, "data": config.get("strategies", [])}


# ===== 模型汇总 API =====

@router.get("/models/summary")
async def get_models_summary(
    stock_code: str = Query(..., min_length=6, max_length=6),
    start_date: str = Query(...),
    end_date: str = Query(...),
):
    """
    对指定股票+区间，返回所有模型最近回测结果摘要。
    用于工作台"一键对比"功能。
    """
    from ...config import load_strategies
    strategies_config = load_strategies()
    models = strategies_config.get("strategies", [])

    repo = DataRepository()
    session = repo.get_session()
    try:
        from ...models.database import BacktestResult, StrategyConfig
        summaries = []
        for s in models:
            strategy_record = session.query(StrategyConfig).filter_by(
                name=s["name"]
            ).first()
            if not strategy_record:
                # 尝试直接用名称模糊匹配
                strategy_record = session.query(StrategyConfig).filter(
                    StrategyConfig.name.like(f"%{s['name']}%")
                ).first()

            if strategy_record:
                result = session.query(BacktestResult).filter(
                    BacktestResult.strategy_id == strategy_record.id,
                    BacktestResult.stock_code == stock_code,
                ).order_by(BacktestResult.created_at.desc()).first()

                if result:
                    summaries.append({
                        "strategy_name": s["name"],
                        "strategy_type": s.get("strategy_type", "signal"),
                        "total_return": result.total_return,
                        "annual_return": result.annual_return,
                        "sharpe_ratio": result.sharpe_ratio,
                        "max_drawdown": result.max_drawdown,
                        "win_rate": result.win_rate,
                        "total_trades": result.total_trades,
                        "excess_return": result.excess_return,
                        "result_id": result.id,
                        "created_at": str(result.created_at),
                    })
                else:
                    summaries.append({
                        "strategy_name": s["name"],
                        "strategy_type": s.get("strategy_type", "signal"),
                        "has_data": False,
                        "result_id": None,
                    })
            else:
                summaries.append({
                    "strategy_name": s["name"],
                    "strategy_type": s.get("strategy_type", "signal"),
                    "has_data": False,
                    "result_id": None,
                })

        return {"success": True, "data": summaries}
    finally:
        session.close()


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


# ===== 批量回测 API =====

@router.post("/backtest/batch/run")
async def run_batch_backtest(req: BatchBacktestRequest):
    """批量回测: 多策略 × 多股票"""
    try:
        from ...config import load_strategies
        import importlib

        strategies_config = load_strategies()

        # 加载策略类
        strategy_classes = []
        for s_name in req.strategy_names:
            found = False
            for s in strategies_config.get("strategies", []):
                if s["name"] == s_name and s.get("strategy_type", "signal") == "signal":
                    module_path, class_name = s["class_path"].rsplit(".", 1)
                    module = importlib.import_module(module_path)
                    strategy_classes.append(getattr(module, class_name))
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
        raise HTTPException(status_code=500, detail=str(e))


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
        import importlib

        strategies_config = load_strategies()
        strategy_class = None
        for s in strategies_config.get("strategies", []):
            if s["name"] == req.strategy_name and s.get("strategy_type", "signal") == "signal":
                module_path, class_name = s["class_path"].rsplit(".", 1)
                module = importlib.import_module(module_path)
                strategy_class = getattr(module, class_name)
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
        raise HTTPException(status_code=500, detail=str(e))



# ===== 股票池筛选 API =====

@router.get("/stockpool/list")
async def get_stock_pool(
    exclude_st: bool = Query(True),
    industry: Optional[str] = Query(None),
    min_mcap: Optional[float] = Query(None),
    max_mcap: Optional[float] = Query(None),
    max_stocks: Optional[int] = Query(None),
):
    """获取符合条件的股票池"""
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

    codes = df["code"].tolist()
    names = df["name"].tolist()
    industries = df["industry"].tolist() if "industry" in df.columns else []

    if max_stocks:
        codes = codes[:max_stocks]
        names = names[:max_stocks]

    return {
        "success": True,
        "total": len(codes),
        "data": [
            {"code": c, "name": n, "industry": industries[i] if industries else ""}
            for i, (c, n) in enumerate(zip(codes, names))
        ],
    }
