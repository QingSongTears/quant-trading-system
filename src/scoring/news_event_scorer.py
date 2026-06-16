"""
消息面/事件驱动评分器 (权重 15%)
===============================

基于 announcements 表 + research_report 表的催化剂评分。

数据: 4,920条公告, 462只股票; 1,919条研报(去重), 328只股票
日期: 公告 2026-03~2026-06, 研报 2024-01~2026-06

子指标:
  1. 公告密集度     (0-3): 近30日公告数量
  2. 利好类型       (0-3): 分红/增持/回购 = 利好
  3. 季报窗口       (0-3): 是否处于业绩披露窗口期
  4. 风险规避       (0-3): 有无风险警示/ST/诉讼 (反向)
  5. 研报关注度     (0-3): 近90日研报数量
  6. 评级动量       (0-3): 近期评级上调趋势
  7. 事件综合       (0-3): 综合事件催化剂评分
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional

from ..config import get_config, get_db_url

# 研报数据缓存
_research_cache: Optional[pd.DataFrame] = None


class NewsEventScorer:
    """消息面评分器"""

    def __init__(self, engine=None):
        if engine is None:
            config = get_config()
            db_url = get_db_url(config)
            self.engine = create_engine(db_url, echo=False)
        else:
            self.engine = engine

    # ============================================================
    #  数据加载
    # ============================================================

    def _get_announcements(self, code: str, as_of_date: str, lookback: int = 30) -> pd.DataFrame:
        """获取近期公告"""
        query = f"""
            SELECT title, date FROM announcements
            WHERE code = '{code}'
              AND date <= '{as_of_date}'
            ORDER BY date DESC
            LIMIT 50
        """
        try:
            return pd.read_sql(query, self.engine)
        except:
            return pd.DataFrame(columns=["title", "date"])

    def _load_research_data(self, code: str, as_of_date: str, lookback: int = 90) -> pd.DataFrame:
        """加载指定股票的研报数据（近 N 天）

        Args:
            code: 股票代码 (6位字符串, 如 '000001')
            as_of_date: 截止日期 (YYYY-MM-DD)
            lookback: 回溯天数

        Returns:
            DataFrame with columns: code, market, name, date, org, title, rating, rating_change
        """
        global _research_cache

        # 首次加载全量数据到缓存
        if _research_cache is None:
            try:
                _research_cache = pd.read_sql(
                    "SELECT code, market, name, date, org, title, rating, rating_change "
                    "FROM research_report ORDER BY code, date DESC",
                    self.engine
                )
            except Exception:
                return pd.DataFrame()

        if _research_cache is None or _research_cache.empty:
            return pd.DataFrame()

        # 计算日期边界
        cutoff = (pd.to_datetime(as_of_date) - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")

        # 筛选: 同股票 + 日期范围内
        mask = (
            (_research_cache["code"] == code)
            & (_research_cache["date"] >= cutoff)
            & (_research_cache["date"] <= as_of_date)
        )
        return _research_cache[mask].copy()

    # ============================================================
    #  1. 公告密集度 (0-3)
    # ============================================================
    def _score_density(self, df: pd.DataFrame) -> int:
        """近期公告越多 → 催化剂越多"""
        if df.empty:
            return 1  # 无公告=中性
        n = len(df)
        if n >= 10:
            return 3
        elif n >= 5:
            return 2
        elif n >= 2:
            return 1
        return 0

    # ============================================================
    #  2. 利好类型 (0-3)
    # ============================================================
    def _score_positive_type(self, df: pd.DataFrame) -> int:
        """分红/增持/回购 = 利好信号"""
        if df.empty:
            return 1
        titles = " ".join(df["title"].tolist()).lower() if "title" in df.columns else ""

        positive = 0
        if any(kw in titles for kw in ["分红", "派息", "权益分派"]):
            positive += 1
        if any(kw in titles for kw in ["增持", "回购"]):
            positive += 1
        if any(kw in titles for kw in ["中标", "合同", "协议", "收购"]):
            positive += 1

        return min(positive, 3)

    # ============================================================
    #  3. 季报窗口 (0-3)
    # ============================================================
    def _score_earnings_window(self, df: pd.DataFrame, as_of_date: str) -> int:
        """是否在业绩披露密集期"""
        if df.empty:
            return 1
        titles = " ".join(df["title"].tolist())

        has_quarterly = any(kw in titles for kw in ["季度报告", "年度报告", "年报", "季报", "业绩预告", "业绩快报"])

        if has_quarterly:
            return 3
        return 1

    # ============================================================
    #  4. 风险规避 (0-3) — 反向: 无风险 = 高分
    # ============================================================
    def _score_risk_avoidance(self, df: pd.DataFrame) -> int:
        """无风险警示 = 3分"""
        if df.empty:
            return 2  # 无公告=信息不足, 偏正面
        titles = " ".join(df["title"].tolist())

        risk_kw = ["ST", "退市", "风险", "警示", "立案", "调查", "处罚", "诉讼", "减持"]
        risk_count = sum(1 for kw in risk_kw if kw in titles)

        if risk_count == 0:
            return 3
        elif risk_count == 1:
            return 1
        return 0

    # ============================================================
    #  5. 研报关注度 (0-3)
    # ============================================================
    def _score_research_attention(self, code: str, as_of_date: str) -> int:
        """研报关注度: 近90日研报数量反映机构关注热度

        分析依据:
          - 328只股票有研报覆盖, 近90天中位数=2篇, Q3=3篇, 最大=10篇
          - 1篇研报的股票 20日平均收益=-0.63%
          - 2-3篇: +0.68%~+2.09%
          - 4-10篇: +0.07%~+1.91%

        阈值: >=4篇→3分, 2-3篇→2分, 1篇→1分, 0篇→0分
        """
        df = self._load_research_data(code, as_of_date, lookback=90)
        n = len(df)

        if n >= 4:
            return 3
        elif n >= 2:
            return 2
        elif n >= 1:
            return 1
        return 0

    # ============================================================
    #  6. 评级动量 (0-3)
    # ============================================================
    def _score_rating_momentum(self, code: str, as_of_date: str) -> int:
        """评级动量: 近期评级变化趋势

        分析依据:
          - "上调" 占总数 75% (1437/1919), "维持" 16%, "下调" 仅 13条
          - "维持" 20日平均收益最高 (+2.04%), "上调" 接近0
          - "下调" 和 "未知" 显著负收益 (-0.77%~-1.91%)
          - 上调次数多 + 无下调 = 强动量信号

        评分:
          3: 近180天 >=3次上调 且 最新评级为"买入"
          2: >=2次上调 或 维持+买入
          1: 有研报覆盖, 无下调
          0: 有下调或负面信号
        """
        df = self._load_research_data(code, as_of_date, lookback=180)
        if df is None or df.empty:
            return 1  # 无研报数据=中性

        n = len(df)
        upgrades = (df["rating_change"] == "上调").sum()
        downgrades = (df["rating_change"] == "下调").sum()
        maintains = (df["rating_change"] == "维持").sum()

        # 有任何下调 → 严重负面
        if downgrades > 0:
            return 0

        # 获取最新评级
        latest_rating = None
        if "rating" in df.columns and len(df) > 0:
            ratings = df["rating"].dropna()
            if len(ratings) > 0:
                latest_rating = ratings.iloc[0]

        is_strong_buy = latest_rating == "买入"

        # 多篇上调 + 买入级别 → 强动量
        if upgrades >= 3 and is_strong_buy:
            return 3

        # >=2次上调或维持+买入 → 正面动量
        if upgrades >= 2:
            return 2
        if maintains >= 2 and is_strong_buy:
            return 2
        if upgrades >= 1 and is_strong_buy:
            return 2

        # 有覆盖, 无下调 → 中性偏正
        if upgrades >= 1 or maintains >= 1:
            return 1

        # 仅有未知评级变化 → 信息不足
        return 1 if n >= 1 else 0

    # ============================================================
    #  7. 事件综合 (0-3)
    # ============================================================
    def _score_event_composite(self, sub_scores: dict) -> int:
        """综合事件催化剂"""
        parts = [
            sub_scores.get("density", 1),
            sub_scores.get("positive_type", 1),
            sub_scores.get("earnings_window", 1),
            sub_scores.get("risk_avoidance", 1),
            sub_scores.get("research_attention", 1),
            sub_scores.get("rating_momentum", 1),
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
    def score(self, code: str, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """计算消息面综合评分

        Args:
            code: 股票代码
            as_of_date: 截止日期

        Returns:
            {
                "code": str,
                "as_of_date": str,
                "total": int,       # 7项原始总分 (0-21)
                "weighted": float,  # 归一化到 0-20
                "sub_scores": dict, # 7个子指标
                "error": str | None
            }
        """
        df = self._get_announcements(code, as_of_date)

        sub_scores = {
            "density": self._score_density(df),
            "positive_type": self._score_positive_type(df),
            "earnings_window": self._score_earnings_window(df, as_of_date),
            "risk_avoidance": self._score_risk_avoidance(df),
            "research_attention": self._score_research_attention(code, as_of_date),
            "rating_momentum": self._score_rating_momentum(code, as_of_date),
        }
        sub_scores["event_composite"] = self._score_event_composite(sub_scores)

        total = sum(sub_scores.values())
        weighted = round(total / 21 * 20, 1)

        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": total,
            "weighted": weighted,
            "sub_scores": sub_scores,
            "error": None,
        }

    def batch_score(
        self, codes: List[str], as_of_date: Optional[str] = None, verbose: bool = False
    ) -> "pd.DataFrame":
        import pandas as pd
        results = []
        for i, code in enumerate(codes):
            r = self.score(code, as_of_date)
            if r["error"] is None:
                results.append({
                    "code": code,
                    "as_of_date": r["as_of_date"],
                    "total": r["total"],
                    "weighted": r["weighted"],
                    **{f"news_{k}": v for k, v in r["sub_scores"].items()},
                })
            if verbose and (i + 1) % 50 == 0:
                print(f"  ... news_event {i + 1}/{len(codes)}")
        return pd.DataFrame(results)
