"""
主策略编排 — 整合筛选、信号、量化检测、买点评分、风控
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from ..config import OUTPUT_DIR, MAX_POSITIONS

from .data_fetcher import (
    get_all_stocks_with_market_cap,
    get_stock_kline,
    get_industry_classification,
)
from .screener import screen_candidates
from .signal_detector import batch_detect_signals
from .quant_detector import batch_detect_quant
from .buy_point import batch_evaluate_buy_points
from .risk_manager import RiskManager


class StockScreenerStrategy:
    """A股波段选股策略主引擎"""

    def __init__(self):
        self.risk_manager = RiskManager()
        self.spot_df = None
        self.candidates = None
        self.signals = None
        self.quant_results = None
        self.buy_points = None
        self.final_picks = None
        self.run_time = None

    def run(self):
        """执行完整选股流程"""
        print("=" * 60)
        print(f"🚀 A股波段选股策略 — 启动")
        print(f"📅 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

        # Step 1: 获取全市场行情
        print("\n📡 [1/6] 获取全市场行情...")
        self.spot_df = get_all_stocks_with_market_cap()
        if self.spot_df.empty:
            print("❌ 无法获取行情数据，终止运行")
            return
        print(f"  ✅ 获取到 {len(self.spot_df)} 只股票")

        # Step 2: 筛选候选池
        print("\n🔍 [2/6] 筛选候选池...")
        self.candidates = screen_candidates(self.spot_df)
        if self.candidates.empty:
            print("❌ 筛选后无候选股票，终止运行")
            return

        # Step 3: 检测金叉信号
        print(f"\n📊 [3/6] 检测 EMA 金叉信号... (候选池 {len(self.candidates)} 只)")
        candidate_codes = self.candidates["code"].tolist()

        # 预加载K线缓存
        print("  🔄 预加载K线数据...")
        kline_cache = {}
        for i, code in enumerate(candidate_codes):
            if i % 50 == 0:
                print(f"     K线预加载: {i}/{len(candidate_codes)}")
            try:
                kline = get_stock_kline(code, days=250)
                if not kline.empty:
                    kline_cache[code] = kline
            except:
                pass
        print(f"  ✅ K线缓存: {len(kline_cache)} 只")

        self.signals = batch_detect_signals(candidate_codes, kline_cache)

        if self.signals.empty:
            print("\n📭 未检测到任何金叉信号")
            self._save_empty_result()
            return

        print(f"  ✅ 检测到 {len(self.signals)} 只金叉标的")

        # Step 4: 量化检测
        print(f"\n🤖 [4/6] 量化资金检测...")
        signal_codes = self.signals["code"].tolist()
        self.quant_results = batch_detect_quant(signal_codes, kline_cache)

        quant_count = len(self.quant_results[self.quant_results["is_quant_stock"]])
        normal_count = len(self.quant_results) - quant_count
        print(f"  ✅ 量化票: {quant_count} | 正常票: {normal_count}")

        # Step 5: 买点评估
        print(f"\n🎯 [5/6] 回踩买点评估...")
        self.buy_points = batch_evaluate_buy_points(self.signals, kline_cache)

        buy_ready = self.buy_points[self.buy_points["buy_ready"]] if not self.buy_points.empty else pd.DataFrame()
        print(f"  ✅ 具备买点: {len(buy_ready)}")

        # Step 6: 综合排序 & 风控
        print(f"\n🏆 [6/6] 综合排序生成最终推荐...")
        self.final_picks = self._rank_and_filter()
        self.run_time = datetime.now()

        # 输出结果
        self._print_results()
        self._save_results()

        return self.final_picks

    def _rank_and_filter(self) -> pd.DataFrame:
        """综合排名和过滤"""
        if self.buy_points is None or self.buy_points.empty:
            return pd.DataFrame()

        df = self.buy_points.copy()

        # 合并量化检测结果
        if self.quant_results is not None and not self.quant_results.empty:
            qmap = self.quant_results.set_index("code")[["quant_score", "is_quant_stock", "label"]].to_dict("index")
            for idx, row in df.iterrows():
                code = row["code"]
                if code in qmap:
                    df.at[idx, "quant_score"] = qmap[code]["quant_score"]
                    df.at[idx, "is_quant_stock"] = qmap[code]["is_quant_stock"]
                    df.at[idx, "label"] = qmap[code]["label"]

        # 合并候选池基本信息（名称、市值等）
        if self.candidates is not None:
            info_map = self.candidates.set_index("code")[["name", "mcap_yi", "pe_ttm", "pb", "turnover_pct"]].to_dict("index")
            for idx, row in df.iterrows():
                code = row["code"]
                if code in info_map:
                    for k, v in info_map[code].items():
                        df.at[idx, k] = v

        # 综合得分 = 信号分 * 0.35 + 买点分 * 0.25 + 量化调整 * 0.15 + 信号强度 * 0.15 + 风控调整 * 0.10
        if "signal_score" in df.columns and "score" in df.columns:
            # 量化票扣分
            quant_penalty = df["is_quant_stock"].fillna(False).apply(lambda x: -2 if x else 0)
            df["final_score"] = (
                df["signal_score"].fillna(5) * 0.35 +
                df["score"].fillna(2) * 0.25 +
                (1 - df["quant_score"].fillna(0)) * 10 * 0.15 +  # 量化分低=好
                quant_penalty * 0.10
            )
        else:
            df["final_score"] = df.get("score", 0)

        df = df.sort_values("final_score", ascending=False)

        # 取 TOP 10（后续由用户手动筛选到 3-5 只）
        top = df.head(10).copy()
        top["rank"] = range(1, len(top) + 1)

        return top

    def _print_results(self):
        """打印结果"""
        if self.final_picks is None or self.final_picks.empty:
            print("\n📭 今日无符合条件的标的")
            return

        print("\n" + "=" * 80)
        print("📋 选股结果 — A股波段策略")
        print("=" * 80)

        for _, row in self.final_picks.iterrows():
            print(f"\n{'─' * 60}")
            print(f"#{int(row['rank'])} {row.get('name', '未知')} ({row['code']})")
            print(f"   🏷️  {row.get('label', '📊 正常波段')}")
            print(f"   💰 市值: {row.get('mcap_yi', 0):.0f}亿 | PE: {row.get('pe_ttm', 0):.1f} | 换手: {row.get('turnover_pct', 0):.1f}%")
            print(f"   📊 信号分: {row.get('signal_score', 0)} | 买点分: {row.get('score', 0)} | 综合: {row.get('final_score', 0):.1f}")
            print(f"   📍 金叉日: {row.get('cross_date', 'N/A')} | 距今 {row.get('days_since_cross', '?')}天")
            print(f"   📈 当前价: {row.get('current_close', 0)} | EMA20: {row.get('ema_fast', 0)} | EMA60: {row.get('ema_slow', 0)}")
            print(f"   🔻 回踩深度: {row.get('pullback_depth_pct', 0):.1f}%")
            print(f"   📝 {row.get('desc', '')}")
            print(f"   🤖 量化分: {row.get('quant_score', 0):.2f}")

            # 风控建议
            is_quant = row.get('is_quant_stock', False)
            stop_loss = round(row.get('current_close', 0) * 0.9, 2)  # -10%
            take_profit_pct = 8 if is_quant else 15
            take_profit = round(row.get('current_close', 0) * (1 + take_profit_pct / 100), 2)
            print(f"   🛡️  止损: {stop_loss} (-10%) | 止盈: {take_profit} (+{take_profit_pct}%)")

    def _save_results(self):
        """保存结果到文件"""
        out_dir = Path(OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d_%H%M")

        if self.final_picks is not None and not self.final_picks.empty:
            # CSV
            csv_path = out_dir / f"stock_picks_{date_str}.csv"
            self.final_picks.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"\n💾 结果已保存: {csv_path}")

            # 汇总文本
            txt_path = out_dir / f"stock_picks_{date_str}.md"
            lines = [
                f"# A股波段选股结果 — {datetime.now().strftime('%Y-%m-%d')}",
                "",
                f"候选池: {len(self.candidates) if self.candidates is not None else 0} 只",
                f"金叉信号: {len(self.signals) if self.signals is not None else 0} 只",
                f"推荐: {len(self.final_picks)} 只",
                "",
                "| # | 代码 | 名称 | 市值(亿) | 信号分 | 买点分 | 综合 | 量化 | 状态 |",
                "|---|------|------|---------|--------|--------|------|------|------|",
            ]
            for _, row in self.final_picks.iterrows():
                lines.append(
                    f"| {int(row['rank'])} | {row['code']} | {row.get('name', '')} | "
                    f"{row.get('mcap_yi', 0):.0f} | {row.get('signal_score', 0)} | "
                    f"{row.get('score', 0)} | {row.get('final_score', 0):.1f} | "
                    f"{row.get('label', '')} | {row.get('status', '')} |"
                )

            lines.extend([
                "",
                "## 风控规则",
                f"- 止损: -10% 固定止损",
                f"- 量化票止盈: +8% | 普通票止盈: +15%",
                f"- 最大持仓: {MAX_POSITIONS} 只",
                f"- 量化票持仓 ≤ 5 个交易日",
                "",
                "> ⚠️ 以上为量化策略筛选结果，仅供参考，不构成投资建议",
            ])

            txt_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"💾 摘要已保存: {txt_path}")

    def _save_empty_result(self):
        """保存空结果"""
        out_dir = Path(OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        txt_path = out_dir / f"stock_picks_{date_str}.md"
        txt_path.write_text(
            f"# A股波段选股结果 — {datetime.now().strftime('%Y-%m-%d')}\n\n"
            f"📭 今日无符合条件的标的\n\n"
            f"候选池: {len(self.candidates) if self.candidates is not None else 0} 只，无金叉信号。\n",
            encoding="utf-8",
        )
        print(f"💾 空结果已保存: {txt_path}")
