"""
V6 + 六维评分管线 混合策略
============================
v6信号产生候选池 → 多维评分精选 → 综合排名

当前可用维度:
  - technical: ✅ (技术面, quant.db)
  - fundamental: ❌ (需补roe/eps列)
  - fund_flow: ✅ (v2.1版本, fund_flow表)
  - chip: ✅ (筹码面, chip_distribution表)

v2.2 改进 (2026-06-19):
  - scoring_dims 默认开启 fund_flow + chip
  - 表缺失时优雅降级到可用维度
  - 融合权重可调, 适配维度增减

ADR-0010 (2026-06-27): 财务过滤改走 datafeed.get_finance_snapshot
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.scoring import ScorerRegistry


class V6PipelineHybridStrategy(V6ReversalSelectionStrategy):
    """
    V6 + 多维评分融合策略

    流程:
    1. v6 检测超卖反转信号 → 候选池 (≤8只)
    2. 对候选池运行启用评分器
    3. 融合评分: combined = v6_score × w_v6 + Σ(scorer_weighted × w_dim)
    4. 按融合分排序 → 返回 top N
    """

    name: str = "V6多维融合"
    description: str = "V6超卖信号 × 多维评分融合精选"
    source: str = "v6_reversal + ScorerRegistry 多维评分管线"

    # 融合权重
    V6_WEIGHT: float = 0.6
    PIPELINE_WEIGHT: float = 0.4

    # 启用的评分维度: 资金面+筹码面 (表缺失时优雅降级)
    scoring_dims: list[str] = ["technical", "fund_flow", "chip"]

    # 表存在性缓存: 避免每次 select 都查 SQLite
    _available_tables_cache: dict = {}

    def __init__(self, **kwargs):
        # 提取scoring相关参数
        scoring_dims = kwargs.pop("scoring_dims", None)
        v6_weight = kwargs.pop("v6_weight", None)
        pipeline_weight = kwargs.pop("pipeline_weight", None)

        super().__init__(**kwargs)

        if scoring_dims is not None:
            self.scoring_dims = scoring_dims
        if v6_weight is not None:
            self.V6_WEIGHT = v6_weight
        if pipeline_weight is not None:
            self.PIPELINE_WEIGHT = pipeline_weight

        # 懒加载评分器
        self._scorers = None

        # 检测和缓存可用表对应的评分维度 (graceful fallback)
        self._available_dims = self._detect_available_dims()

    def _detect_available_dims(self) -> list[str]:
        """
        检测当前数据库中真实存在的评分维度对应表。
        缓存到类属性中, 多策略实例不需重复查询。

        映射:
          technical     -> daily_price 表            (要求价格数据)
          fundamental   -> finance_snapshot_v2 表    (ROE/PE)
          fund_flow     -> fund_flow 表              (资金流)
          news_event    -> news_event_data 表        (新闻)
          sentiment     -> em_global_news 表         (情绪)
          institutional -> dragon_tiger_data 表      (机构)
          chip          -> chip_distribution 表      (筹码)
        """
        cache_key = id(self.engine)
        if cache_key in V6PipelineHybridStrategy._available_tables_cache:
            return V6PipelineHybridStrategy._available_tables_cache[cache_key]

        required_tables = {
            "technical": "daily_price",
            "fundamental": "finance_snapshot_v2",
            "fund_flow": "fund_flow",
            "news_event": "news_event_data",
            "sentiment": "em_global_news",
            "institutional": "dragon_tiger_data",
            "chip": "chip_distribution",
        }

        available = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                existing = {r[0] for r in rows}

            for dim, table in required_tables.items():
                if table in existing:
                    available.append(dim)
        except Exception:
            pass

        V6PipelineHybridStrategy._available_tables_cache[cache_key] = available
        return available

    def _ensure_scorers(self):
        """懒加载评分器实例"""
        if self._scorers is not None:
            return
        self._scorers = {}

        # 只运行真实可用的维度 (graceful fallback)
        active_dims = [d for d in self.scoring_dims if d in self._available_dims]
        if not active_dims:
            print("  [WARN] 没有可用评分维度, v6 单机运行")
            return

        skipped = [d for d in self.scoring_dims if d not in self._available_dims]
        if skipped:
            print(f"  [WARN] 跳过缺少数据的评分维度: {skipped}")

        for dim in active_dims:
            try:
                self._scorers[dim] = ScorerRegistry.get(dim, engine=self.engine)
            except Exception as e:
                print(f"  [WARN] 评分器 {dim} 加载失败: {e}")

    def _financial_filter(self, candidates: list[dict]) -> list[dict]:
        """
        财务质量过滤: 排除净利润为负或PE极端异常的股票

        ADR-0010 (2026-06-27): 改走 datafeed.get_finance_snapshot() (替代 read_sql)
        PE = TotalShareholderEquity / |NPParentCompanyOwnersTTM| 近似估算
        """
        if not candidates:
            return candidates

        codes = [c["code"] for c in candidates]

        try:
            fin = self.datafeed.get_finance_snapshot(list(codes))
            if not fin:
                return candidates  # 无财务数据：不过滤

            # 构建过滤字典
            valid_codes = set()
            for code, fields in fin.items():
                code_norm = str(code).strip().zfill(6)
                net_profit = fields.get("net_profit")
                pe = fields.get("pe_ttm")

                # 过滤条件: net_profit > 0
                # PE检查仅当值在合理范围(0~1000)内才生效 (部分数据PE存的是市值)
                if net_profit is not None and net_profit > 0:
                    if pe is None:
                        valid_codes.add(code_norm)
                    elif pe > 0 and pe < 1000:
                        valid_codes.add(code_norm)  # PE合理: 通过
                    elif pe >= 1000:
                        valid_codes.add(code_norm)  # PE值异常大(可能是市值): 跳过PE检查, 通过
                    # pe <= 0: 不通过

            return [c for c in candidates if c["code"] in valid_codes]

        except Exception:
            return candidates  # datafeed 查询失败：不过滤

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> list[str]:
        """
        v6信号 + 多维评分融合选股
        """
        if universe_df.empty:
            return []

        date_str = str(rebalance_date.date()) if hasattr(rebalance_date, 'date') else str(rebalance_date)[:10]

        # Step 1: 从缓存获取v6指标
        indicators_df = self._indicator_cache.get(date_str)
        if indicators_df is None:
            return []

        # 只保留 universe 中的股票
        universe_codes = set(universe_df["code"].tolist())
        mask = indicators_df.index.isin(universe_codes)
        indicators_df = indicators_df[mask].reset_index()
        if indicators_df.empty:
            return []

        # Step 2: v6 信号检测 → 候选池 (含 v6 评分)
        v6_candidates = self._detect_signals(indicators_df, universe_df)
        if not v6_candidates:
            return []

        # Step 2.5: 财务质量过滤 (net_profit > 0, PE合理)
        v6_candidates = self._financial_filter(v6_candidates)

        if not v6_candidates:
            return []

        # 按 v6 评分排序
        v6_candidates.sort(key=lambda x: x["score"], reverse=True)

        # 如果没有启用任何评分维度，直接返回 v6 结果
        if not self.scoring_dims:
            return [c["code"] for c in v6_candidates[:self.n_stocks]]

        # Step 3: 多维评分
        candidate_codes = [c["code"] for c in v6_candidates[:16]]  # 最多16只进入评分
        if not candidate_codes:
            return [c["code"] for c in v6_candidates[:self.n_stocks]]

        self._ensure_scorers()

        # 如果评分器全部加载失败 (含 fund_flow 表缺失的 fallback), 退化为纯 v6
        if not self._scorers:
            return [c["code"] for c in v6_candidates[:self.n_stocks]]

        # 归一化 v6 分数到 0-20 (与 weighted 一致)
        v6_scores = np.array([c["score"] for c in v6_candidates[:16]])
        if v6_scores.max() > v6_scores.min():
            v6_normalized = (v6_scores - v6_scores.min()) / (v6_scores.max() - v6_scores.min()) * 20
        else:
            v6_normalized = np.full_like(v6_scores, 10.0)

        # 构建 v6 分数字典
        v6_score_map = {code: round(s, 2) for code, s in zip(candidate_codes, v6_normalized)}

        # 运行实际加载到的维度 (跳过缺失的 fund_flow 等)
        dim_scores = {}
        for dim_name in self._scorers.keys():
            scorer = self._scorers[dim_name]
            try:
                if dim_name == "fundamental":
                    batch_df = scorer.batch_score(candidate_codes)
                else:
                    batch_df = scorer.batch_score(candidate_codes, date_str)

                if not batch_df.empty and "weighted" in batch_df.columns:
                    dim_scores[dim_name] = dict(zip(
                        batch_df["code"].astype(str),
                        batch_df["weighted"]
                    ))
            except Exception:
                pass  # 评分器失败: 静默跳过

        # Step 4: 融合评分 (只用实际加载到的维度)
        final_scores = {}
        for code in candidate_codes:
            v6_s = v6_score_map.get(code, 0)
            pipe_s = 0.0
            dim_count = 0
            for dim_name in self._scorers.keys():
                if dim_name in dim_scores and code in dim_scores[dim_name]:
                    pipe_s += dim_scores[dim_name][code]
                    dim_count += 1
            if dim_count > 0:
                pipe_s /= dim_count  # 等权平均各维度

            final_scores[code] = v6_s * self.V6_WEIGHT + pipe_s * self.PIPELINE_WEIGHT

        # 按融合分排序
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        return [code for code, _ in ranked[:self.n_stocks]]


# ── 业务名别名 (LIVE_TRADING_ROADMAP.md 命名规范化) ─────────
# 兼容旧引用: V6MultiDimStrategy = V6PipelineHybridStrategy
V6MultiDimStrategy = V6PipelineHybridStrategy
