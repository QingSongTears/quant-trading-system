"""
机构持仓/筹码集中度评分器 (权重 10%)
=====================================

基于 A-stock-data 技能 API 的机构行为与筹码分析。

数据源:
  - 龙虎榜席位 (§3.5): 机构专用席位买卖明细
  - 融资融券 (§4.1): 融资余额变化 (杠杆资金情绪)
  - 股东户数 (§4.3): 季度股东户数变化 (筹码集中/分散)
  - 大宗交易 (§4.2): 大宗交易折溢价

子指标 (每项 0-3 分):
  1. 机构净买入   (0-3): 龙虎榜机构席位净买入
  2. 筹码集中度   (0-3): 股东户数环比减少=集中 (好)
  3. 融资情绪     (0-3): 融资余额变化方向
  4. 大宗折溢价   (0-3): 大宗交易是折价还是溢价
  5. 机构持续买入 (0-3): 机构是否连续出现在龙虎榜
  6. 北向资金     (0-3): 北向资金持仓变化 (需 §3.2)
  7. 筹码综合     (0-3): 综合筹码评分
"""
import numpy as np
import pandas as pd
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

# 东财 datacenter API 基础地址
_DATACENTER_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DEFAULT_TIMEOUT = 15


class InstitutionalScorer:
    """机构持仓评分器 — API驱动"""

    def __init__(self, engine=None):
        # engine 参数保持接口统一，机构面通过 HTTP API 获取数据
        self._cache = {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://data.eastmoney.com/',
        })

    # ============================================================
    #  通用 API 调用
    # ============================================================
    def _call_datacenter_api(
        self,
        report_name: str,
        extra_params: Optional[Dict[str, Any]] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> List[dict]:
        """
        通用东财 datacenter API 调用。
        返回 result.data 列表, 异常时返回空列表。
        """
        params = {
            "source": "WEB",
            "client": "WEB",
            "pageNumber": 1,
            "pageSize": 500,
            "reportName": report_name,
            "columns": "ALL",
        }
        if extra_params:
            params.update(extra_params)

        try:
            resp = self.session.get(
                _DATACENTER_BASE, params=params, timeout=timeout
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                return []
            result = data.get("result")
            if result is None:
                return []
            return result.get("data") or []
        except Exception:
            return []

    # ============================================================
    #  API 端点
    # ============================================================
    def _fetch_lhb_institutional(
        self, code: str, trade_date: Optional[str] = None
    ) -> dict:
        """
        龙虎榜机构席位 (§3.5)

        通过买入/卖出席位明细 API 识别机构专用席位:
          1. RPT_BILLBOARD_DAILYDETAILSBUY  — 买入席位明细 (含机构)
          2. RPT_BILLBOARD_DAILYDETAILSSELL — 卖出席位明细 (含机构)

        机构识别: OPERATEDEPT_CODE="0" = 机构专用席位

        Returns:
            {
                "inst_buy": float,         # 机构买入总额
                "inst_sell": float,        # 机构卖出总额
                "inst_appear_days": int,   # 机构出现天数
                "buy_records": list,       # 机构买入记录
                "sell_records": list,      # 机构卖出记录
            }
        """
        if trade_date:
            end_date = trade_date
        else:
            end_date = datetime.now().strftime("%Y-%m-%d")

        try:
            start_date = (
                datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=30)
            ).strftime("%Y-%m-%d")
        except ValueError:
            start_date = (
                datetime.now() - timedelta(days=30)
            ).strftime("%Y-%m-%d")
            end_date = datetime.now().strftime("%Y-%m-%d")

        # 注意: 东财 filter 中, 字符串值用双引号, 日期值用单引号
        inst_filter = (
            f'(SECURITY_CODE="{code}")'
            f"(TRADE_DATE>='{start_date}')"
            f"(TRADE_DATE<='{end_date}')"
            f'(OPERATEDEPT_CODE="0")'
        )
        common_params = {
            "filter": inst_filter,
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
        }

        # 1. 机构买入席位
        buy_records = self._call_datacenter_api(
            "RPT_BILLBOARD_DAILYDETAILSBUY", common_params
        )
        inst_buy_total = 0.0
        inst_days = set()

        for rec in buy_records:
            try:
                inst_buy_total += float(rec.get("BUY") or 0)
            except (TypeError, ValueError):
                pass
            td = str(rec.get("TRADE_DATE", ""))[:10]
            if td:
                inst_days.add(td)

        # 2. 机构卖出席位
        sell_records = self._call_datacenter_api(
            "RPT_BILLBOARD_DAILYDETAILSSELL", common_params
        )
        inst_sell_total = 0.0

        for rec in sell_records:
            try:
                inst_sell_total += float(rec.get("SELL") or 0)
            except (TypeError, ValueError):
                pass
            td = str(rec.get("TRADE_DATE", ""))[:10]
            if td:
                inst_days.add(td)

        return {
            "inst_buy": inst_buy_total,
            "inst_sell": inst_sell_total,
            "inst_appear_days": len(inst_days),
            "buy_records": buy_records,
            "sell_records": sell_records,
        }

    def _fetch_margin_balance(self, code: str) -> dict:
        """
        融资融券 (§4.1)

        reportName: RPTA_WEB_RZRQ_GGMX
        filter: (SCODE="{code}")

        核心字段:
          RZYE   — 融资余额
          RZMRE  — 融资买入额
          RZCHE  — 融资偿还额
          RZJME  — 融资净买入额
          RQYE   — 融券余额
          RZRQYE — 融资融券余额合计

        Returns:
            {
                "balance_change_pct": float,   # 最新两日融资余额变化率(%)
                "latest_balance": float,       # 最新融资余额
                "net_buy_amount": float,       # 最新融资净买入额
                "raw": list,
            }
        """
        params = {
            "filter": f'(SCODE="{code}")',
            "sortColumns": "DATE",
            "sortTypes": "-1",
            "pageSize": 5,
        }
        data = self._call_datacenter_api("RPTA_WEB_RZRQ_GGMX", params)

        if not data:
            return {
                "balance_change_pct": 0,
                "latest_balance": 0,
                "net_buy_amount": 0,
                "raw": [],
            }

        try:
            latest = data[0]
            cur_balance = float(latest.get("RZYE") or 0)
            net_buy = float(latest.get("RZJME") or 0)

            if len(data) >= 2:
                prev = data[1]
                prev_balance = float(prev.get("RZYE") or 0)
                if prev_balance > 0:
                    change_pct = (
                        (cur_balance - prev_balance) / prev_balance * 100
                    )
                else:
                    change_pct = 0
            else:
                change_pct = 0
        except (TypeError, ValueError, IndexError):
            cur_balance = 0
            change_pct = 0
            net_buy = 0

        return {
            "balance_change_pct": round(change_pct, 2),
            "latest_balance": cur_balance,
            "net_buy_amount": net_buy,
            "raw": data,
        }

    def _fetch_shareholder_count(self, code: str) -> dict:
        """
        股东户数变化 (§4.3)

        reportName: RPT_HOLDERNUMLATEST
        filter: (SECURITY_CODE="{code}")

        核心字段:
          HOLDER_NUM        — 最新股东户数
          PRE_HOLDER_NUM    — 上期股东户数
          HOLDER_NUM_CHANGE — 股东户数变化量
          HOLDER_NUM_RATIO  — 股东户数变化率(%)
          END_DATE          — 截止日期

        核心逻辑: 股东户数减少(HOLDER_NUM_RATIO<0) = 筹码集中 = 主力吸筹

        Returns:
            {
                "holder_change_pct": float,   # 股东户数变化率(%)，负=筹码集中
                "latest_count": int,          # 最新股东户数
                "end_date": str,              # 截止日期
                "raw": list,
            }
        """
        params = {
            "filter": f'(SECURITY_CODE="{code}")',
            "sortColumns": "END_DATE",
            "sortTypes": "-1",
            "pageSize": 5,
        }
        data = self._call_datacenter_api("RPT_HOLDERNUMLATEST", params)

        if not data:
            return {
                "holder_change_pct": 0,
                "latest_count": 0,
                "end_date": "",
                "raw": [],
            }

        try:
            # HOLDER_NUM_RATIO 即为环比变化率(%)
            ratio = data[0].get("HOLDER_NUM_RATIO")
            if ratio is not None:
                change_pct = float(ratio)
            else:
                # Fallback: 对比最近两期
                cur_num = float(data[0].get("HOLDER_NUM") or 0)
                prev_num = float(data[0].get("PRE_HOLDER_NUM") or 0)
                if prev_num > 0:
                    change_pct = (cur_num - prev_num) / prev_num * 100
                else:
                    change_pct = 0

            latest_count = int(data[0].get("HOLDER_NUM") or 0)
            end_date = str(data[0].get("END_DATE", ""))[:10]
        except (TypeError, ValueError, IndexError):
            change_pct = 0
            latest_count = 0
            end_date = ""

        return {
            "holder_change_pct": round(change_pct, 2),
            "latest_count": latest_count,
            "end_date": end_date,
            "raw": data,
        }

    def _fetch_block_trades(self, code: str) -> dict:
        """
        大宗交易 (§4.2)
        ⚠️ 当前为 stub — 大宗交易 API 需额外实现
        """
        return {}

    # ============================================================
    #  评分逻辑
    # ============================================================
    def _score_institutional_buy(self, lhb_data: dict) -> int:
        """机构净买入"""
        if not lhb_data:
            return 1
        buy = lhb_data.get("inst_buy", 0)
        sell = lhb_data.get("inst_sell", 0)
        net = buy - sell
        if net > 100_000_000:  # >1亿
            return 3
        elif net > 10_000_000:  # >1000万
            return 2
        elif net > 0:
            return 1
        return 0

    def _score_chip_concentration(self, holder_data: dict) -> int:
        """筹码集中度 — 股东户数减少=集中"""
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
        """融资情绪"""
        if not margin_data:
            return 1
        change = margin_data.get("balance_change_pct", 0)
        if change > 5:
            return 3
        elif change > 2:
            return 2
        elif change > 0:
            return 1
        return 0

    def _score_block_trade_premium(self, block_data: dict) -> int:
        """大宗交易溢价=看好"""
        if not block_data:
            return 1
        premium = block_data.get("avg_premium_pct", 0)
        if premium > 5:
            return 3
        elif premium > 0:
            return 2
        elif premium > -5:
            return 1
        return 0

    def _score_institutional_persistence(self, lhb_data: dict) -> int:
        """机构持续出现"""
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
        """北向资金 — 需 §3.2 API"""
        return 1  # stub

    def _score_chip_composite(self, subs: dict) -> int:
        parts = [subs.get(k, 1) for k in [
            "institutional_buy", "chip_concentration",
            "margin_sentiment", "block_trade_premium",
            "institutional_persistence"
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
        # Fetch API data
        lhb = self._fetch_lhb_institutional(code, as_of_date)
        margin = self._fetch_margin_balance(code)
        holder = self._fetch_shareholder_count(code)
        block = self._fetch_block_trades(code)

        sub_scores = {
            "institutional_buy": self._score_institutional_buy(lhb),
            "chip_concentration": self._score_chip_concentration(holder),
            "margin_sentiment": self._score_margin_sentiment(margin),
            "block_trade_premium": self._score_block_trade_premium(block),
            "institutional_persistence": self._score_institutional_persistence(lhb),
            "northbound_flow": self._score_northbound_flow(),
        }
        sub_scores["chip_composite"] = self._score_chip_composite(sub_scores)

        total = sum(sub_scores.values())
        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": total,
            "weighted": round(total / 21 * 20, 1),
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
                    **{f"inst_{k}": v for k, v in r["sub_scores"].items()},
                })
            if verbose and (i + 1) % 50 == 0:
                print(f"  ... institutional {i + 1}/{len(codes)}")
        return pd.DataFrame(results)
