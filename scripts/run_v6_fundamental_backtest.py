#!/usr/bin/env python3
"""
v6 + 基本面融合 回测验证（高效版，共享缓存）
==============================================
对比: 纯v6 vs v6+基本面融合
"""
import sys, os, time, warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from datetime import date
from typing import List

from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.scoring.fundamental_scorer import FundamentalScorer
from src.backtest.base_selection_strategy import BaseSelectionStrategy
from src.backtest.portfolio_engine import PortfolioBacktestEngine


# ============================================================
# V6 + 基本面融合策略
# ============================================================

class V6FundamentalHybrid(V6ReversalSelectionStrategy):
    """V6信号 × 基本面成长合理价模型 加权融合"""
    
    name = "v6_fundamental"
    description = "V6超卖信号 × 基本面成长合理价模型 加权融合"
    source = "v6_reversal + fundamental_scorer_v2"
    
    V6_WEIGHT = 0.6
    FUNDAMENTAL_WEIGHT = 0.4
    
    def __init__(self, **kwargs):
        self.v6_weight = kwargs.pop("v6_weight", self.V6_WEIGHT)
        self.fundam_weight = kwargs.pop("fundam_weight", self.FUNDAMENTAL_WEIGHT)
        super().__init__(**kwargs)
        self.fundamental_scorer = None
    
    def _init_scorer(self):
        if self.fundamental_scorer is None:
            self.fundamental_scorer = FundamentalScorer()
    
    def select(self, rebalance_date, universe_df):
        """v6候选池 → 基本面评分 → 加权融合"""
        v6_selected = super().select(rebalance_date, universe_df)
        
        # select() 返回 List[str]（股票代码列表）
        if not v6_selected:
            return v6_selected
        
        # 转为 DataFrame
        codes = v6_selected if isinstance(v6_selected, list) else v6_selected["code"].tolist()
        if not codes:
            return v6_selected
        
        self._init_scorer()
        fundam_scores = self.fundamental_scorer.batch_score(codes)
        
        if fundam_scores.empty:
            return v6_selected
        
        # v6 排名分: 按选股顺序给分（排前面的分高）
        n = len(codes)
        v6_rank_scores = {c: 20 * (n - i) / n for i, c in enumerate(codes)}
        
        # 融合
        merged = fundam_scores[["code", "weighted"]].copy()
        merged["v6_score"] = merged["code"].map(v6_rank_scores).fillna(0)
        merged["combined"] = (
            merged["v6_score"] * self.v6_weight +
            merged["weighted"] * self.fundam_weight
        )
        
        merged = merged.sort_values("combined", ascending=False)
        final_codes = merged.head(self.n_stocks)["code"].tolist()
        
        return final_codes


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("v6 + 基本面融合 回测验证（高效版）")
    print("=" * 60)
    
    START = date(2024, 1, 2)
    END = date(2025, 6, 30)
    CAPITAL = 1_000_000
    
    engine = PortfolioBacktestEngine()
    
    # 策略1: 纯v6（日频调仓，与原成功回测参数一致）
    v6 = V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=1, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5
    )
    
    # 策略2: v6+基本面（共用v6预计算缓存）
    v6_fund = V6FundamentalHybrid(
        n_stocks=8, rebalance_days=1, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        v6_weight=0.6, fundam_weight=0.4
    )
    
    # 预计算（仅一次，类缓存共享）
    print("\n[预计算] v6 指标（仅一次，两个策略共享）...")
    t0 = time.time()
    V6ReversalSelectionStrategy._class_cache = {}
    v6.precompute_all(START, END)
    print(f"  预计算耗时: {time.time()-t0:.0f}s")
    
    results = {}
    for strat, label in [(v6, "Pure V6"), (v6_fund, "V6+Fundamental")]:
        print(f"\n[{label}] 回测中...")
        t0 = time.time()
        report = engine.run(strat, start_date=START, end_date=END, initial_capital=CAPITAL)
        elapsed = time.time() - t0
        results[label] = report
        print(f"  耗时: {elapsed:.0f}s | 年化={report.annual_return:.2f}% "
              f"夏普={report.sharpe_ratio:.2f} 回撤={report.max_drawdown:.2f}% "
              f"交易={report.total_trades}")
    
    # 对比
    print("\n" + "=" * 70)
    print(f"{'策略':<22s} {'年化%':>8s} {'夏普':>7s} {'回撤%':>8s} {'交易':>6s} {'胜率%':>7s}")
    print("-" * 70)
    for label, r in results.items():
        print(f"{label:<22s} {r.annual_return:>7.2f}% {r.sharpe_ratio:>6.2f} "
              f"{r.max_drawdown:>7.2f}% {r.total_trades:>5d} {r.win_rate:>6.1f}%")
    print("-" * 70)
    print("目标: 年化>15%, 夏普>1.0, 回撤<-25%, 交易≥50笔")
    
    # 保存
    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "output", "v6_fundamental_comparison.csv"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pd.DataFrame([
        {"strategy": l, "annual_return_pct": r.annual_return, "sharpe_ratio": r.sharpe_ratio,
         "max_drawdown_pct": r.max_drawdown, "total_trades": r.total_trades,
         "win_rate_pct": r.win_rate, "volatility_pct": r.annual_volatility}
        for l, r in results.items()
    ]).to_csv(out_path, index=False)
    print(f"\n结果已保存: {out_path}")
    
    # 结论
    v6_sharpe = results["Pure V6"].sharpe_ratio
    fund_sharpe = results["V6+Fundamental"].sharpe_ratio
    delta = fund_sharpe - v6_sharpe
    if delta > 0.05:
        print(f"\n✅ 基本面融合有效提升夏普: +{delta:.2f}")
    elif delta > 0:
        print(f"\n📊 基本面融合小幅提升夏普: +{delta:.2f}")
    else:
        print(f"\n⚠️ 基本面融合未提升夏普: {delta:.2f}")


if __name__ == "__main__":
    main()
