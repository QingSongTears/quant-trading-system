"""
V龙头 特征工程模块 — vnpy.alpha.dataset 角色
==========================================

设计目标:
  - 与 scripts/train_xgb_v4.py 训练时特征工程**完全一致** (确保在线/训练分布一致)
  - 用 ScorerRegistry.batch_score() 替代原 data/all_7d_scores.json 快照
  - 提供批量接口 (build_batch_matrix) 供策略类一次拿到 N 只股票的特征矩阵
  - 多级缓存: dim_scores / tech_indicators / industry, 减少 DB 压力
  - 缺失数据**安全降级** (返回默认值, 不抛异常) — 实战中很多数据不全

特征 schema (与 train_xgb_v4.py 一致, 共 74 维):
  1. 8 维评分 raw          tech/fundam/fund/institutional/lh_institutional/
                           sentiment/news_event/chip (each _weighted)
  2. 8 维评分 截面 pct     ..._pct
  3. 1 维 综合 pct         avg_score_pct
  4. 8 维 滞后 1m          ..._lag1m
  5. 8 维 动量 (本月-上月) ..._delta
  6. 5 维 技术指标         macd_hist, rsi14, kdj_k, kdj_j, boll_pos
  7. 3 维 交叉特征         score_x_rsi, score_x_macd, score_x_kdj
  8. 33 维 行业 one-hot    ind_<industry_name>

依赖:
  - quant.db.technical_indicators (MACD/RSI/KDJ/Bollinger)
  - quant.db.stock_profile.industry (优先) / stock_basic.industry (兜底)
  - 8 个 Scorer 的 DB 表 (chip_distribution / fund_flow_data / 等)

待重构:
  - scripts/param_server.py:_predict_with_xgb 是本模块的旧版 (基于快照 JSON)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

# ── 特征 schema 常量 (与 train_xgb_v4.py 严格一致) ──

# 8 维评分列名 (训练时 dim_cols 顺序)
DIM_COLS: list[str] = [
    "tech_weighted",
    "fundam_weighted",
    "fund_weighted",
    "institutional_weighted",
    "lh_institutional_weighted",
    "sentiment_weighted",
    "news_event_weighted",
    "chip_weighted",
]

# 8 维评分 → ScorerRegistry name 映射
# 注意:fundam_weighted 列名对应 fundamental scorer,fund_weighted 对应 fund_flow scorer
DIM_TO_SCORER: dict[str, str] = {
    "tech_weighted": "technical",
    "fundam_weighted": "fundamental",
    "fund_weighted": "fund_flow",
    "institutional_weighted": "institutional",
    "lh_institutional_weighted": "lhb_institutional",
    "sentiment_weighted": "sentiment",
    "news_event_weighted": "news_event",
    "chip_weighted": "chip",
}

# 5 维技术指标 (与 train_xgb_v4.py 一致)
TECH_COLS: list[str] = ["macd_hist", "rsi14", "kdj_k", "kdj_j", "boll_pos"]
TECH_DEFAULTS: dict[str, float] = {
    "macd_hist": 0.0,
    "rsi14": 0.5,  # 归一化到 0-1
    "kdj_k": 0.5,
    "kdj_j": 0.5,
    "boll_pos": 0.5,
}

# 3 维交叉特征
CROSS_COLS: list[str] = ["score_x_rsi", "score_x_macd", "score_x_kdj"]

# 需要 as_of_date 参数的 scorer
SCORERS_WITH_DATE: set[str] = {
    "technical",
    "fund_flow",
    "institutional",
    "lhb_institutional",
    "sentiment",
    "news_event",
}

# 不需要日期的 scorer (fundamental/chip 只有 batch_score(codes))
SCORERS_NO_DATE: set[str] = {"fundamental", "chip"}


__all__ = [
    "DIM_COLS",
    "DIM_TO_SCORER",
    "TECH_COLS",
    "TECH_DEFAULTS",
    "CROSS_COLS",
    "FeatureBuilder",
    "pct_rank",
]


# ────────────────────────────────────────────────────────
#  工具函数
# ────────────────────────────────────────────────────────


def pct_rank(values: list[float], target: float) -> float:
    """
    百分位排名 (与 train_xgb_v4.py 公式一致)

    rank = (values < target 的个数) + 0.5 * (values == target 的个数)
    pct  = rank / max(len(values), 1)
    """
    if not values:
        return 0.5
    arr = np.asarray(values, dtype=float)
    target = float(target) if target is not None else 0.0
    lt = int(np.sum(arr < target))
    eq = int(np.sum(arr == target))
    return (lt + 0.5 * eq) / max(len(arr), 1)


def _month_end(as_of_date: str) -> str:
    """取 as_of_date 所在月份的最后一天 (YYYY-MM-DD)"""
    if isinstance(as_of_date, (date, datetime)):
        d = as_of_date
    else:
        d = datetime.strptime(str(as_of_date)[:10], "%Y-%m-%d")
    if d.month == 12:
        next_month = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_month = d.replace(month=d.month + 1, day=1)
    return (next_month - timedelta(days=1)).strftime("%Y-%m-%d")


def _prev_month(ym: str) -> Optional[str]:
    """上一月的 YYYY-MM 字符串 (用于滞后特征)"""
    try:
        y, m = map(int, ym.split("-")[:2])
        if m == 1:
            return f"{y - 1}-12"
        return f"{y}-{m - 1:02d}"
    except (ValueError, AttributeError):
        return None


# ────────────────────────────────────────────────────────
#  FeatureBuilder — 策略不感知细节的封装
# ────────────────────────────────────────────────────────


class FeatureBuilder:
    """
    V龙头 特征构建器 — 按 (code, ym) 缓存, 批量输出 np.ndarray

    用法:
        engine = get_engine()
        builder = FeatureBuilder(engine)
        X, valid_codes = builder.build_batch_matrix(
            codes=["000001", "000002", ...],
            ym="2026-05",
            as_of_date="2026-05-30",
            feature_order=model.feature_names,
        )
        proba = model.predict_proba(X)

    缓存策略:
        - dim_scores:   {(code, ym): {dim_col: weighted_value, ...}}
        - tech_features: {(code, date): {tech_col: value, ...}}
        - industry:     {code: industry_name}
    """

    def __init__(self, engine: Engine, scorer_names: Optional[list[str]] = None):
        """
        Args:
            engine: SQLAlchemy Engine
            scorer_names: 8 个 scorer name 列表, 默认 = DIM_TO_SCORER.values()
        """
        self.engine = engine
        self.scorer_names = scorer_names or list(DIM_TO_SCORER.values())
        self._dim_cache: dict[tuple[str, str], dict[str, float]] = {}
        self._tech_cache: dict[tuple[str, str], dict[str, float]] = {}
        self._industry_cache: dict[str, str] = {}

    # ── 行业 ──────────────────────────────────────
    def _load_industries(self, codes: list[str]) -> dict[str, str]:
        """批量查 industry (优先 stock_profile, 兜底 stock_basic)"""
        if not codes:
            return {}
        # 已缓存的跳过
        missing = [c for c in codes if c not in self._industry_cache]
        if not missing:
            return {c: self._industry_cache.get(c, "其他") for c in codes}

        result: dict[str, str] = {}
        # 1. 优先 stock_profile
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT code, industry FROM stock_profile "
                        "WHERE code IN :codes AND industry IS NOT NULL"
                    ),
                    {"codes": tuple(missing)},
                ).fetchall()
            for code, ind in rows:
                key = str(code).strip().zfill(6)
                if key and ind:
                    result[key] = ind
        except Exception:
            pass

        # 2. 兜底 stock_basic
        still_missing = [c for c in missing if c not in result]
        if still_missing:
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT code, industry FROM stock_basic "
                            "WHERE code IN :codes AND industry IS NOT NULL"
                        ),
                        {"codes": tuple(still_missing)},
                    ).fetchall()
                for code, ind in rows:
                    key = str(code).strip().zfill(6)
                    if key and ind and key not in result:
                        result[key] = ind
            except Exception:
                pass

        # 3. 其余填 "其他"
        for c in missing:
            key = str(c).strip().zfill(6)
            if key not in result:
                result[key] = "其他"

        self._industry_cache.update(result)
        return {c: self._industry_cache.get(str(c).strip().zfill(6), "其他") for c in codes}

    # ── 8 维评分 ──────────────────────────────────
    def _load_dim_scores(
        self, codes: list[str], ym: str, as_of_date: str,
    ) -> dict[str, dict[str, float]]:
        """
        用 ScorerRegistry 取 8 维 weighted 评分

        Returns:
            {(code, ym): {dim_col: weighted_value}}
        """
        result: dict[tuple[str, str], dict[str, float]] = {}
        for code in codes:
            key = (code, ym)
            if key in self._dim_cache:
                result[key] = self._dim_cache[key]
                continue

            row: dict[str, float] = {}
            for dim_col, scorer_name in DIM_TO_SCORER.items():
                if scorer_name not in self.scorer_names:
                    row[dim_col] = 0.0
                    continue
                try:
                    from src.scoring import ScorerRegistry
                    scorer = ScorerRegistry.get(
                        scorer_name, engine=self.engine, use_cache=True,
                    )
                    val = self._score_single(
                        scorer, scorer_name, code, as_of_date,
                    )
                    row[dim_col] = float(val) if val is not None else 0.0
                except Exception:
                    row[dim_col] = 0.0

            self._dim_cache[key] = row
            result[key] = row
        return result

    @staticmethod
    def _score_single(
        scorer: Any, scorer_name: str,
        code: str, as_of_date: str,
    ) -> Optional[float]:
        """单只股票 → weighted 分数 (兼容不同 scorer 的签名差异)"""
        try:
            if scorer_name in SCORERS_NO_DATE:
                # fundamental / chip: batch_score(codes) 无日期
                # 但单只 score(code) 是有的
                r = scorer.score(code)
            else:
                r = scorer.score(code, as_of_date)
            if not r or r.get("error"):
                return None
            return r.get("weighted")
        except Exception:
            return None

    # ── 技术指标 ──────────────────────────────────
    def _load_tech_features(
        self, code: str, as_of_date: str,
    ) -> dict[str, float]:
        """从 technical_indicators 取 5 维技术指标 (找不到回溯 9 天)"""
        key = (code, as_of_date[:10])
        if key in self._tech_cache:
            return self._tech_cache[key]

        target_date = as_of_date[:10]
        # 尝试精确匹配 + 向前回溯 9 天 (与 train_xgb_v4.py 一致)
        for offset in range(10):
            try:
                d = datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=offset)
                d_str = d.strftime("%Y-%m-%d")
                row = self._query_tech_indicators(code, d_str)
                if row is not None:
                    result = {
                        "macd_hist": row.get("macd_hist") or 0.0,
                        # 训练时 rsi/kdj 归一化到 0-1
                        "rsi14": (row.get("rsi14") or 50.0) / 100.0,
                        "kdj_k": (row.get("kdj_k") or 50.0) / 100.0,
                        "kdj_j": (row.get("kdj_j") or 50.0) / 100.0,
                        "boll_pos": 0.5,  # 无 close, 默认中位
                    }
                    self._tech_cache[key] = result
                    return result
            except (ValueError, TypeError):
                continue

        # 完全查不到 — 用默认值
        self._tech_cache[key] = dict(TECH_DEFAULTS)
        return dict(TECH_DEFAULTS)

    def _query_tech_indicators(self, code: str, trade_date: str) -> Optional[dict]:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT macd_hist, rsi14, kdj_k, kdj_j "
                        "FROM technical_indicators "
                        "WHERE code = :code AND trade_date = :d"
                    ),
                    {"code": code, "d": trade_date},
                ).fetchone()
            if row is None:
                return None
            return {
                "macd_hist": row[0],
                "rsi14": row[1],
                "kdj_k": row[2],
                "kdj_j": row[3],
            }
        except Exception:
            return None

    def _load_tech_features_bulk(
        self, codes: list[str], as_of_date: str,
    ) -> dict[str, dict[str, float]]:
        """批量取 (多只股票共用一次 query)"""
        result: dict[str, dict[str, float]] = {}
        uncached = [c for c in codes if (c, as_of_date[:10]) not in self._tech_cache]
        if uncached:
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT code, trade_date, macd_hist, rsi14, kdj_k, kdj_j "
                            "FROM technical_indicators "
                            "WHERE code IN :codes AND trade_date <= :d "
                            "ORDER BY code, trade_date DESC"
                        ),
                        {"codes": tuple(uncached), "d": as_of_date[:10]},
                    ).fetchall()
                # 每只股票取最新一条
                latest_per_code: dict[str, Any] = {}
                for code, d, mh, rsi, k, j in rows:
                    key = str(code).strip().zfill(6)
                    if key not in latest_per_code:
                        latest_per_code[key] = {
                            "macd_hist": mh or 0.0,
                            "rsi14": (rsi or 50.0) / 100.0,
                            "kdj_k": (k or 50.0) / 100.0,
                            "kdj_j": (j or 50.0) / 100.0,
                            "boll_pos": 0.5,
                        }
                for c in uncached:
                    key = c
                    feats = latest_per_code.get(key, dict(TECH_DEFAULTS))
                    self._tech_cache[(key, as_of_date[:10])] = feats
            except Exception:
                for c in uncached:
                    self._tech_cache[(c, as_of_date[:10])] = dict(TECH_DEFAULTS)

        for c in codes:
            result[c] = self._tech_cache.get(
                (c, as_of_date[:10]), dict(TECH_DEFAULTS),
            )
        return result

    # ── 滞后 1m ───────────────────────────────────
    def _load_lag_scores(
        self, codes: list[str], ym: str,
    ) -> dict[str, dict[str, float]]:
        """上月同 ym 评分 (无上月数据 → 填 0)"""
        prev_ym = _prev_month(ym)
        if not prev_ym:
            return {c: {d: 0.0 for d in DIM_COLS} for c in codes}

        # 上月月末日期
        try:
            y, m = map(int, prev_ym.split("-")[:2])
            prev_end = _month_end(f"{y}-{m:02d}-28")
        except Exception:
            prev_end = None

        result: dict[str, dict[str, float]] = {}
        for c in codes:
            key = (c, prev_ym)
            if key in self._dim_cache:
                result[c] = self._dim_cache[key]
                continue
            # 重新查上月 — 走同一套 scorer
            row: dict[str, float] = {}
            for dim_col, scorer_name in DIM_TO_SCORER.items():
                if scorer_name not in self.scorer_names:
                    row[dim_col] = 0.0
                    continue
                try:
                    from src.scoring import ScorerRegistry
                    scorer = ScorerRegistry.get(
                        scorer_name, engine=self.engine, use_cache=True,
                    )
                    val = self._score_single(
                        scorer, scorer_name, c, prev_end or prev_ym + "-28",
                    )
                    row[dim_col] = float(val) if val is not None else 0.0
                except Exception:
                    row[dim_col] = 0.0
            self._dim_cache[key] = row
            result[c] = row
        return result

    # ── 批量构建特征矩阵 ──────────────────────────
    def build_batch_matrix(
        self,
        codes: list[str],
        ym: str,
        as_of_date: str,
        feature_order: list[str],
        industry_columns: Optional[list[str]] = None,
    ) -> tuple[np.ndarray, list[str]]:
        """
        批量: N 只股票 → (X, valid_codes)

        Args:
            codes: 候选股票代码列表
            ym: 形如 "2026-05" 的月份
            as_of_date: 形如 "2026-05-30" 的具体交易日
            feature_order: 训练时的 feature_names 顺序 (X 列序必须一致)
            industry_columns: ind_* 列名列表 (默认从 feature_order 提取)

        Returns:
            X: (n, n_features) float32 ndarray
            valid_codes: 与 X 行一一对应, 数据缺失的 code 会被剔除
        """
        if not codes:
            return np.zeros((0, len(feature_order)), dtype=np.float32), []

        if industry_columns is None:
            industry_columns = [f for f in feature_order if f.startswith("ind_")]

        # 1. dim 评分 (本月)
        month_scores = self._load_dim_scores(codes, ym, as_of_date)
        # 2. dim 评分 (上月, 滞后)
        lag_scores = self._load_lag_scores(codes, ym)
        # 3. 技术指标 (批量)
        tech_features = self._load_tech_features_bulk(codes, as_of_date)
        # 4. 行业 (批量)
        industries = self._load_industries(codes)

        # 5. 截面百分位 — 在当月所有 codes 上计算
        pct_table: dict[str, dict[str, float]] = {c: {} for c in codes}
        avg_scores: list[float] = []
        for c in codes:
            ms = month_scores.get((c, ym), {})
            if ms:
                avg_scores.append(sum(ms.get(d, 0.0) for d in DIM_COLS) / len(DIM_COLS))
            else:
                avg_scores.append(0.0)

        for dim in DIM_COLS:
            vals = [month_scores.get((c, ym), {}).get(dim, 0.0) for c in codes]
            for i, c in enumerate(codes):
                pct_table[c][dim + "_pct"] = pct_rank(vals, vals[i])

        # 综合 avg_score_pct
        for i, c in enumerate(codes):
            pct_table[c]["avg_score_pct"] = pct_rank(avg_scores, avg_scores[i])

        # 6. 拼装特征矩阵
        X_list: list[list[float]] = []
        valid_codes: list[str] = []
        for c in codes:
            ms = month_scores.get((c, ym), {})
            ls = lag_scores.get(c, {})
            tf = tech_features.get(c, dict(TECH_DEFAULTS))
            ind = industries.get(c, "其他")

            if not ms:
                # 完全没有当月评分数据 — 跳过
                continue

            row: list[float] = []
            for fname in feature_order:
                if fname in DIM_COLS:
                    row.append(float(ms.get(fname, 0.0)))
                elif fname.endswith("_pct") and fname.replace("_pct", "") in DIM_COLS:
                    base = fname.replace("_pct", "")
                    row.append(float(pct_table[c].get(base + "_pct", 0.0)))
                elif fname == "avg_score_pct":
                    row.append(float(pct_table[c].get("avg_score_pct", 0.0)))
                elif fname.endswith("_lag1m"):
                    base = fname.replace("_lag1m", "")
                    row.append(float(ls.get(base, 0.0)))
                elif fname.endswith("_delta"):
                    base = fname.replace("_delta", "")
                    cur = float(ms.get(base, 0.0))
                    lag = float(ls.get(base, 0.0))
                    row.append(cur - lag)
                elif fname in TECH_COLS:
                    row.append(float(tf.get(fname, TECH_DEFAULTS.get(fname, 0.0))))
                elif fname in CROSS_COLS:
                    avg = sum(ms.get(d, 0.0) for d in DIM_COLS) / len(DIM_COLS)
                    if fname == "score_x_rsi":
                        row.append(avg * (float(tf.get("rsi14", 0.5)) - 0.5))
                    elif fname == "score_x_macd":
                        row.append(avg * float(tf.get("macd_hist", 0.0)))
                    elif fname == "score_x_kdj":
                        row.append(avg * (float(tf.get("kdj_k", 0.5)) - 0.5))
                    else:
                        row.append(0.0)
                elif fname.startswith("ind_"):
                    ind_name = fname[4:]  # 去掉 "ind_" 前缀
                    row.append(1.0 if ind == ind_name else 0.0)
                else:
                    # 未知列名 — 填 0
                    row.append(0.0)

            X_list.append(row)
            valid_codes.append(c)

        if not X_list:
            return np.zeros((0, len(feature_order)), dtype=np.float32), []

        X = np.asarray(X_list, dtype=np.float32)
        return X, valid_codes

    # ── 单只特征 (调试/可视化用) ─────────────────
    def build_feature_row(
        self,
        code: str,
        ym: str,
        as_of_date: str,
        feature_order: list[str],
        industry_columns: Optional[list[str]] = None,
    ) -> Optional[dict[str, float]]:
        """单只股票 → 特征 dict (index=feature_order)"""
        X, valid = self.build_batch_matrix(
            [code], ym, as_of_date, feature_order, industry_columns,
        )
        if len(valid) == 0:
            return None
        return dict(zip(feature_order, X[0].tolist()))
