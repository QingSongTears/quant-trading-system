"""
研究类 API 路由 (2026-06-25 Phase 12)

提供:
  - /api/research/ic: 因子 IC (Rank IC 5日/20日, 按指标)
  - /api/research/dim-ic: 7 维评分维度对收益的 IC 贡献

IC 算法:
  对每只股票每只指标, 算 corr(指标(t), future_return(t+window))
  Spearman rank correlation (rank IC), 业界标准

数据源:
  - technical_indicators 表 (rsi14/kdj/macd/boll)
  - daily_price 表 (close 用于算 future_return)
  - 7 维评分: technical_scorer / fundamental / fund_flow / chip / institutional /
              sentiment / news_event (ScorerRegistry)

性能:
  - 默认 30 只股票 × 60 天 (可控, 1-2 秒)
  - 大数据量建议预计算到 ic_daily 表 (后续 PR)
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from ..auth import verify_api_key
from ...db.engine import get_engine


logger = logging.getLogger(__name__)
# 2026-06-25: prefix 在 app.py include_router 时加 (/api), 此处只加 /research
router = APIRouter(prefix="/research", tags=["research"])


# ============================================================
# 工具函数
# ============================================================


def _spearman_ic(x: np.ndarray, y: np.ndarray) -> float | None:
    """Spearman rank correlation (单值)"""
    if len(x) < 5 or len(y) < 5:
        return None
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    # 用 pandas 的 spearman 简化 (避免 scipy 依赖)
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < 5:
        return None
    rx = df["x"].rank()
    ry = df["y"].rank()
    # Pearson on ranks
    return float(np.corrcoef(rx, ry)[0, 1])


def _compute_factor_ic(
    engine,
    indicator_col: str,
    window: int,
    start: str,
    end: str,
    stock_limit: int,
) -> dict[str, Any]:
    """
    算单个指标 vs future_return 的 IC

    Args:
        engine: SQLAlchemy engine
        indicator_col: technical_indicators 列名 (e.g. "rsi14")
        window: 未来收益窗口 (5 / 20 日)
        start/end: YYYY-MM-DD
        stock_limit: 股票数 (避免大表扫描)

    Returns:
        {name, window, mean_ic, std_ic, n, series: [{date, ic}]}
    """
    sql = text("""
        WITH stocks AS (
          SELECT DISTINCT code FROM technical_indicators
          WHERE trade_date BETWEEN :start AND :end
          ORDER BY code LIMIT :limit
        )
        SELECT
          t.code, t.trade_date,
          t.{col} AS factor,
          (SELECT close FROM daily_price d
           WHERE d.code = t.code AND d.trade_date > t.trade_date
           ORDER BY d.trade_date ASC LIMIT 1 OFFSET :window)
          AS future_close,
          (SELECT close FROM daily_price d
           WHERE d.code = t.code AND d.trade_date = t.trade_date LIMIT 1)
          AS today_close
        FROM technical_indicators t
        INNER JOIN stocks s USING (code)
        WHERE t.trade_date BETWEEN :start AND :end
          AND t.{col} IS NOT NULL
    """.format(col=indicator_col))

    df = pd.read_sql(sql, engine, params={
        "start": start, "end": end, "limit": stock_limit, "window": window - 1,
    })
    if df.empty:
        return {"name": indicator_col, "window": window, "mean_ic": None, "n": 0, "series": []}

    # future_return = future_close / today_close - 1
    df = df.dropna(subset=["today_close", "future_close", "factor"])
    df = df[df["today_close"] > 0]
    df["future_return"] = df["future_close"] / df["today_close"] - 1
    df = df[np.isfinite(df["future_return"])]

    # 按 trade_date 算每天的 cross-sectional IC
    series = []
    ics = []
    for date, group in df.groupby("trade_date"):
        if len(group) < 5:
            continue
        ic = _spearman_ic(group["factor"].values, group["future_return"].values)
        if ic is not None and np.isfinite(ic):
            ics.append(ic)
            series.append({"date": str(date), "ic": round(ic, 4)})

    if not ics:
        return {"name": indicator_col, "window": window, "mean_ic": None, "n": 0, "series": []}

    ics = np.array(ics)
    return {
        "name": indicator_col,
        "window": window,
        "mean_ic": round(float(ics.mean()), 4),
        "std_ic": round(float(ics.std()), 4),
        "icir": round(float(ics.mean() / ics.std()), 4) if ics.std() > 0 else None,
        "n": int(len(ics)),
        "series": series[-60:],  # 只返最近 60 天
    }


# ============================================================
# API 路由
# ============================================================


@router.get("/ic", dependencies=[Depends(verify_api_key)])
async def factor_ic(
    window: int = Query(5, description="未来收益窗口 (5/20 日)"),
    start: str = Query("2024-01-01", description="起始日期"),
    end: str = Query("2024-12-31", description="结束日期"),
    stock_limit: int = Query(30, description="股票数 (避免大表扫)"),
):
    """因子 IC 分析 (Spearman rank correlation)

    对每个技术指标算 Rank IC, 返 mean_ic + 时序

    返回:
        {
          "factors": [
            {name, window, mean_ic, std_ic, icir, n, series},
            ...
          ]
        }
    """
    engine = get_engine()
    indicators = ["rsi14", "macd_hist", "kdj_k", "kdj_j", "boll_lower"]
    factors = []
    for col in indicators:
        try:
            r = _compute_factor_ic(engine, col, window, start, end, stock_limit)
            factors.append(r)
        except Exception as e:
            logger.warning(f"IC {col} 失败: {e}")
            factors.append({"name": col, "window": window, "error": str(e)})
    return {"factors": factors, "window": window, "start": start, "end": end}


# 7 维评分维度
DIM_NAMES = [
    ("technical", "TechnicalScorer"),
    ("fundamental", "FundamentalScorer"),
    ("fund_flow", "FundFlowScorer"),
    ("chip", "ChipScorer"),
    ("institutional", "InstitutionalScorer"),
    ("sentiment", "SentimentScorer"),
    ("news_event", "NewsEventScorer"),
]


@router.get("/dim-ic", dependencies=[Depends(verify_api_key)])
async def dim_ic(
    window: int = Query(5, description="未来收益窗口 (5/20 日)"),
    start: str = Query("2024-01-01", description="起始日期"),
    end: str = Query("2024-12-31", description="结束日期"),
    stock_limit: int = Query(30, description="股票数"),
):
    """7 维评分维度对收益的 IC 贡献

    算每个 scorer.score() 输出的 weighted 分 vs future_return 的 IC
    """
    from src.scoring import ScorerRegistry

    engine = get_engine()
    # 拉数据 (含 future_return)
    sql = text("""
        WITH stocks AS (
          SELECT DISTINCT code FROM daily_price
          WHERE trade_date BETWEEN :start AND :end
          ORDER BY code LIMIT :limit
        )
        SELECT
          d.code, d.trade_date, d.close AS today_close,
          (SELECT close FROM daily_price d2
           WHERE d2.code = d.code AND d2.trade_date > d.trade_date
           ORDER BY d2.trade_date ASC LIMIT 1 OFFSET :window) AS future_close
        FROM daily_price d
        INNER JOIN stocks s USING (code)
        WHERE d.trade_date BETWEEN :start AND :end
    """)

    df = pd.read_sql(sql, engine, params={
        "start": start, "end": end, "limit": stock_limit, "window": window - 1,
    })
    if df.empty:
        return {"dims": [], "window": window}

    df = df.dropna(subset=["today_close", "future_close"])
    df = df[df["today_close"] > 0]
    df["future_return"] = df["future_close"] / df["today_close"] - 1
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")

    dims = []
    for dim_name, cls_name in DIM_NAMES:
        try:
            scorer = ScorerRegistry.get(dim_name)
            scores = []
            for code in df["code"].unique()[:stock_limit]:
                # 取 as_of_date = 该股的最近 trade_date
                sub = df[df["code"] == code].sort_values("trade_date", ascending=False)
                if sub.empty:
                    continue
                as_of = sub.iloc[0]["trade_date"]
                try:
                    r = scorer.score(code, as_of)
                    weighted = r.get("weighted", 0.0)
                    scores.append({"code": code, "trade_date": as_of, "score": weighted})
                except Exception:
                    continue
            if not scores:
                dims.append({"name": dim_name, "error": "no scores"})
                continue
            sdf = pd.DataFrame(scores)
            merged = df.merge(sdf, on=["code", "trade_date"], how="inner")
            if len(merged) < 5:
                dims.append({"name": dim_name, "error": "insufficient merged data"})
                continue
            ic = _spearman_ic(merged["score"].values, merged["future_return"].values)
            dims.append({
                "name": dim_name,
                "class": cls_name,
                "mean_ic": round(ic, 4) if ic is not None else None,
                "n": int(len(merged)),
            })
        except Exception as e:
            logger.warning(f"Dim-IC {dim_name} 失败: {e}")
            dims.append({"name": dim_name, "error": str(e)})

    return {"dims": dims, "window": window, "start": start, "end": end}


__all__ = ["router"]
