"""
回测 + 策略 + 模型对比 路由 (v2.1.2 #85 拆 api.py)
endpoint:
  POST /backtest/run                 — 单股信号回测
  POST /backtest/portfolio/run       — 组合回测 (选股策略)
  POST /backtest/voting/run          — 技术投票模型回测
  POST /backtest/batch/run           — 批量回测 (多策略 × 多股票)
  GET  /backtest/results             — 最近回测结果列表
  GET  /backtest/history             — 回测历史 (backtest-lab 兼容)
  GET  /strategies                   — 列出所有已注册策略
  GET  /strategy/compare             — 策略对比 (单股 / 全量聚合)
注: /v5/* 端点移至 system_routes.py (500 行限制)
"""
from __future__ import annotations
import json, logging, traceback
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from ._helpers import _safe_float, _safe_json, get_repo
logger = logging.getLogger(__name__)
from ...backtest.engine import BacktestEngine
from ...backtest.portfolio_engine import PortfolioBacktestEngine
from ..auth import safe_import_strategy, require_permission
router = APIRouter()

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

# ===== 共享: report → API 响应 dict =====
def _report_to_api(report, strategy_id: int | None) -> dict:
    """将 BacktestEngine 返回的 report 对象序列化为 API 响应 dict
    4 类回测 (run/portfolio/voting) 返回的字段完全一致,集中到一处避免重复。
    """
    return {
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

# ===== 单股信号回测 =====
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
        repo = get_repo()
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
            "report": _report_to_api(report, strategy_id),
        }
    except Exception as e:
        logger.error("backtest/run 失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="回测服务异常")

# ===== 组合回测 (选股策略) =====
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
        repo = get_repo()
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
            "report": _report_to_api(report, strategy_id),
        }
    except Exception as e:
        logger.error("backtest/portfolio/run 失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="回测服务异常")

# ===== 投票模型回测 (技术投票模型) =====
@router.post("/backtest/voting/run", dependencies=[Depends(require_permission("run_backtest"))])
async def run_voting_backtest(req: VotingBacktestRequest):
    """执行技术投票模型回测"""
    try:
        from ...models.technical_voting import TechnicalVotingModel
        repo = get_repo()
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
            "report": _report_to_api(report, strategy_id),
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
    repo = get_repo()
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

# ===== 回测结果 API =====
@router.get("/backtest/results")
async def get_backtest_results(limit: int = 20):
    """获取最近的回测结果
    数据接口统一 (PR-fix 2026-06-24):
    - max_drawdown 永远输出**负数**(与 PR2.2 metrics.performance 约定一致)
      旧数据 (PR2.2 前) 存的是正数,在此处统一转负值
    - 所有 None 字段转 None (前端可直接判断)
    """
    repo = get_repo()
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

# ===== 回测历史 (backtest-lab.html 兼容) =====
@router.get("/backtest/history")
async def api_backtest_history(limit: int = Query(50, ge=1, le=500)):
    """回测历史列表 — backtest-lab.html 用 (兼容旧 URL 命名)"""
    repo = get_repo()
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
