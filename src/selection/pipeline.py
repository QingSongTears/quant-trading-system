"""
选股管线 — 参数化多维度选股引擎
================================

独立模型架构：各评分器互不耦合，仅共享 quant.db。
通过参数控制启用哪些维度、过滤条件、输出数量。

用法:
    from src.selection import SelectionPipeline

    # 只用技术面
    pipe = SelectionPipeline(dimensions=["technical"])
    picks = pipe.run("2026-06-15", top_n=20)

    # 三因子：技术面 + 基本面 + 资金面
    pipe = SelectionPipeline(dimensions=["technical", "fundamental", "fund_flow"])
    picks = pipe.run("2026-06-15", top_n=30)

    # 六维全开
    pipe = SelectionPipeline()
    picks = pipe.run("2026-06-15", top_n=50)

    # DataFrame 输出
    df = pipe.run_to_dataframe("2026-06-15", top_n=30)
"""

from __future__ import annotations
from typing import Any
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import get_config
from ..db.engine import get_engine
from ..db.sql_utils import read_sql
from ..scoring import ScorerRegistry
# PR2.5: 委托给 scoring.combiner 单一实现
from ..scoring.combiner import combine as _combine_scores


@dataclass
class FilterConfig:
    """过滤参数配置"""
    min_mcap_yi: float = 20.0         # 最低市值(亿)
    min_amount_wan: float = 3000.0    # 最低日均成交额(万)
    max_mcap_yi: float = 0.0          # 最高市值(亿), 0=不限制
    exclude_st: bool = True            # 排除ST
    exclude_new_listed: bool = True    # 排除上市不满60日
    exclude_pe_negative: bool = True   # 排除PE为负
    exclude_sectors: list[str] = field(default_factory=list)  # 排除板块关键词


@dataclass
class DimensionWeight:
    """维度权重配置"""
    name: str
    weight: float


class SelectionPipeline:
    """
    参数化选股管线。

    流程: 加载全市场 → 过滤 → 评分 → 排名 → 输出

    每个评分器独立运行，互不依赖。
    """

    # 预定义权重方案
    PRESET_WEIGHTS = {
        "equal":   {"technical": 1.0, "fundamental": 1.0, "fund_flow": 1.0,
                     "news_event": 1.0, "sentiment": 1.0, "institutional": 1.0},
        "tech_only": {"technical": 1.0},
        "value":   {"technical": 0.6, "fundamental": 1.0, "fund_flow": 0.4},
        "growth":  {"technical": 0.8, "fundamental": 0.6, "fund_flow": 0.6,
                     "news_event": 0.4, "sentiment": 0.4},
    }

    def __init__(
        self,
        dimensions: list[str] | None = None,
        weights: dict[str, float] | None = None,
        weight_preset: str | None = None,
        filters: FilterConfig | None = None,
        engine=None,
        verbose: bool = True,
    ):
        """
        Args:
            dimensions: 启用的评分维度，默认全部6个
            weights: 自定义权重，不指定则用 equal 等权
            weight_preset: 预定义权重方案 (equal/tech_only/value/growth)
            filters: 过滤配置，默认 FilterConfig()
            engine: 数据库引擎
            verbose: 是否打印进度
        """
        if engine is None:
            self.engine = get_engine()
        else:
            self.engine = engine

        self.dimensions = dimensions or list(ScorerRegistry._registry.keys())
        self.filters = filters or FilterConfig()
        self.verbose = verbose

        # 权重解析
        if weights is not None:
            self.weights = weights
        elif weight_preset and weight_preset in self.PRESET_WEIGHTS:
            self.weights = self.PRESET_WEIGHTS[weight_preset]
        else:
            self.weights = self.PRESET_WEIGHTS["equal"]

        # 仅保留启用维度的权重
        self.weights = {k: v for k, v in self.weights.items() if k in self.dimensions}

        # 评分器实例 (延迟加载)
        self._scorers: dict[str, Any] = {}

    # ================================================================
    #  主入口
    # ================================================================

    def run(self, as_of_date: str, top_n: int = 30) -> pd.DataFrame:
        """
        执行选股。

        Args:
            as_of_date: 基准日期 "YYYY-MM-DD"
            top_n: 最终返回的股票数量

        Returns:
            DataFrame，按 combined_score 降序排列
        """
        # Step 1: 加载全市场
        universe = self._load_universe(as_of_date)
        if self.verbose:
            print(f"[Pipeline] 全市场: {len(universe)} 只")

        # Step 2: 过滤
        candidates = self._apply_filters(universe)
        if self.verbose:
            print(f"[Pipeline] 过滤后: {len(candidates)} 只")
        if candidates.empty:
            return pd.DataFrame()

        # Step 3: 评分
        codes = candidates["code"].tolist()
        scores_df = self._score_all(codes, as_of_date)
        if self.verbose:
            print(f"[Pipeline] 评分完成: {len(scores_df)} 只")

        # Step 4: 计算综合分
        result = self._combine_scores(scores_df)
        # 附加候选池信息 (仅合并存在的列)
        merge_cols = ["code", "name"]
        for mc in ["mcap_yi", "pe_ttm"]:
            if mc in candidates.columns:
                merge_cols.append(mc)
        result = result.merge(candidates[merge_cols], on="code", how="left")

        # Step 5: 排名
        result = result.sort_values("combined_score", ascending=False)
        result["rank"] = range(1, len(result) + 1)
        result = result.head(top_n)

        if self.verbose:
            print(f"[Pipeline] 最终推荐: {len(result)} 只")

        return result.reset_index(drop=True)

    def run_to_dataframe(self, as_of_date: str, top_n: int = 30) -> pd.DataFrame:
        """run() 的别名，明确返回 DataFrame"""
        return self.run(as_of_date, top_n)

    # ================================================================
    #  内部步骤
    # ================================================================

    def _load_universe(self, as_of_date: str) -> pd.DataFrame:
        """加载全市场股票 + 当日行情指标"""
        sql = """
            SELECT DISTINCT dp.code, sb.name, dp.close
            FROM daily_price dp
            JOIN stock_basic sb ON dp.code = sb.code
            WHERE dp.trade_date = :as_of
              AND dp.close > 0
        """
        df = read_sql(sql, self.engine, {"as_of": as_of_date})
        return df

    def _apply_filters(self, df: pd.DataFrame) -> pd.DataFrame:
        """应用过滤条件"""
        f = self.filters

        # ST 排除
        if f.exclude_st and "name" in df.columns:
            df = df[~df["name"].str.contains("ST|退|\\*ST", na=False, regex=True)]

        # PE 负排除 — 从 finance_summary 获取（替代已删除的 finance_snapshot_v2）
        if f.exclude_pe_negative:
            try:
                pe_data = read_sql(
                    "SELECT code FROM finance_summary WHERE NPParentCompanyOwnersTTM > 0",
                    self.engine,
                )
                if pe_data.empty:
                    # finance_summary 表为空时(尚未导入),跳过 PE 过滤避免误杀所有股票
                    if self.verbose:
                        print("[Pipeline] finance_summary 表为空,跳过 PE 负过滤")
                else:
                    pe_data["code"] = pe_data["code"].astype(str).str.zfill(6)
                    df = df[df["code"].isin(pe_data["code"])]
            except Exception:
                pass

        # 板块排除
        for kw in f.exclude_sectors:
            if "name" in df.columns:
                df = df[~df["name"].str.contains(kw, na=False)]

        # 预留: 市值/成交额过滤 (需要 tencent_quotes 或其他数据源)
        # 当前 kline_daily 不含市值数据，跳过

        return df

    def _score_all(self, codes: list[str], as_of_date: str) -> pd.DataFrame:
        """对所有候选股票运行启用的评分器"""
        self._load_scorers()

        all_scores = []

        for dim_name in self.dimensions:
            scorer = self._scorers.get(dim_name)
            if scorer is None:
                continue

            if self.verbose:
                print(f"  [{dim_name}] 评分中...")

            try:
                if dim_name == "fundamental":
                    batch_df = scorer.batch_score(codes)
                else:
                    batch_df = scorer.batch_score(codes, as_of_date)

                if not batch_df.empty:
                    batch_df["dimension"] = dim_name
                    all_scores.append(batch_df)
            except Exception as e:
                if self.verbose:
                    print(f"  [{dim_name}] 评分失败: {e}")

        if not all_scores:
            return pd.DataFrame(columns=["code"])

        return pd.concat(all_scores, ignore_index=True)

    def _combine_scores(self, scores_df: pd.DataFrame) -> pd.DataFrame:
        """计算加权综合分"""
        if scores_df.empty:
            return pd.DataFrame(columns=["code", "combined_score"])

        # 每个维度取 weighted 列 (0-20 scale)
        dim_scores = {}
        for dim_name in self.dimensions:
            dim_rows = scores_df[scores_df["dimension"] == dim_name]
            if not dim_rows.empty:
                dim_scores[dim_name] = dim_rows.set_index("code")["weighted"]

        if not dim_scores:
            return pd.DataFrame(columns=["code", "combined_score"])

        # 构建合并 DataFrame
        combined = pd.DataFrame(index=list(dim_scores.values())[0].index)
        for dim_name, series in dim_scores.items():
            combined[f"score_{dim_name}"] = series
            combined[f"score_{dim_name}"] = combined[f"score_{dim_name}"].fillna(0)

        # PR2.5: 加权综合 — 委托给 scoring.combiner (与 regenerate_combined_scores 一致)
        combined["combined_score"] = combined.apply(
            lambda row: _combine_scores(
                {dim: float(row[f"score_{dim}"]) for dim in self.weights
                 if f"score_{dim}" in combined.columns and pd.notna(row[f"score_{dim}"])},
                weights=self.weights,
                method="weighted_mean",
            ),
            axis=1,
        )

        combined = combined.reset_index().rename(columns={"index": "code"})
        return combined[["code", "combined_score"] +
                       [c for c in combined.columns if c.startswith("score_")]]

    def _load_scorers(self):
        """懒加载评分器实例"""
        for dim_name in self.dimensions:
            if dim_name not in self._scorers:
                self._scorers[dim_name] = ScorerRegistry.get(dim_name, engine=self.engine)

    def clear_cache(self):
        """清除评分器缓存"""
        self._scorers.clear()
        ScorerRegistry.clear_cache()
