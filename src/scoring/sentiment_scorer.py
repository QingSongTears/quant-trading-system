"""
情绪面评分器 v2 — DB驱动"安静好股票"模型 (权重 10%)
===================================================

v1 失败原因:
  - API驱动 → 沙箱不可用 → 所有股票同分 → IC = -0.0224 ❌
  - "追热点"逻辑 → 热门题材反而是顶部信号

v2 设计理念:
  - 完全DB驱动，零HTTP调用
  - 方向反转: "安静的好股票"而非"热门的题材股"
  - 低关注度 + 基本面韧性 + 研报正面 = 情绪面高分

6个子指标 (每项 0-3 分，满分 18，归一化到 0-20):
  1. 公告净情绪   (0-3): 近期公告的利好/利空关键词计数
  2. 研报评级     (0-3): 近期研报上调 + 最新评级品质
  3. 低波动       (0-3): 低波动=筹码稳定 (反向评分)
  4. 缩量止跌     (0-3): 成交量收缩 + 价格企稳
  5. 融资信号     (0-3): 融资余额趋势
  6. 信息效率     (0-3): 综合上述信号

用法:
    scorer = SentimentScorer(engine=engine)
    result = scorer.score(code="000001", as_of_date="2026-06-12")
"""

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from typing import Dict, Any, List, Optional, Tuple

from ..config import get_config, get_db_url


class SentimentScorer:
    """情绪面评分器 v2 — DB驱动安静好股票模型"""

    def __init__(self, engine=None):
        if engine is None:
            config = get_config()
            db_url = get_db_url(config)
            self.engine = create_engine(db_url, echo=False)
        else:
            self.engine = engine

    # ============================================================
    #  批量数据加载
    # ============================================================
    def _load_bulk_announcements(
        self, codes: List[str], as_of_date: str, lookback: int = 60
    ) -> Dict[str, pd.DataFrame]:
        """批量加载公告"""
        if not codes:
            return {}
        codes_str = "', '".join(codes)
        query = f"""
            SELECT code, title, date FROM announcements
            WHERE code IN ('{codes_str}')
              AND date <= '{as_of_date}'
              AND date >= DATE('{as_of_date}', '-{lookback} days')
            ORDER BY code, date DESC
        """
        try:
            df = pd.read_sql(query, self.engine)
        except Exception:
            return {c: pd.DataFrame(columns=["title", "date"]) for c in codes}

        result = {}
        for code in codes:
            result[code] = df[df["code"] == code] if not df.empty else pd.DataFrame(columns=["title", "date"])
        return result

    def _load_bulk_price_data(
        self, codes: List[str], as_of_date: str, lookback: int = 60
    ) -> Dict[str, pd.DataFrame]:
        """批量加载价格数据"""
        if not codes:
            return {}
        codes_str = "', '".join(codes)
        query = f"""
            SELECT code, trade_date, close, volume, pct_change
            FROM daily_price
            WHERE code IN ('{codes_str}')
              AND trade_date <= '{as_of_date}'
            ORDER BY code, trade_date DESC
        """
        df = pd.read_sql(query, self.engine)
        if df.empty:
            return {}

        result = {}
        for code, grp in df.groupby("code"):
            grp = grp.sort_values("trade_date").reset_index(drop=True)
            if len(grp) >= 20:
                result[code] = grp.tail(lookback)
        return result

    def _load_bulk_research(
        self, codes: List[str], as_of_date: str, lookback: int = 180
    ) -> Dict[str, pd.DataFrame]:
        """批量加载研报"""
        if not codes:
            return {}
        codes_str = "', '".join(codes)
        query = f"""
            SELECT code, date, rating, rating_change
            FROM research_report
            WHERE code IN ('{codes_str}')
              AND date <= '{as_of_date}'
              AND date >= DATE('{as_of_date}', '-{lookback} days')
            ORDER BY code, date DESC
        """
        try:
            df = pd.read_sql(query, self.engine)
        except Exception:
            return {}

        result = {}
        for code, grp in df.groupby("code"):
            result[code] = grp.reset_index(drop=True)
        return result

    # ============================================================
    #  1. 公告净情绪 (0-3)
    # ============================================================
    def _score_announcement_sentiment(self, ann_df: pd.DataFrame) -> int:
        """分析公告标题的利好/利空倾向"""
        if ann_df.empty:
            return 1  # 无公告=中性

        titles = " ".join(ann_df["title"].tolist())

        positive_kw = [
            "增持", "回购", "分红", "派息", "中标", "合同", "收购",
            "增长", "盈利", "突破", "获批", "订单", "战略合作",
            "股权激励", "员工持股", "业绩预增", "扭亏",
        ]
        negative_kw = [
            "减持", "ST", "退市", "立案", "调查", "处罚", "诉讼",
            "亏损", "下滑", "预亏", "预降", "冻结", "质押",
            "终止", "取消", "违规", "警示", "处分",
        ]

        pos_count = sum(1 for kw in positive_kw if kw in titles)
        neg_count = sum(1 for kw in negative_kw if kw in titles)
        net = pos_count - neg_count

        n = len(ann_df)
        # 大量公告 + 正面倾向 = 高情绪
        if net >= 3 and n >= 5:
            return 3
        elif net >= 1:
            return 2
        elif net == 0 and n >= 2:
            return 1
        elif net < 0:
            return 0
        return 1

    # ============================================================
    #  2. 研报评级 (0-3)
    # ============================================================
    def _score_research_rating(self, research_df: pd.DataFrame) -> int:
        """研报上调 + 评级品质"""
        if research_df is None or research_df.empty:
            return 1

        n = len(research_df)
        upgrades = (research_df["rating_change"] == "上调").sum()
        downgrades = (research_df["rating_change"] == "下调").sum()

        if downgrades > 0:
            return 0

        # 最新评级
        latest = research_df.iloc[0].get("rating", "")
        is_strong = latest in ["买入", "增持"]

        if upgrades >= 2 and is_strong:
            return 3
        elif upgrades >= 1 and is_strong:
            return 2
        elif n >= 2 and is_strong:
            return 1
        elif n >= 1:
            return 1
        return 1

    # ============================================================
    #  3. 低波动 (0-3) — 反向: 越低越好
    # ============================================================
    def _score_low_volatility(self, price_df: pd.DataFrame) -> int:
        """低波动 = 筹码稳定，非共识机会"""
        if price_df is None or len(price_df) < 20:
            return 1

        returns = price_df["pct_change"].dropna().values[-20:]
        if len(returns) < 10:
            return 1

        vol = np.std(returns)
        # vol 通常在 1%~5% 之间
        # <1.5% → 3分, 1.5-2.5% → 2分, 2.5-4% → 1分, >4% → 0分
        if vol < 1.5:
            return 3
        elif vol < 2.5:
            return 2
        elif vol < 4.0:
            return 1
        return 0

    # ============================================================
    #  4. 缩量止跌 (0-3)
    # ============================================================
    def _score_volume_contraction(self, price_df: pd.DataFrame) -> int:
        """成交量收缩 + 价格企稳 = 底部信号"""
        if price_df is None or len(price_df) < 20:
            return 1

        volume = price_df["volume"].values[-20:]
        close = price_df["close"].values[-20:]

        if len(volume) < 10:
            return 1

        # 近5日均量 vs 近20日均量
        vol_5d = np.mean(volume[-5:])
        vol_20d = np.mean(volume)
        if vol_20d == 0:
            return 1
        vol_ratio = vol_5d / vol_20d

        # 近5日价格变化
        price_5d = (close[-1] - close[-5]) / close[-5] * 100

        # 缩量 + 止跌 → 高分
        if vol_ratio < 0.6 and price_5d > -5:
            return 3
        elif vol_ratio < 0.8 and price_5d > -10:
            return 2
        elif vol_ratio < 1.0 and price_5d > -15:
            return 1
        return 0

    # ============================================================
    #  5. 融资信号 (0-3)
    # ============================================================
    def _score_margin_signal(self, code: str, price_df: pd.DataFrame) -> int:
        """融资余额变化趋势"""
        if price_df is None or len(price_df) < 5:
            return 1

        # 用近5日量价关系推断：放量下跌=恐慌，缩量止跌=企稳
        recent = price_df.tail(5)
        returns = recent["pct_change"].values
        volumes = recent["volume"].values

        avg_ret = np.mean(returns)
        # 量价背离检测
        if len(volumes) >= 3:
            vol_trend = (volumes[-1] - volumes[0]) / volumes[0] if volumes[0] > 0 else 0
        else:
            vol_trend = 0

        # 缩量 + 价格稳定 = 积极信号
        if vol_trend < -0.2 and avg_ret > -2:
            return 3
        elif vol_trend < 0 and avg_ret > -3:
            return 2
        elif avg_ret > -5:
            return 1
        return 0

    # ============================================================
    #  6. 信息效率 (0-3) — 综合
    # ============================================================
    def _score_efficiency(self, sub_scores: dict) -> int:
        parts = [
            sub_scores.get("announcement_sentiment", 1),
            sub_scores.get("research_rating", 1),
            sub_scores.get("low_volatility", 1),
            sub_scores.get("volume_contraction", 1),
            sub_scores.get("margin_signal", 1),
        ]
        avg = np.mean(parts)
        if avg >= 2.5:
            return 3
        elif avg >= 2.0:
            return 2
        elif avg >= 1.5:
            return 1
        return 0

    # ============================================================
    #  主入口
    # ============================================================
    def score(self, code: str, as_of_date: str) -> Dict[str, Any]:
        """单只股票评分"""
        # 加载数据
        price_df = self._load_bulk_price_data([code], as_of_date).get(code)
        ann_df = self._load_bulk_announcements([code], as_of_date).get(code, pd.DataFrame())
        research_df = self._load_bulk_research([code], as_of_date).get(code)

        if price_df is None or len(price_df) < 20:
            return {
                "code": code, "as_of_date": as_of_date,
                "total": 0, "weighted": 0.0,
                "sub_scores": {}, "error": "数据不足",
            }

        sub_scores = {
            "announcement_sentiment": self._score_announcement_sentiment(ann_df),
            "research_rating": self._score_research_rating(research_df),
            "low_volatility": self._score_low_volatility(price_df),
            "volume_contraction": self._score_volume_contraction(price_df),
            "margin_signal": self._score_margin_signal(code, price_df),
        }
        sub_scores["efficiency"] = self._score_efficiency(sub_scores)

        total = sum(sub_scores.values())
        weighted = round(total / 18 * 20, 1)

        return {
            "code": code, "as_of_date": as_of_date,
            "total": total, "weighted": weighted,
            "sub_scores": sub_scores, "error": None,
        }

    def batch_score(
        self, codes: List[str], as_of_date: Optional[str] = None, verbose: bool = False
    ) -> pd.DataFrame:
        """批量评分 — 使用批量数据加载"""
        if not codes or as_of_date is None:
            return pd.DataFrame()

        # 批量加载所有数据
        price_data = self._load_bulk_price_data(codes, as_of_date)
        ann_data = self._load_bulk_announcements(codes, as_of_date)
        research_data = self._load_bulk_research(codes, as_of_date)

        results = []
        for code in codes:
            pdf = price_data.get(code)
            if pdf is None or len(pdf) < 20:
                continue

            adf = ann_data.get(code, pd.DataFrame(columns=["title", "date"]))
            rdf = research_data.get(code)

            sub_scores = {
                "announcement_sentiment": self._score_announcement_sentiment(adf),
                "research_rating": self._score_research_rating(rdf),
                "low_volatility": self._score_low_volatility(pdf),
                "volume_contraction": self._score_volume_contraction(pdf),
                "margin_signal": self._score_margin_signal(code, pdf),
            }
            sub_scores["efficiency"] = self._score_efficiency(sub_scores)

            total = sum(sub_scores.values())
            results.append({
                "code": code,
                "as_of_date": as_of_date,
                "total": total,
                "weighted": round(total / 18 * 20, 1),
                **{f"sent_{k}": v for k, v in sub_scores.items()},
            })

        return pd.DataFrame(results)
