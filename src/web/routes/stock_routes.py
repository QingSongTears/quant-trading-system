"""
个股详情页 API 路由 (v2.1.2 #85 拆 api.py) — leek-fund 风格

endpoint:
  GET /stock/quote             — 行情快照
  GET /stock/kline             — K 线
  GET /stock/minute            — 分时
  GET /stock/profile           — 公司简况
  GET /stock/finance           — 财务数据
  GET /stock/technical         — 技术指标
  GET /stock/{code}/backtest   — 单股历史回测 (backtest_lab.html 用)
  GET /stock/{code}            — 单股基本信息

注: helpers (_parse_md_table / _to_westock_code / _f) 在 _helpers.py
"""
from __future__ import annotations
import logging
from fastapi import APIRouter, Query
from ._helpers import _f, _parse_md_table, _to_westock_code, get_repo
logger = logging.getLogger(__name__)
router = APIRouter()

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
            from src.data import data_mgr
            from ...data.datafeed.base import code_to_vt_symbol
            vt = code_to_vt_symbol(code.zfill(6))
            bars = data_mgr.datafeed.get_bars(vt)
            for b in bars[-5:]:
                rows.append({
                    "date": str(b.datetime.date() if hasattr(b.datetime, "date") else b.datetime),
                    "open": b.open_price, "high": b.high_price,
                    "low": b.low_price, "close": b.close_price,
                    "volume": b.volume,
                })
            for c in data_mgr.datafeed.get_stock_list():
                if c.symbol == code.zfill(6):
                    name = c.name or name
                    break
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
            "code": wcode, "name": name, "date": latest.get("date"),
            "price": price, "prev_close": prev_close,
            "open": open_, "high": high, "low": low,
            "volume": volume, "amount": amount,
            "change": change, "change_pct": change_pct,
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
    """
    from ...data.westock import get_kline
    wcode = _to_westock_code(code)
    rows = []
    try:
        r = get_kline(wcode, period, limit, fq)
        rows = _parse_md_table(r.get("raw", "")) if "raw" in r else r.get("data", [])
        if rows and isinstance(rows[0], dict) and "date" in rows[0]:
            rows = list(reversed(rows))
    except Exception as e:
        logger.warning("stock/kline westock 失败, 降级到 DB: %s", e)
    if not rows:
        try:
            from src.data import data_mgr
            from ...data.datafeed.base import code_to_vt_symbol
            vt = code_to_vt_symbol(code.zfill(6))
            bars = data_mgr.datafeed.get_bars(vt)
            for b in bars[-limit:]:
                rows.append({
                    "date": str(b.datetime.date() if hasattr(b.datetime, "date") else b.datetime),
                    "open": b.open_price, "high": b.high_price, "low": b.low_price,
                    "close": b.close_price, "volume": b.volume,
                })
        except Exception as e:
            logger.error("stock/kline DB 兜底失败: %s", e)
    if not rows:
        return {"success": False, "error": "无K线数据", "data": {"candles": [], "volumes": []}}
    candles, volumes = [], []
    for r0 in rows:
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
                "code": p.get("code", wcode), "name": p.get("name", code),
                "industry": p.get("industry", ""), "sector": p.get("sector", ""),
                "listedDate": p.get("listedDate", ""), "business": p.get("business", ""),
                "website": p.get("website", ""), "issuePrice": p.get("issuePrice", ""),
                "regCapital": p.get("regCapital", ""), "chairman": p.get("chairman", ""),
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
    """
    repo = get_repo()
    try:
        with repo.engine.connect() as conn:
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
                    "stockCode": code, "strategyId": strategy_id, "strategyName": row[11],
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
    """单股基本信息 — diagnose.html / portfolio.html / predict.html 用"""
    repo = get_repo()
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
                    "code": row[0], "name": row[1], "market": row[2],
                    "industry": row[3], "list_date": str(row[4]) if row[4] else None,
                }
            }
        return {"success": False, "error": "股票不存在", "data": None}
    except Exception as e:
        logger.error("stock/%s 失败: %s", code, e)
        return {"success": False, "error": str(e)[:200], "data": None}
