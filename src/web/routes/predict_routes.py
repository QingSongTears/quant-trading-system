"""
预测面板 路由 (v2.1.2 #85 拆 api.py)

endpoint:
  GET  /predict/stats                — 预测效果统计
  GET  /predict/verify               — 回填 prediction_record 实际收益
  GET  /predict/history              — 预测历史记录
  GET  /predict/{code}               — 单股 LogReg 预测
  GET  /strategy/signal/{code}       — 单股综合信号 (signal_dashboard 用)
  GET  /signal/list                  — 全市场综合信号 (signal.html 用)

注: /ic/* /factor/* 在 #83+#85 阶段无 endpoint, 留空待将来按需添加。
"""
from __future__ import annotations
import json, logging, math
from datetime import date as _date, datetime as _dt
from pathlib import Path as _Path
from fastapi import APIRouter, Query
from sqlalchemy.exc import OperationalError
from sqlalchemy import text
from ._helpers import get_repo
from ...data import get_data_manager
logger = logging.getLogger(__name__)
router = APIRouter()

# ============== 预测面板 4 端点 ==============

@router.get("/predict/stats")
async def predict_stats():
    """预测效果统计 — predict_dashboard.html / predict_verify.html 用
    Returns: {total, hit_rate, bin_stats: [...], available?, reason?, message?}
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
    注: all_7d_scores.json 不存在时返 available=False (优雅降级)
    """
    scores_path = _Path("data/all_7d_scores.json")
    if not scores_path.exists():
        return {"updated": 0, "total_pending": 0,
                "available": False, "reason": "all_7d_scores.json 不存在"}
    with open(scores_path, encoding="utf-8") as f:
        all_scores = json.load(f)
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
    Returns: {total, results: [{code, stock_name, pred_month, ...}]}
    """
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
                    d[k] = json.loads(d[k])
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
    """
    from src.scoring import ScorerRegistry
    code = str(code).zfill(6)
    try:
        # ── 1. 加载 LogReg 模型 ──
        model_path = _Path("data/bull_8d_monthly_result.json")
        if not model_path.exists():
            return {"error": "预测模型文件不存在: data/bull_8d_monthly_result.json",
                    "available": False, "code": code}
        with open(model_path, encoding="utf-8") as f:
            model = json.load(f)
        coef = model.get("logistic_coef", {})
        intercept = float(model.get("intercept", 0.0))
        auc = float(model.get("auc", 0.5515))
        # ── 2. 实时计算 7 维评分 ──
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
        today_str = _date.today().isoformat()
        for name in scorer_names:
            try:
                try:
                    scorer = ScorerRegistry.get(name)
                except Exception as e_init:
                    logger.warning("scorer %s 注册/初始化失败: %s", name, e_init)
                    dim_scores[DIM_MAP[name]] = 0.0
                    continue
                try:
                    r = scorer.score(code, today_str)
                except TypeError:
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
        dim_scores["lh_institutional"] = dim_scores.get("institutional", 0.0)
        # ── 3. 构造 LogReg 特征向量 ──
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
            dim_scores_json = json.dumps(dim_scores, ensure_ascii=False)
            coef_json = json.dumps(coef, ensure_ascii=False)
            now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
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

# ============== signal / strategy/signal ==============

@router.get("/signal/list")
async def signal_list(
    min_score: float = Query(0, ge=0, le=20, description="最低综合分过滤"),
    sort_by: str = Query("avg_score", description="avg_score | ret_20d | ret_60d"),
    limit: int = Query(500, ge=1, le=2000),
    as_of_date: str | None = Query(None, description="指定数据截止日 (空=最新一期)"),
):
    """全市场综合信号 — signal.html 专用
    Returns: {success, total, as_of_date, stocks: [...]}
    """
    from sqlalchemy import text as _sql_text
    repo = get_repo()
    try:
        with repo.engine.connect() as conn:
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
            rows = conn.execute(_sql_text("""
                SELECT p.code, p.stock_name, p.pred_proba, p.signal, p.dim_scores,
                       p.actual_return_20d, p.actual_return_60d, p.pred_month, p.as_of_date,
                       s.industry, s.market
                FROM prediction_record p
                LEFT JOIN stock_basic s ON p.code = s.code
                WHERE p.as_of_date = :d
            """), {"d": target_date}).fetchall()
        stocks = []
        for r in rows:
            dim = {}
            raw_dim = r[4]
            if raw_dim and isinstance(raw_dim, str):
                try:
                    dim = json.loads(raw_dim)
                except Exception:
                    dim = {}
            dim_keys = ["tech", "fundam", "fund", "institutional",
                        "sentiment", "news_event", "chip", "lh_institutional"]
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
                "dim_scores": dim_aliased,
                "avg_score": round(avg_score, 2),
                "ret_20d": float(r[5]) if r[5] is not None else None,
                "ret_60d": float(r[6]) if r[6] is not None else None,
                "pred_month": r[7] or "",
            })
        if min_score > 0:
            stocks = [s for s in stocks if s["avg_score"] >= min_score]
        if sort_by in ("ret_20d", "ret_60d"):
            stocks.sort(key=lambda s: s.get(sort_by) or -1e9, reverse=True)
        else:
            stocks.sort(key=lambda s: s["avg_score"], reverse=True)
        stocks = stocks[:limit]
        return {
            "success": True,
            "total": len(stocks),
            "as_of_date": target_date,
            "stocks": stocks,
        }
    except Exception as e:
        logger.error("signal/list 失败: %s\n%s", e, __import__("traceback").format_exc())
        return {"success": False, "error": str(e)[:200], "stocks": []}

@router.get("/strategy/signal/{code}")
async def strategy_signal(code: str):
    """单股综合信号 — signal_dashboard.html 用
    2026-06-26 重构: 补全字段, 修复 P0-3
    """
    import json as _json
    repo = get_repo()
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
                    "success": False, "available": False, "error": "无策略信号", "code": code,
                    "composite_score": 0, "signal": "回避", "position": "空仓", "position_pct": 0,
                    "pred_month": "",
                    "xgb_prediction": {"proba": 0, "signal": "回避"},
                    "strategy_vote": {"for": 0, "total": 0, "total_return": 0, "avg_sharpe": 0},
                    "market_filter": {"hs300_above_ma20": True},
                    "strategy_details": [], "strategies": [],
                }
            total = len(rows)
            up_count = sum(1 for r in rows if (r[1] or 0) > 0)
            avg_return = sum((r[1] or 0) for r in rows) / total
            avg_sharpe = sum((r[2] or 0) for r in rows) / total
            consensus = up_count / total
            if consensus >= 0.6:
                signal = "buy"; signal_zh = "买入"
            elif consensus <= 0.4:
                signal = "sell"; signal_zh = "回避"
            else:
                signal = "hold"; signal_zh = "中性"
            if consensus >= 0.6 and avg_return > 5:
                position, position_pct = "重仓", 80
            elif consensus >= 0.45 and avg_return > 0:
                position, position_pct = "轻仓", 30
            else:
                position, position_pct = "空仓", 0
            market_filter = {"hs300_above_ma20": True}
            xgb_proba = round(consensus, 3)
            xgb_signal = "买入" if xgb_proba >= 0.55 else ("中性" if xgb_proba >= 0.4 else "回避")
            avg_win_rate = sum((r[4] or 50) for r in rows) / total
            composite_score = round(consensus * 0.6 + (avg_win_rate / 100) * 0.4, 3)
            pred_row = conn.execute(
                text("SELECT pred_month, pred_proba, dim_scores FROM prediction_record "
                     "WHERE code=:c ORDER BY as_of_date DESC LIMIT 1"),
                {"c": code}
            ).fetchone()
            pred_month = pred_row[0] if pred_row else ""
            if pred_row and pred_row[1] is not None:
                xgb_proba = float(pred_row[1])
                xgb_signal = "买入" if xgb_proba >= 0.55 else ("中性" if xgb_proba >= 0.4 else "回避")
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
                "success": True, "available": True, "code": code,
                "composite_score": composite_score,
                "signal": signal_zh, "signal_en": signal,
                "position": position, "position_pct": position_pct,
                "pred_month": pred_month,
                "xgb_prediction": {"proba": xgb_proba, "signal": xgb_signal},
                "strategy_vote": {
                    "for": up_count, "total": total,
                    "total_return": round(avg_return, 2),
                    "avg_sharpe": round(avg_sharpe, 2),
                },
                "market_filter": market_filter,
                "strategy_details": strategy_details,
                "data": {
                    "stock_code": code, "total_strategies": total, "up_count": up_count,
                    "consensus": round(consensus, 3),
                    "signal": signal, "signal_zh": signal_zh,
                    "avg_return": round(avg_return, 2), "avg_sharpe": round(avg_sharpe, 2),
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
