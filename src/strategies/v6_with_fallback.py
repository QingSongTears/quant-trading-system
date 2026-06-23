"""
V6ReversalWithFallback — V6 + Fallback 包装类

业务名: V6超卖反转-兜底版
类名:   V6ReversalWithFallback

设计:
  - 继承 V6ReversalSelectionStrategy
  - 主策略（4 条件超卖）无信号时,回退到单条件选股
  - Fallback 条件: RSI14 ≤ fallback_rsi 且 60日回撤 ≤ fallback_max_dd
  - 目的: 解决 OOS-1 真实阻塞 (V6 信号稀疏)

用法:
  python scripts/walk_forward.py \\
    --strategy src.strategies.v6_with_fallback.V6ReversalWithFallback \\
    --start 2024-01-01 --end 2024-09-30 \\
    --train-months 3 --test-months 1 --step-months 1
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

from .v6_reversal_selection import V6ReversalSelectionStrategy

logger = logging.getLogger(__name__)


class V6ReversalWithFallback(V6ReversalSelectionStrategy):
    """
    V6超卖反转 + Fallback

    优先级:
      1. V6 主策略 (4 条件超卖): MAX_RSI_14 ≤ 38 / MAX_RSI_6 ≤ 23 / MAX_BB_POSITION ≤ 0.10 / MAX_DRAWDOWN_60D ≥ -8%
      2. V6 Fallback (单条件): RSI14 ≤ fallback_rsi 且 60日最大回撤 ≤ fallback_max_dd
      3. 仍无信号 → 空仓 (持有现金, equity_curve 不动)

    参数:
      fallback_rsi     : fallback 用的 RSI14 上限 (默认 50, 50 即 ≤ 50)
      fallback_max_dd  : fallback 用的 60日最大回撤下限 (默认 -20%, 即 ≥ -20%)
      fallback_top_n   : fallback 选几只 (默认同 n_stocks)
    """

    # ── Fallback 默认参数 (子类可在 setting 覆盖) ──────────
    fallback_rsi: float = 50.0
    fallback_max_dd: float = -20.0
    fallback_top_n: Optional[int] = None  # None → 用 n_stocks

    def __init__(self, *args, **kwargs):
        self.fallback_used_count = 0
        self.main_used_count = 0
        super().__init__(*args, **kwargs)

    def _detect_signals(self, indicators: pd.DataFrame,
                        universe: pd.DataFrame) -> list[dict]:
        """
        先按 V6 主策略选, 无信号时按 Fallback 选

        返回: 候选股票列表 [{code, close, score}], 已按 score 降序
        """
        # ── 主策略 ─────────────────────────────────
        main_candidates = super()._detect_signals(indicators, universe)

        if main_candidates:
            self.main_used_count += len(main_candidates)
            return main_candidates

        # ── Fallback: 单条件 (放宽 RSI + 深度回撤) ─────────
        fallback_candidates = self._detect_fallback_signals(indicators, universe)

        if fallback_candidates:
            self.fallback_used_count += len(fallback_candidates)
            logger.debug(
                f"V6 fallback 启用: 选出 {len(fallback_candidates)} 只 "
                f"(RSI14≤{self.fallback_rsi}, DD60≤{self.fallback_max_dd}%)"
            )

        return fallback_candidates

    def _detect_fallback_signals(self, indicators: pd.DataFrame,
                                  universe: pd.DataFrame) -> list[dict]:
        """Fallback 单条件选股: 评分 = 100 - rsi14 (越超卖分越高)"""
        candidates = []
        for _, row in indicators.iterrows():
            rsi14 = row.get("rsi_14", 50)
            if rsi14 is None or pd.isna(rsi14):
                continue

            # 主条件: RSI14 不超买
            if rsi14 > self.fallback_rsi:
                continue

            # 次条件: 60日回撤在阈值内
            max_dd_60 = row.get("max_dd_60d", 0)
            if max_dd_60 is not None and not pd.isna(max_dd_60):
                if max_dd_60 > self.fallback_max_dd:
                    continue

            # 简单评分: 越超卖越高分
            score = max(0, (self.fallback_rsi - rsi14))

            candidates.append({
                "code": row["code"],
                "close": row["close"],
                "score": round(score, 2),
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)

        top_n = self.fallback_top_n or self.n_stocks
        return candidates[:top_n]


# ── 业务名别名 (命名规范化) ──────────────────────────
# V6超卖反转-兜底版
V6ReversalFallbackStrategy = V6ReversalWithFallback  # 英文类名 alias