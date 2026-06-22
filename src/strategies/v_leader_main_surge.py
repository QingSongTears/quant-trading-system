"""
V龙头 主升浪选股策略 — 月度高胜率候选池
====================================

基于近 2 年 A 股强势票样本，每日日 K 提取多维特征，
训练 XGBoost 模型预测未来 60 日大涨概率，输出月度调仓候选池。

设计思路：
  - 样本：近 2 年涨幅前列的强势票（涨幅 > 60% 或 ret_60d > 30%）
  - 特征：8 维评分 + 技术指标 + 滞后特征 + 评分动量
  - 标签：ret_60d > 10% (识别主升浪)
  - 划分：时间序列（18 个月训练 → 6 个月验证 → 滚动）
  - 模型：XGBoost v4 (AUC 0.7912, 见 scripts/train_xgb_v4.py)

调度流程（月度）：
  T0 (每月初): 加载当月全市场 → 提取特征 → XGBoost 预测 → top 30 → 候选池
  T1+ (每日) : 候选池内叠加 V6 反转信号 → 实际入场

⚠️ 状态：骨架阶段（XGBoost v4 模型已有，候选池逻辑待实现）

TODO(实盘前必做):
  - [ ] 实现 _extract_features() (从 8 维评分 + 技术指标拼接)
  - [ ] 实现 _predict() (加载 xgb_v4_*.pkl, 跑预测)
  - [ ] 实现 _build_pool() (top N + 流动性过滤 + 行业分散)
  - [ ] OOS 验证 (Phase 9, walk_forward.py)
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from ..backtest.base_selection_strategy import BaseSelectionStrategy


class VLeaderMainSurgeStrategy(BaseSelectionStrategy):
    """
    V龙头 主升浪 — 月度高胜率候选池

    信号来源：XGBoost v4 (scripts/train_xgb_v4.py)
    调仓周期：月度 (rebalance_days=20 约 1 个月)
    输出数量：top 30 (候选池，叠加 V6 触发实际入场)
    """

    name: str = "V龙头主升"
    description: str = (
        "V龙头 主升浪 — 8维评分 + 技术指标 + 滞后特征 → "
        "XGBoost v4 月度预测 → 高胜率候选池"
    )
    source: str = (
        "近 2 年强势票样本 + XGBoost v4 (AUC 0.7912) + "
        "Fama-French(1993) + Jegadeesh(1990)"
    )

    # ── 选股参数 ──
    n_stocks: int = 30              # 候选池大小
    rebalance_days: int = 20         # 月度调仓
    lookback_days: int = 60          # 特征提取回看

    # ── 流动性过滤 ──
    min_amount_wan: float = 3000.0   # 最低日均成交 3000 万
    exclude_st: bool = True

    # ── 行业暴露控制 (TODO T3.2) ──
    max_sector_pct: float = 0.30     # 单行业不超过 30%

    # ── 模型配置 ──
    ml_model_path: str = "models/xgb_v4_2026_06.pkl"
    feature_window_days: int = 250   # 训练窗口（交易日）

    # ── 强势票样本定义 ──
    bull_definition: dict = None
    """
    强势票定义（用于训练样本标注）:
      - ret_60d > 30%  (60 日涨幅 > 30%)
      - 或 涨幅排名前 5%
      - 或 连板数 ≥ 3 (待实现)
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ml_model = None        # 懒加载 XGBoost 模型
        self._feature_pipeline = None  # 懒加载 StandardScaler

    # ================================================================
    #  样本构建 (训练用, 见 scripts/train_xgb_v4.py)
    # ================================================================

    @staticmethod
    def label_bull_stocks(
        df: pd.DataFrame,
        ret_col: str = "ret_60d",
        threshold: float = 10.0,
    ) -> pd.Series:
        """
        标注强势票标签：ret_60d > threshold

        Args:
            df: 包含 ret_col 列的 DataFrame
            ret_col: 收益列名
            threshold: 涨幅阈值 (%)

        Returns:
            Series[bool], True = 强势票
        """
        return (df[ret_col] > threshold).astype(int)

    # ================================================================
    #  特征提取 (预测用)
    # ================================================================

    def _extract_features(
        self, code: str, as_of_date: str,
    ) -> Optional[pd.Series]:
        """
        提取单只股票的多维特征向量。

        特征维度:
          1. 8 维评分 (tech/fundamental/flow/institutional/chip/sentiment/news/lhb)
          2. 技术指标 (MACD/RSI/KDJ/布林) from technical_indicators
          3. 滞后特征 (上月 8 维评分)
          4. 评分动量 (3 个月评分变化)

        Returns:
            pd.Series 索引为特征名, 或 None (数据不足)
        """
        # TODO: 实现
        # 提示:
        #   - 8 维评分走 ScorerRegistry.batch_score()
        #   - 技术指标查 technical_indicators 表
        #   - 滞后特征查上月 stock_score 快照表
        raise NotImplementedError(
            "V龙头 特征提取待实现 — 见 TODO 列表"
        )

    # ================================================================
    #  预测 (主入口)
    # ================================================================

    def _predict(
        self, codes: List[str], as_of_date: str,
    ) -> pd.DataFrame:
        """
        对候选股票运行 XGBoost 预测, 返回概率 + 排名

        Args:
            codes: 候选股票代码列表
            as_of_date: 基准日期

        Returns:
            DataFrame[code, predict_proba, rank]
        """
        # TODO: 实现
        # 1. 提取每只股票的特征
        # 2. 拼接成特征矩阵
        # 3. 加载 self._ml_model, 跑 predict_proba
        # 4. 返回排序后的 DataFrame
        raise NotImplementedError("V龙头 预测逻辑待实现")

    # ================================================================
    #  选股 (BaseSelectionStrategy 接口)
    # ================================================================

    def select(
        self, rebalance_date, universe_df: pd.DataFrame,
    ) -> List[str]:
        """
        月度选股 — 输出 V龙头 候选池 (top 30)

        流程:
          1. universe 流动性/ST 过滤 (BaseSelectionStrategy.filter_universe)
          2. 对每只股票提取特征
          3. XGBoost 预测 → predict_proba
          4. 行业分散约束 (单行业 ≤ 30%)
          5. 取 top n_stocks

        Returns:
            List[str], 候选股票代码列表
        """
        if universe_df.empty:
            return []

        date_str = (
            str(rebalance_date.date())
            if hasattr(rebalance_date, "date")
            else str(rebalance_date)[:10]
        )

        # Step 1: 流动性 + ST 过滤
        filtered = self.filter_universe(universe_df)
        if filtered.empty:
            return []

        # Step 2+3: 特征 + 预测
        try:
            predictions = self._predict(
                filtered["code"].tolist(), date_str,
            )
        except NotImplementedError:
            # 骨架阶段: 返回空候选池, 等待实现
            return []

        if predictions.empty:
            return []

        # Step 4: 行业分散 (TODO T3.2)
        # ... 待实现 ...

        # Step 5: top N
        return predictions.head(self.n_stocks)["code"].tolist()

    # ================================================================
    #  模型管理
    # ================================================================

    def _load_ml_model(self):
        """懒加载 XGBoost 模型 + Scaler"""
        if self._ml_model is not None:
            return

        # TODO: 实现模型加载
        # import joblib
        # self._ml_model = joblib.load(self.ml_model_path)
        # self._feature_pipeline = joblib.load(self.ml_model_path.replace(".pkl", "_scaler.pkl"))
        raise NotImplementedError("模型加载待实现")


# ── 业务名别名 (LIVE_TRADING_ROADMAP.md 命名规范化) ─────────
VLeaderStrategy = VLeaderMainSurgeStrategy