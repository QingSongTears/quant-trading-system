"""
机构持仓/筹码集中度评分器 v3 — dragon_tiger 数据驱动 (权重 10%)
============================================================

v3 更新 (2026-06): 使用 dragon_tiger_data 替代 lhb_institutional

数据源 (DB表):
  - dragon_tiger_data: 龙虎榜全量数据 (26,732条, 4,313只股票)
  - margin_trading:    融资融券数据 (76,071条, 3,663只股票)
  - shareholder_count: 股东户数最新快照 (5,332条, 5,099只股票)

子指标 (每项 0-3 分, 满分 18, 归一化到 0-20):
  1. 龙虎榜净买入   (0-3): 近30日龙虎榜净买入额
  2. 筹码集中度     (0-3): 股东户数环比变化率 (负=集中=好)
  3. 融资情绪       (0-3): 融资净买入额
  4. 上榜活跃度     (0-3): 龙虎榜出现天数
  5. 筹码综合       (0-3): 综合筹码评分
  6. 北向资金       (0-3): 北向资金 (stub, 后续下载)
"""
from __future__ import annotations
import numpy as np
import pandas as pd
# PR3.2: create_engine 由 base.py 通过 get_engine 单例提供, text
from typing import Any
from datetime import datetime, timedelta

from ..config import get_config, get_db_url
from ..db.sql_utils import read_sql
from .base import BaseScorer


class InstitutionalScorer(BaseScorer):
    """机构持仓评分器 v3 — dragon_tiger 数据驱动"""

    name = "institutional"
    label_zh = "机构持仓"
    weight = 0.10
    max_raw = 18

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        # 批量预加载的数据缓存
        self._dragon_cache: pd.DataFrame | None = None
        self._margin_cache: pd.DataFrame | None = None
        self._holder_cache: pd.DataFrame | None = None
        self._cache_date: str | None = None

    # ============================================================
    #  DB 数据加载
    # ============================================================

    def _prefetch_all(self, as_of_date: str | None = None):
        """批量模式下预加载全部机构数据到内存"""
        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y-%m-%d")

        if self._cache_date == as_of_date and self._dragon_cache is not None:
            return  # 已缓存

        # 解析日期
        try:
            end_date = datetime.strptime(as_of_date, "%Y-%m-%d")
        except ValueError:
            end_date = datetime.now()
        start_date = (end_date - timedelta(days=30)).strftime("%Y-%m-%d")

        # dragon_tiger_data: 近30日龙虎榜数据
        try:
            self._dragon_cache = read_sql(
                """
                SELECT code, trade_date, net_buy_wan, turnover_pct
                FROM dragon_tiger_data
                WHERE trade_date >= :start_date
                  AND trade_date <= :as_of
                """,
                self.engine,
                {"start_date": start_date, "as_of": as_of_date},
            )
            self._dragon_cache["code"] = self._dragon_cache["code"].astype(str).str.zfill(6)
        except Exception:
            self._dragon_cache = pd.DataFrame(
                columns=["code", "trade_date", "net_buy_wan", "turnover_pct"]
            )

        # margin_trading: 最新融资融券快照（取每条股票的最新日期）
        try:
            self._margin_cache = read_sql(
                """
                SELECT m.code, m.trade_date, m.rzye, m.rzmre, m.rzche, m.rqye
                FROM margin_trading m
                INNER JOIN (
                    SELECT code, MAX(trade_date) AS max_date
                    FROM margin_trading
                    GROUP BY code
                ) latest ON m.code = latest.code AND m.trade_date = latest.max_date
                """,
                self.engine,
            )
            self._margin_cache["code"] = self._margin_cache["code"].astype(str).str.zfill(6)
        except Exception:
            self._margin_cache = pd.DataFrame(
                columns=["code", "trade_date", "rzye", "rzmre", "rzche", "rqye"]
            )

        # shareholder_count: 最新股东户数快照
        try:
            self._holder_cache = read_sql(
                """
                SELECT s.code, s.end_date, s.holder_num, s.change_num, s.change_ratio, s.avg_shares
                FROM shareholder_count s
                INNER JOIN (
                    SELECT code, MAX(end_date) AS max_date
                    FROM shareholder_count
                    GROUP BY code
                ) latest ON s.code = latest.code AND s.end_date = latest.max_date
                """,
                self.engine,
            )
            self._holder_cache["code"] = self._holder_cache["code"].astype(str).str.zfill(6)
        except Exception:
            self._holder_cache = pd.DataFrame(
                columns=["code", "end_date", "holder_num", "change_num", "change_ratio", "avg_shares"]
            )

        self._cache_date = as_of_date

    def _get_dragon_data(self, code: str) -> dict:
        """从预加载缓存中获取单只股票的龙虎榜数据"""
        if self._dragon_cache is None or self._dragon_cache.empty:
            return {}
        stock_data = self._dragon_cache[self._dragon_cache["code"] == code]
        if stock_data.empty:
            return {}
        return {
            "net_buy_wan": float(stock_data["net_buy_wan"].sum()),
            "max_net_buy_wan": float(stock_data["net_buy_wan"].max()),
            "appear_days": int(stock_data["trade_date"].nunique()),
            "avg_turnover": float(stock_data["turnover_pct"].mean()),
        }

    def _get_margin_data(self, code: str) -> dict:
        """从预加载缓存中获取单只股票的融资融券数据"""
        if self._margin_cache is None or self._margin_cache.empty:
            return {}
        row = self._margin_cache[self._margin_cache["code"] == code]
        if row.empty:
            return {}
        r = row.iloc[0]
        return {
            "rzye": float(r.get("rzye") or 0),
            "rzmre": float(r.get("rzmre") or 0),
            "rzche": float(r.get("rzche") or 0),
            "rqye": float(r.get("rqye") or 0),
        }

    def _get_holder_data(self, code: str) -> dict:
        """从预加载缓存中获取单只股票的股东户数数据"""
        if self._holder_cache is None or self._holder_cache.empty:
            return {}
        row = self._holder_cache[self._holder_cache["code"] == code]
        if row.empty:
            return {}
        r = row.iloc[0]
        return {
            "holder_num": int(r.get("holder_num") or 0),
            "change_num": int(r.get("change_num") or 0),
            "change_ratio": float(r.get("change_ratio") or 0),
            "avg_shares": float(r.get("avg_shares") or 0),
            "end_date": str(r.get("end_date", ""))[:10],
        }

    # ============================================================
    #  评分逻辑
    # ============================================================

    def _score_dragon_net_buy(self, dragon_data: dict) -> int:
        """龙虎榜近30日净买入评分"""
        if not dragon_data:
            return 1
        net = dragon_data.get("net_buy_wan", 0)
        if net > 10_000:      # >1亿 (净买入 >1亿)
            return 3
        elif net > 2_000:     # >2000万
            return 2
        elif net > 0:
            return 1
        elif net > -2_000:    # 小幅净卖出
            return 0
        return 0

    def _score_chip_concentration(self, holder_data: dict) -> int:
        """筹码集中度 — 股东户数减少=筹码集中"""
        if not holder_data:
            return 1
        change = holder_data.get("change_ratio", 0)
        if change < -10:    # 减少>10% = 显著集中
            return 3
        elif change < -5:   # 减少5%~10%
            return 2
        elif change < 0:    # 减少0%~5%
            return 1
        return 0

    def _score_margin_sentiment(self, margin_data: dict) -> int:
        """融资情绪 — 基于融资净买入额(买入-偿还)"""
        if not margin_data:
            return 1
        buy = margin_data.get("rzmre", 0)
        repay = margin_data.get("rzche", 0)
        net = buy - repay
        if net > 50_000_000:     # 净买入>5000万
            return 3
        elif net > 10_000_000:   # 净买入>1000万
            return 2
        elif net > 0:
            return 1
        return 0

    def _score_dragon_activity(self, dragon_data: dict) -> int:
        """上榜活跃度 — 龙虎榜出现天数"""
        if not dragon_data:
            return 1
        days = dragon_data.get("appear_days", 0)
        if days >= 5:
            return 3
        elif days >= 3:
            return 2
        elif days >= 1:
            return 1
        return 0

    def _score_northbound_flow(self) -> int:
        """北向资金 — stub (后续下载北向数据)"""
        return 1

    def _score_chip_composite(self, subs: dict) -> int:
        parts = [subs.get(k, 1) for k in [
            "dragon_net_buy", "chip_concentration",
            "margin_sentiment", "dragon_activity"
        ]]
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

    def score(
        self, code: str, as_of_date: str | None = None
    ) -> dict[str, Any]:
        """
        单只股票评分 — 从DB读取数据。

        Args:
            code: 6位股票代码
            as_of_date: 基准日期 (YYYY-MM-DD)
        """
        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y-%m-%d")

        code = str(code).zfill(6)
        self._prefetch_all(as_of_date)

        dragon = self._get_dragon_data(code)
        margin = self._get_margin_data(code)
        holder = self._get_holder_data(code)

        sub_scores = {
            "dragon_net_buy": self._score_dragon_net_buy(dragon),
            "chip_concentration": self._score_chip_concentration(holder),
            "margin_sentiment": self._score_margin_sentiment(margin),
            "dragon_activity": self._score_dragon_activity(dragon),
            "northbound_flow": self._score_northbound_flow(),
        }
        sub_scores["chip_composite"] = self._score_chip_composite(sub_scores)

        total = sum(sub_scores.values())
        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": total,
            "weighted": round(total / 18 * 20, 1),  # 6维 × 3分 = 18 → 0-20
            "sub_scores": sub_scores,
            "raw_data": {
                "net_buy_wan": dragon.get("net_buy_wan", 0) if dragon else 0,
                "appear_days": dragon.get("appear_days", 0) if dragon else 0,
                "holder_change_ratio": holder.get("change_ratio", 0) if holder else 0,
                "margin_net": (margin.get("rzmre", 0) - margin.get("rzche", 0)) if margin else 0,
            },
            "error": None,
        }

    def batch_score(
        self, codes: list[str], as_of_date: str | None = None, verbose: bool = False
    ) -> "pd.DataFrame":
        """
        批量评分 — 全量数据从DB预加载，逐只内存评分。

        Args:
            codes: 股票代码列表
            as_of_date: 基准日期
            verbose: 是否打印进度
        """
        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y-%m-%d")

        # 一次性预加载全部数据
        self._prefetch_all(as_of_date)

        results = []
        for i, c in enumerate(codes):
            code = str(c).zfill(6)
            dragon = self._get_dragon_data(code)
            margin = self._get_margin_data(code)
            holder = self._get_holder_data(code)

            sub_scores = {
                "dragon_net_buy": self._score_dragon_net_buy(dragon),
                "chip_concentration": self._score_chip_concentration(holder),
                "margin_sentiment": self._score_margin_sentiment(margin),
                "dragon_activity": self._score_dragon_activity(dragon),
                "northbound_flow": self._score_northbound_flow(),
            }
            sub_scores["chip_composite"] = self._score_chip_composite(sub_scores)

            total = sum(sub_scores.values())
            results.append({
                "code": code,
                "as_of_date": as_of_date,
                "total": total,
                "weighted": round(total / 18 * 20, 1),
                **{f"inst_{k}": v for k, v in sub_scores.items()},
            })

            if verbose and (i + 1) % 500 == 0:
                print(f"  ... institutional {i + 1}/{len(codes)}")

        return pd.DataFrame(results)
