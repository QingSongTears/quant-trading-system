"""
基本面评分器 v2 — 成长合理价模型 (权重 15%)
=============================================

版本演进:
  v1 (七维价值):  低PE + 老牌 + 分红 = 好 → 负相关(-0.075) ❌
  v2 (成长合理价): 小盘 + 年轻 + 高ROE + 合理估值 = 好 ✅

核心洞察 (31,200样本 × 基本面因子归因):
  - log_size -0.068: 小市值超额收益是6个因子中最强的信号
  - list_years -0.046: 年轻公司跑赢老牌公司 (v1方向完全反了!)
  - dividend -0.034: 不分红(再投资成长) > 高分红(价值陷阱)
  - PE_proxy -0.028: 低PE确实优于高PE (v1这个方向是对的!)
  - ROE +0.030: 高ROE公司有持续竞争优势
  - 最优象限: 中ROE×低PE (60日收益+14.7%), 最差: 低ROE×高PE (+8.3%)

v2 设计理念:
  寻找"成长合理价"——不是最便宜的，也不是最高ROE的，
  而是 ROE质量 + 估值合理 + 小盘弹性 + 成长阶段的组合。

7个子指标 (每项 0-3 分，满分 21，归一化到 0-20):
  1. ROE质量   (0-3): 高ROE = 持续竞争优势 (保留v1方向)
  2. 估值合理   (0-3): 低PE = 安全边际 (保留v1方向)
  3. 规模弹性   (0-3): 小盘优先 (保留v1方向)
  4. 成长阶段   (0-3): 年轻公司 > 老牌公司 (INVERTED! v1翻转)
  5. 再投资倾向 (0-3): 不分红=成长 > 高分红=价值 (INVERTED!)
  6. 财务健康   (0-3): 低负债率 (新增, 替代v1的现金流)
  7. 成长质量   (0-3): ROE+低负债+营收规模综合 (重构)

用法:
    scorer = FundamentalScorer()
    result = scorer.score("000001")
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from typing import Dict, Any, List, Tuple

from ..config import get_config, get_db_url


class FundamentalScorer:
    """基本面评分器 v2 — 成长合理价模型"""

    def __init__(self, engine=None):
        if engine is None:
            config = get_config()
            db_url = get_db_url(config)
            self.engine = create_engine(db_url, echo=False)
        else:
            self.engine = engine
        self._load_data()

    def _load_data(self):
        """v3: 从 finance_summary 加载真实财报数据 + stock_profile 行业/上市日期"""
        self.df = pd.read_sql(
            "SELECT f.code, "
            "f.ROETTM as roe, f.EPSTTM as eps, f.NAPS, "
            "f.DebtAssetsRatio as debt_ratio, "
            "f.OperatingRevenueGrowRate as revenue_growth, "
            "f.NPParentCompanyYOY as profit_growth, "
            "f.NetOperateCashFlowTTM as cashflow, "
            "f.TotalShareholderEquity as equity, "
            "f.OperatingRevenueTTM as revenue, "
            "f.NPParentCompanyOwnersTTM as net_profit, "
            "f.NetProfitRatioTTM as net_margin, "
            "f.TotalAssets, "
            "f._date as report_date, "
            "COALESCE(s.listedDate, '') as listed_date, "
            "COALESCE(s.industry, '') as industry, "
            "COALESCE(s.sector, '') as sector "
            "FROM finance_summary f "
            "LEFT JOIN stock_profile s ON f.code = s.code",
            self.engine
        )
        self.df = self.df.set_index("code")

        # 强制转换为 float（处理可能混入的字符串）
        float_cols = ["roe", "eps", "NAPS", "debt_ratio", "revenue_growth",
                      "profit_growth", "cashflow", "equity", "revenue",
                      "net_profit", "net_margin", "TotalAssets"]
        for c in float_cols:
            if c in self.df.columns:
                self.df[c] = pd.to_numeric(self.df[c], errors="coerce")

        # 衍生指标（clip处理极端值）
        net_profit_abs = self.df["net_profit"].fillna(0).replace(0, np.nan).abs()
        equity_v = self.df["equity"].fillna(1e7).clip(lower=1e4)
        self.df["pe_proxy"] = (equity_v / net_profit_abs).clip(-500, 500)
        self.df["roe_v"] = (self.df["roe"] / 100).clip(-1, 1)  # ROETTM是百分比，转小数
        self.df["debt_v"] = (self.df["debt_ratio"] / 100).clip(0, 1)  # DebtAssetsRatio是百分比
        self.df["log_size"] = np.log10(equity_v)
        # listedDate → 提取年份
        raw = self.df["listed_date"].replace("", np.nan)
        self.df["list_years"] = np.where(
            raw.notna(),
            (2026 - raw.astype(str).str[:4].astype(float)).clip(0, 35),
            10
        )
        # 现金流质量
        self.df["cf_quality"] = self.df["cashflow"].fillna(0).clip(-1e12, 1e12)
        # 营收增长
        self.df["rev_growth_v"] = self.df["revenue_growth"].fillna(0).clip(-200, 500)

        # 预计算分位数
        self._pct = {}
        for col in ["roe_v", "pe_proxy", "log_size", "list_years", "debt_v",
                     "cf_quality", "rev_growth_v", "net_margin"]:
            vals = self.df[col].dropna()
            if len(vals) > 0:
                self._pct[col] = {
                    p: np.percentile(vals, p) for p in [10, 20, 30, 40, 50, 60, 70, 80, 90]
                }

    # ============================================================
    #  1. ROE质量 (0-3) — 高ROE = 持续竞争优势
    # ============================================================
    def _score_roe(self, roe: float) -> int:
        """
        实证: ROE +0.030 相关, Q5-Q1=+3.0% (60d)

        3分: ROE > P80 (行业中上水平, 稳定盈利)
        2分: ROE > P50
        1分: ROE > P20
        0分: ROE < P20 或亏损
        """
        if pd.isna(roe) or roe < 0:
            return 0
        pct = self._pct.get("roe_v", {})
        if roe >= pct.get(80, 5):
            return 3
        elif roe >= pct.get(50, 1.5):
            return 2
        elif roe >= pct.get(20, 0.5):
            return 1
        return 0

    # ============================================================
    #  2. 估值吸引力 (0-3) — 低PE = 安全边际
    # ============================================================
    def _score_valuation(self, pe: float) -> int:
        """
        实证: PE_proxy -0.028 相关, 低PE(Q1) > 高PE(Q5) 差距-3.0% (60d)

        v1这个方向是对的！低PE确实优于高PE。

        3分: PE < P20 (市场最便宜的20%)
        2分: PE < P40
        1分: PE < P60
        0分: PE > P60 或负PE(亏损)
        """
        if pd.isna(pe) or pe <= 0:
            return 0
        pct = self._pct.get("pe_proxy", {})
        if pe <= pct.get(20, 8):
            return 3
        elif pe <= pct.get(40, 15):
            return 2
        elif pe <= pct.get(60, 25):
            return 1
        return 0

    # ============================================================
    #  3. 规模弹性 (0-3) — 小盘优先 (最强信号!)
    # ============================================================
    def _score_size(self, log_size: float) -> int:
        """
        实证: log_size -0.068 相关, 6因子中最强! Q1(小盘) > Q5(大盘) -4.6% (60d)

        纯粹的小市值溢价。A股中小盘弹性远超大盘。

        3分: 规模 < P30 (真正小盘)
        2分: 规模 < P50
        1分: 规模 < P70
        0分: 规模 > P70 (大盘股弹性差)
        """
        if pd.isna(log_size):
            return 1
        pct = self._pct.get("log_size", {})
        if log_size <= pct.get(30, 10.2):
            return 3
        elif log_size <= pct.get(50, 10.6):
            return 2
        elif log_size <= pct.get(70, 11.0):
            return 1
        return 0

    # ============================================================
    #  4. 成长阶段 (0-3) — 年轻公司 > 老牌公司 (INVERTED!)
    # ============================================================
    def _score_growth_stage(self, list_years: float) -> int:
        """
        实证: list_years -0.046 相关, Q1(新股) > Q5(老股) -3.6% (60d)

        v1方向反了！v1奖励老牌公司(≥10年=3分)，但年轻公司跑赢。

        3分: 上市 2-8 年 (过了次新风险期, 处于成长期)
        2分: 上市 8-15 年 (成熟期, 仍有一定成长性)
        1分: 上市 < 2 年 (次新股, 波动大但弹性好)
        0分: 上市 > 15 年 (成熟/衰退, 弹性最差)
        """
        if pd.isna(list_years):
            return 1
        if 2 <= list_years < 8:
            return 3
        elif 8 <= list_years < 15:
            return 2
        elif list_years < 2:
            return 1
        return 0

    # ============================================================
    #  5. 再投资倾向 (0-3) — 不分红 > 高分红 (INVERTED!)
    # ============================================================
    def _score_reinvestment(self, dividend: float) -> int:
        """
        实证: dividend -0.034 相关, Q1(不分红) > Q5(高分红) -3.8% (60d)

        v1方向反了！v1奖励分红(有分红=3分, 无=0分), 但成长股不分红跑赢。

        3分: 零分红 (利润再投资 = 高增长预期)
        2分: 低分红 (象征性分红)
        1分: 中等分红
        0分: 高分红 (价值陷阱 — 缺乏成长机会才分红)
        """
        if pd.isna(dividend) or dividend == 0:
            return 3  # 不分红=成长
        pct = self._pct.get("dividend", {})
        if dividend <= pct.get(30, 2):
            return 2
        elif dividend <= pct.get(60, 8):
            return 1
        return 0

    # ============================================================
    #  6. 财务健康 (0-3) — 低负债率 (替代v1现金流)
    # ============================================================
    def _score_financial_health(self, debt_ratio: float) -> int:
        """
        低负债率 = 财务稳健 = 抗风险能力强。

        3分: 负债率 < P30 (低杠杆, 财务安全)
        2分: 负债率 < P50
        1分: 负债率 < P70
        0分: 负债率 > P70 (高杠杆风险)
        """
        if pd.isna(debt_ratio):
            return 1
        pct = self._pct.get("debt_v", {})
        if debt_ratio <= pct.get(30, 0.3):
            return 3
        elif debt_ratio <= pct.get(50, 0.5):
            return 2
        elif debt_ratio <= pct.get(70, 0.7):
            return 1
        return 0

    # ============================================================
    #  7. 成长质量 (0-3) — ROE + 低负债 + 营收
    # ============================================================
    def _score_growth_quality(self, roe: float, debt_ratio: float,
                              rev_growth: float, cf_quality: float) -> int:
        """v3: ROE + 低负债 + 营收增长 + 正现金流 = 有质量的成长"""
        pct_roe = self._pct.get("roe_v", {})
        pct_debt = self._pct.get("debt_v", {})
        pct_growth = self._pct.get("rev_growth_v", {})
        pct_cf = self._pct.get("cf_quality", {})

        high_roe = (not pd.isna(roe) and roe >= pct_roe.get(50, 0.05))
        low_debt = (not pd.isna(debt_ratio) and debt_ratio <= pct_debt.get(50, 0.5))
        pos_growth = (not pd.isna(rev_growth) and rev_growth >= pct_growth.get(50, 0))
        pos_cf = (not pd.isna(cf_quality) and cf_quality > 0)

        score = sum([high_roe, low_debt, pos_growth, pos_cf])
        if score >= 4:
            return 3
        elif score >= 2:
            return 2
        elif score >= 1:
            return 1
        return 0

    # ============================================================
    #  主入口
    # ============================================================
    def score(self, code: str) -> Dict[str, Any]:
        if code not in self.df.index:
            return {
                "code": code,
                "total": 0,
                "weighted": 0.0,
                "sub_scores": {},
                "error": "无财务数据",
            }

        row = self.df.loc[code]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        roe = row.get("roe_v", np.nan)
        pe = row.get("pe_proxy", np.nan)
        log_size = row.get("log_size", np.nan)
        list_yrs = row.get("list_years", np.nan)
        debt = row.get("debt_v", np.nan)
        rev_growth = row.get("rev_growth_v", np.nan)
        cf = row.get("cf_quality", np.nan)

        sub_scores = {
            "roe_quality": self._score_roe(roe),
            "valuation": self._score_valuation(pe),
            "size_premium": self._score_size(log_size),
            "growth_stage": self._score_growth_stage(list_yrs),
            "reinvestment": 1,  # 无分红数据，给中性
            "financial_health": self._score_financial_health(debt),
            "growth_quality": self._score_growth_quality(roe, debt, rev_growth, cf),
        }

        total = sum(sub_scores.values())
        weighted = round(total / 21 * 20, 1)

        return {
            "code": code,
            "total": total,
            "weighted": weighted,
            "sub_scores": sub_scores,
            "error": None,
        }

    def batch_score(self, codes: List[str]) -> pd.DataFrame:
        results = []
        for code in codes:
            r = self.score(code)
            if r["error"] is None:
                results.append({
                    "code": code,
                    "total": r["total"],
                    "weighted": r["weighted"],
                    **{f"fundam_{k}": v for k, v in r["sub_scores"].items()},
                })
        return pd.DataFrame(results)
