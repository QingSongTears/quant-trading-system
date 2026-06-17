"""
机构持仓/筹码集中度评分器 v2 — DB驱动 (权重 10%)
=================================================

v1 (API驱动): 逐只HTTP调用, 5000只=10000+次请求, 批量模式被迫用默认分 ❌
v2 (DB驱动):  全量数据存入SQLite, 批量评分从DB读取, 秒级完成 ✅

数据源 (DB表):
  - lhb_institutional: 龙虎榜机构专用席位买卖汇总 (5,054条, 1,825只股票)
  - margin_trading:    融资融券最新快照 (4,370条, 4,370只股票)
  - shareholder_count: 股东户数最新季度快照 (5,521条, 5,521只股票)

子指标 (每项 0-3 分, 满分 18, 归一化到 0-20):
  1. 机构净买入   (0-3): 龙虎榜机构席位近30日净买入额
  2. 筹码集中度   (0-3): 股东户数环比变化率 (负=集中=好)
  3. 融资情绪     (0-3): 融资净买入额
  4. 机构活跃度   (0-3): 机构出现天数
  5. 筹码综合     (0-3): 综合筹码评分
  6. 北向资金     (0-3): 北向资金 (stub, 后续下载)
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from ..config import get_config, get_db_url


class InstitutionalScorer:
    """机构持仓评分器 v2 — DB驱动"""

    def __init__(self, engine=None):
        if engine is None:
            config = get_config()
            db_url = get_db_url(config)
            self.engine = create_engine(db_url, echo=False)
        else:
            self.engine = engine

        # 批量预加载的数据缓存
        self._lhb_cache: Optional[pd.DataFrame] = None
        self._margin_cache: Optional[pd.DataFrame] = None
        self._holder_cache: Optional[pd.DataFrame] = None
        self._cache_date: Optional[str] = None

    # ============================================================
    #  DB 数据加载 (替代API调用)
    # ============================================================

    def _prefetch_all(self, as_of_date: Optional[str] = None):
        """批量模式下预加载全部机构数据到内存"""
        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y-%m-%d")

        if self._cache_date == as_of_date and self._lhb_cache is not None:
            return  # 已缓存

        # LHB: 近30日机构席位数据
        try:
            end_date = datetime.strptime(as_of_date, "%Y-%m-%d")
        except ValueError:
            end_date = datetime.now()
        start_date = (end_date - timedelta(days=30)).strftime("%Y-%m-%d")

        try:
            self._lhb_cache = pd.read_sql(
                f"""
                SELECT code, trade_date, inst_buy, inst_sell, inst_net
                FROM lhb_institutional
                WHERE trade_date >= '{start_date}'
                  AND trade_date <= '{as_of_date}'
                """,
                self.engine,
            )
        except Exception:
            self._lhb_cache = pd.DataFrame(
                columns=["code", "trade_date", "inst_buy", "inst_sell", "inst_net"]
            )

        # Margin: 最新快照
        try:
            self._margin_cache = pd.read_sql(
                "SELECT code, date, rzye, rzmre, rzche, rzjme, rqye, rzrqye FROM margin_trading",
                self.engine,
            )
            self._margin_cache["code"] = self._margin_cache["code"].astype(str).str.zfill(6)
        except Exception:
            self._margin_cache = pd.DataFrame(
                columns=["code", "date", "rzye", "rzmre", "rzche", "rzjme", "rqye", "rzrqye"]
            )

        # Shareholder: 最新快照
        try:
            self._holder_cache = pd.read_sql(
                "SELECT code, end_date, holder_num, pre_holder_num, holder_change_pct, avg_holding FROM shareholder_count",
                self.engine,
            )
            self._holder_cache["code"] = self._holder_cache["code"].astype(str).str.zfill(6)
        except Exception:
            self._holder_cache = pd.DataFrame(
                columns=["code", "end_date", "holder_num", "pre_holder_num", "holder_change_pct", "avg_holding"]
            )

        self._cache_date = as_of_date

    def _get_lhb_data(self, code: str) -> dict:
        """从预加载缓存中获取单只股票的LHB数据"""
        if self._lhb_cache is None or self._lhb_cache.empty:
            return {}
        stock_lhb = self._lhb_cache[self._lhb_cache["code"] == code]
        if stock_lhb.empty:
            return {}
        return {
            "inst_buy": stock_lhb["inst_buy"].sum(),
            "inst_sell": stock_lhb["inst_sell"].sum(),
            "inst_appear_days": stock_lhb["trade_date"].nunique(),
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
            "rzjme": float(r.get("rzjme") or 0),
            "rqye": float(r.get("rqye") or 0),
            "rzrqye": float(r.get("rzrqye") or 0),
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
            "holder_change_pct": float(r.get("holder_change_pct") or 0),
            "latest_count": int(r.get("holder_num") or 0),
            "end_date": str(r.get("end_date", ""))[:10],
        }

    # ============================================================
    #  评分逻辑 (与 v1 相同)
    # ============================================================

    def _score_institutional_buy(self, lhb_data: dict) -> int:
        """机构近30日净买入"""
        if not lhb_data:
            return 1
        buy = lhb_data.get("inst_buy", 0)
        sell = lhb_data.get("inst_sell", 0)
        net = buy - sell
        if net > 100_000_000:   # >1亿
            return 3
        elif net > 10_000_000:  # >1000万
            return 2
        elif net > 0:
            return 1
        return 0

    def _score_chip_concentration(self, holder_data: dict) -> int:
        """筹码集中度 — 股东户数减少=筹码集中"""
        if not holder_data:
            return 1
        change = holder_data.get("holder_change_pct", 0)
        if change < -10:  # 减少>10% = 显著集中
            return 3
        elif change < -5:
            return 2
        elif change < 0:
            return 1
        return 0

    def _score_margin_sentiment(self, margin_data: dict) -> int:
        """融资情绪 — 基于融资净买入额"""
        if not margin_data:
            return 1
        net_buy = margin_data.get("rzjme", 0)
        if net_buy > 50_000_000:    # >5000万
            return 3
        elif net_buy > 10_000_000:  # >1000万
            return 2
        elif net_buy > 0:
            return 1
        return 0

    def _score_institutional_activity(self, lhb_data: dict) -> int:
        """机构活跃度 — 机构出现天数"""
        if not lhb_data:
            return 1
        days = lhb_data.get("inst_appear_days", 0)
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
            "institutional_buy", "chip_concentration",
            "margin_sentiment", "institutional_activity"
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
        self, code: str, as_of_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        单只股票评分 — 从DB读取数据。

        Args:
            code: 6位股票代码
            as_of_date: 基准日期
        """
        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y-%m-%d")

        # 确保数据已预加载
        self._prefetch_all(as_of_date)

        lhb = self._get_lhb_data(code)
        margin = self._get_margin_data(code)
        holder = self._get_holder_data(code)

        sub_scores = {
            "institutional_buy": self._score_institutional_buy(lhb),
            "chip_concentration": self._score_chip_concentration(holder),
            "margin_sentiment": self._score_margin_sentiment(margin),
            "institutional_activity": self._score_institutional_activity(lhb),
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
            "error": None,
        }

    def batch_score(
        self, codes: List[str], as_of_date: Optional[str] = None, verbose: bool = False
    ) -> "pd.DataFrame":
        """
        批量评分 — 全量数据从DB预加载，逐只内存评分。

        Args:
            codes: 股票代码列表
            as_of_date: 基准日期
            verbose: 是否打印进度
        """
        import pandas as pd

        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y-%m-%d")

        # 一次性预加载全部机构数据
        self._prefetch_all(as_of_date)

        results = []
        for i, code in enumerate(codes):
            lhb = self._get_lhb_data(code)
            margin = self._get_margin_data(code)
            holder = self._get_holder_data(code)

            sub_scores = {
                "institutional_buy": self._score_institutional_buy(lhb),
                "chip_concentration": self._score_chip_concentration(holder),
                "margin_sentiment": self._score_margin_sentiment(margin),
                "institutional_activity": self._score_institutional_activity(lhb),
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

    # ============================================================
    #  保留单只API方法作为 fallback (当 DB 无数据时)
    # ============================================================
    # 以下方法在 DB 数据缺失时可通过 HTTP API 补充，当前版本 DB 数据已覆盖。
