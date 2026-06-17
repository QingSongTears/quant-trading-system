"""
V6 + 六维评分管线 混合策略
============================
v6信号产生候选池 → 多维评分精选 → 综合排名

当前可用维度:
  - technical: ✅ (技术面, quant.db)
  - fundamental: ❌ (需补roe/eps列)
  - fund_flow: ❌ (需fund_flow表)
  
先使用技术面做概念验证, 后续扩展更多维度。
"""
from typing import List, Optional
from datetime import date

import numpy as np
import pandas as pd

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

    name: str = "v6_pipeline_hybrid"
    description: str = "V6超卖信号 × 多维评分融合精选"
    source: str = "v6_reversal + ScorerRegistry 多维评分管线"

    # 融合权重
    V6_WEIGHT: float = 0.6
    PIPELINE_WEIGHT: float = 0.4

    # 启用的评分维度
    scoring_dims: List[str] = ["technical"]

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

    def _ensure_scorers(self):
        """懒加载评分器实例"""
        if self._scorers is not None:
            return
        self._scorers = {}
        for dim in self.scoring_dims:
            try:
                self._scorers[dim] = ScorerRegistry.get(dim, engine=self.engine)
            except Exception as e:
                print(f"  [WARN] 评分器 {dim} 加载失败: {e}")

    def _financial_filter(self, candidates: List[dict]) -> List[dict]:
        """
        财务质量过滤: 排除净利润为负或PE极端异常的股票
        使用 finance_snapshot_v2 表
        """
        if not candidates:
            return candidates

        codes = [c["code"] for c in candidates]
        codes_str = ",".join([f"'{c}'" for c in codes])

        try:
            query = f"""
                SELECT code, net_profit, pe_ttm
                FROM finance_snapshot_v2
                WHERE code IN ({codes_str})
            """
            df = pd.read_sql(query, self.engine)

            if df.empty:
                return candidates  # 无财务数据：不过滤

            # 构建过滤字典
            valid_codes = set()
            for _, row in df.iterrows():
                code = str(row["code"]).strip().zfill(6)
                net_profit = row.get("net_profit")
                pe = row.get("pe_ttm")

                # 过滤条件: net_profit > 0
                # PE检查仅当值在合理范围(0~1000)内才生效 (部分数据PE存的是市值)
                if net_profit is not None and net_profit > 0:
                    if pe is None:
                        valid_codes.add(code)
                    elif pe > 0 and pe < 1000:
                        valid_codes.add(code)  # PE合理: 通过
                    elif pe >= 1000:
                        valid_codes.add(code)  # PE值异常大(可能是市值): 跳过PE检查, 通过
                    # pe <= 0: 不通过

            return [c for c in candidates if c["code"] in valid_codes]

        except Exception:
            return candidates  # DB查询失败：不过滤

    def select(self, rebalance_date, universe_df: pd.DataFrame) -> List[str]:
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

        # 归一化 v6 分数到 0-20 (与 weighted 一致)
        v6_scores = np.array([c["score"] for c in v6_candidates[:16]])
        if v6_scores.max() > v6_scores.min():
            v6_normalized = (v6_scores - v6_scores.min()) / (v6_scores.max() - v6_scores.min()) * 20
        else:
            v6_normalized = np.full_like(v6_scores, 10.0)

        # 构建 v6 分数字典
        v6_score_map = {code: round(s, 2) for code, s in zip(candidate_codes, v6_normalized)}

        # 运行各维度评分器
        dim_scores = {}
        for dim_name in self.scoring_dims:
            scorer = self._scorers.get(dim_name)
            if scorer is None:
                continue
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
            except Exception as e:
                pass  # 评分器失败：静默跳过

        # Step 4: 融合评分
        final_scores = {}
        for code in candidate_codes:
            v6_s = v6_score_map.get(code, 0)
            pipe_s = 0.0
            dim_count = 0
            for dim_name in self.scoring_dims:
                if dim_name in dim_scores and code in dim_scores[dim_name]:
                    pipe_s += dim_scores[dim_name][code]
                    dim_count += 1
            if dim_count > 0:
                pipe_s /= dim_count  # 等权平均各维度

            final_scores[code] = v6_s * self.V6_WEIGHT + pipe_s * self.PIPELINE_WEIGHT

        # 按融合分排序
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        return [code for code, _ in ranked[:self.n_stocks]]
