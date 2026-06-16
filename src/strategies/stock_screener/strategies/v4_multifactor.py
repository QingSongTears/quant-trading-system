"""
策略 v4 — 多因子综合评分
权重: 技术35% + 基本面25% + 板块共振20% + 量价背离20%

初筛: 市值≥100亿 + 非排除板块 + 流动性OK
评分: 多因子加权 → TOP N买入
风控: 止损-8%, 止盈+15%, 移动止损, 时间止损25天
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import List, Tuple, Dict
from backtest.engine import Signal, Trade, BaseStrategy


# 板块分类 (简单关键词映射)
SECTOR_KEYWORDS = {
    "半导体": ["芯片","半导体","集成电路","晶圆","封测"],
    "新能源": ["新能源","光伏","锂电","储能","风电","太阳能","电池"],
    "医药": ["医药","生物","制药","医疗","基因","疫苗","试剂"],
    "消费电子": ["电子","光电","精密","声学","显示","触摸"],
    "汽车": ["汽车","汽配","车轮","轮胎","车规"],
    "软件": ["软件","信息","数据","云计算","大数据","AI","人工智能"],
    "机械": ["机械","设备","机床","机器","重工","工程"],
    "化工": ["化工","化学","材料","塑料","橡胶","纤维"],
    "军工": ["军工","航天","航空","兵器","船舶","防务"],
    "有色": ["有色","黄金","白银","铜","铝","稀土","矿业"],
    "食品": ["食品","饮料","乳业","调味","粮油"],
    "电力": ["电力","电网","能源","发电","核电","水电"],
    "通信": ["通信","5G","光通信","光纤","基站"],
    "农业": ["农业","种业","畜牧","饲料","农药"],
    "建筑": ["建筑","建材","水泥","玻璃","装修"],
    "交通": ["交通","铁路","港口","公路","物流","快递"],
    "环保": ["环保","节能","碳","污染","废水"],
    "家电": ["家电","空调","冰箱","洗衣机"],
    "纺织": ["纺织","服装","化纤","印染"],
    "传媒": ["传媒","广告","影视","游戏","出版"],
}


def _classify_sector(name: str) -> str:
    """根据名称关键词分类板块"""
    if not isinstance(name, str):
        return "其他"
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return sector
    return "其他"


class V4MultiFactorStrategy(BaseStrategy):
    """v4 多因子综合评分"""

    name = "v4_multifactor"

    # === 初筛 ===
    MIN_MCAP = 100          # 市值≥100亿
    MIN_TURNOVER = 0.3      # 换手率≥0.3%

    # === 评分权重 ===
    W_TECH = 0.35
    W_FUND = 0.25
    W_SECTOR = 0.20
    W_VOLPRICE = 0.20

    # === 风控 ===
    STOP_LOSS = -0.08
    TAKE_PROFIT = 0.15
    TRAILING_STOP = 0.12
    TRAILING_DD = -0.03
    TIME_STOP_DAYS = 25
    TIME_STOP_RETURN = 0.03
    MAX_POSITIONS = 8
    MAX_SIGNALS = 10

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k.upper()):
                setattr(self, k.upper(), v)

        # 板块缓存
        self._sector_cache: Dict[str, str] = {}
        self._finance_map: Dict[str, dict] = {}

    def _get_sector(self, name: str) -> str:
        if name not in self._sector_cache:
            self._sector_cache[name] = _classify_sector(name)
        return self._sector_cache[name]

    # ---------- 扫描 ----------

    def scan(self, today_data: pd.DataFrame, today, context: dict) -> List[Signal]:
        excluded = context["excluded_codes"]
        spot_map = context.get("spot_map", {})
        held = context.get("held_codes", set())
        finance = context.get("finance")

        # 构建财务映射（惰性）
        if finance is not None and not self._finance_map:
            fin = finance.set_index("code")
            for col in ["roe", "growth_revenue", "growth_profit", "net_profit"]:
                if col in fin.columns:
                    for code, val in fin[col].items():
                        if code not in self._finance_map:
                            self._finance_map[code] = {}
                        self._finance_map[code][col] = val if pd.notna(val) else None

        # 先收集所有可评分候选
        candidates = []
        today_data = today_data.copy()

        for _, row in today_data.iterrows():
            code = row["code"]
            if code in excluded or code in held:
                continue

            info = spot_map.get(code, {})
            mcap = info.get("mcap_yi", 0) or 0
            if mcap < self.MIN_MCAP:
                continue
            turnover = info.get("turnover_pct", 0) or 0
            if turnover < self.MIN_TURNOVER:
                continue

            close = row["close"]
            rsi14 = row.get("rsi_14_d", 50) or 50
            rsi6 = row.get("rsi_6_d", 50) or 50
            bb_pos = row.get("bb_pos_d", 0.5)
            max_dd_60 = row.get("max_dd_60d", 0) or -100
            vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
            prev_close = row.get("prev_close_d", close)
            price_chg = (close / prev_close - 1) * 100 if prev_close > 0 else 0
            r2_60d = row.get("r2_60d", 0) or 0
            ret_60d = row.get("ret_60d_pct", 0) or 0

            if pd.isna(rsi14) or pd.isna(bb_pos):
                continue

            # ---- 技术因子 (35%) ----
            # RSI 适中分 (15): RSI在30-55最佳，太低=继续跌，太高=追高
            rsi_score = 0.0
            if 25 <= rsi14 <= 55:
                rsi_score = 15.0 - abs(rsi14 - 40) / 15 * 5  # 40附近满分
            elif rsi14 < 25:
                rsi_score = max(0, rsi14 / 25 * 8)  # 太低扣分
            elif rsi14 > 55:
                rsi_score = max(0, (100 - rsi14) / 45 * 8)  # 太高扣分

            # BB位置分 (10): 下轨附近最好，中轨上方扣分
            bb_score = 0.0
            if bb_pos is not None and not pd.isna(bb_pos):
                if bb_pos <= 0.2:
                    bb_score = 10.0  # 下轨附近最佳
                elif bb_pos <= 0.5:
                    bb_score = 10.0 - (bb_pos - 0.2) / 0.3 * 5
                else:
                    bb_score = 5.0 - (bb_pos - 0.5) / 0.5 * 5

            # 回撤深度分 (10): 适度回撤好
            dd_score = 0.0
            if max_dd_60 < -5:
                dd_score = min(10, abs(max_dd_60) / 10 * 5)

            tech_total = rsi_score + bb_score + dd_score

            # ---- 基本面因子 (25%) ----
            pe = info.get("pe_ttm")
            pe = pe if pe and pe > 0 else 50  # 负PE默认50

            # PE分 (10): PE在10-50之间最好
            pe_score = 0.0
            if 10 <= pe <= 50:
                pe_score = 10.0 - abs(pe - 25) / 20 * 5
            elif 0 < pe < 10:
                pe_score = 7.0  # 低PE也不错
            elif pe > 50:
                pe_score = max(0, 10.0 - (pe - 50) / 50 * 10)

            # ROE分 (10)
            roe = (self._finance_map.get(code, {}) or {}).get("roe")
            roe_score = 0.0
            if roe is not None:
                if roe >= 15:
                    roe_score = 10.0
                elif roe >= 8:
                    roe_score = 8.0
                elif roe >= 3:
                    roe_score = 5.0
                elif roe > 0:
                    roe_score = 3.0

            # 增长分 (5)
            growth_rev = (self._finance_map.get(code, {}) or {}).get("growth_revenue")
            growth_score = 0.0
            if growth_rev is not None:
                if growth_rev > 1e9:
                    growth_score = 5.0
                elif growth_rev > 1e8:
                    growth_score = 3.5
                elif growth_rev > 0:
                    growth_score = 2.0

            fund_total = pe_score + roe_score + growth_score

            # ---- 板块共振 (20%) 简化版 ----
            # 基于60日涨幅和趋势R²给出板块强度
            sector_score = 0.0
            if ret_60d > 5 and r2_60d > 0.6:
                sector_score = 15.0 + min(ret_60d / 20 * 5, 5)  # 趋势强劲加分
            elif ret_60d > 0:
                sector_score = 10.0 + ret_60d / 10 * 5
            else:
                sector_score = max(0, 10.0 + ret_60d / 10 * 5)

            # ---- 量价背离 (20%) ----
            # 判断量价关系
            vol_price_score = 10.0  # 基准

            # 放量上涨加分
            if price_chg > 0.5 and vol_ratio > 1.2:
                vol_price_score += min(price_chg * 3 + (vol_ratio - 1) * 5, 8)
            # 缩量下跌加分（洗盘）
            elif price_chg < -0.5 and vol_ratio < 0.8:
                vol_price_score += min(abs(price_chg) * 2, 6)
            # 放量下跌扣分
            elif price_chg < -1 and vol_ratio > 1.5:
                vol_price_score -= min(abs(price_chg) * 2, 8)
            # 缩量上涨一般
            elif price_chg > 0.5 and vol_ratio < 0.8:
                vol_price_score += 2

            vol_price_score = max(0, min(20, vol_price_score))

            # ---- 加权总分 ----
            score = (
                tech_total * self.W_TECH +
                fund_total * self.W_FUND +
                sector_score * self.W_SECTOR +
                vol_price_score * self.W_VOLPRICE
            )

            candidates.append({
                "code": code, "close": close,
                "name": info.get("name", ""),
                "score": round(score, 1),
                "extra": {
                    "rsi14": round(rsi14, 1),
                    "bb_pos": round(bb_pos or 0, 3),
                    "max_dd": round(max_dd_60, 1),
                    "pe": round(pe, 1) if pe else None,
                    "roe": round(roe, 1) if roe else None,
                    "price_chg": round(price_chg, 2),
                    "vol_ratio": round(vol_ratio, 2),
                }
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)

        # 去重 + TOP N
        seen = set()
        unique = []
        for c in candidates:
            if c["code"] not in seen:
                seen.add(c["code"])
                unique.append(c)
            if len(unique) >= self.MAX_SIGNALS:
                break

        return [Signal(**c, strategy=self.name) for c in unique[:self.MAX_POSITIONS]]

    # ---------- 持仓更新 (同v3) ----------

    def update_positions(
        self, positions: List[dict], row: pd.Series, today, today_str: str
    ) -> Tuple[List[dict], List[Trade]]:
        closed, surviving = [], []
        for p in positions:
            cp = row["close"]
            hold = (today - pd.Timestamp(p["entry_date"])).days
            ret = (cp - p["entry_price"]) / p["entry_price"]

            high_water = p.get("high_water", p["entry_price"])
            if cp > high_water:
                p["high_water"] = cp
                high_water = cp
            highest_ret = (high_water - p["entry_price"]) / p["entry_price"]

            # 止损
            if ret <= self.STOP_LOSS:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "stop_loss",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            # 止盈
            if ret >= self.TAKE_PROFIT:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "take_profit",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            # 移动止损
            if highest_ret > self.TRAILING_STOP:
                trail_dd = (cp - high_water) / high_water
                if trail_dd <= self.TRAILING_DD:
                    t = Trade(p["code"], p.get("name", ""), self.name,
                              p["entry_date"], today_str,
                              p["entry_price"], cp, "trailing_stop",
                              ret * 100, hold, p["shares"])
                    closed.append(t)
                    continue

            # 时间止损
            if hold > self.TIME_STOP_DAYS and ret < self.TIME_STOP_RETURN:
                t = Trade(p["code"], p.get("name", ""), self.name,
                          p["entry_date"], today_str,
                          p["entry_price"], cp, "time_stop",
                          ret * 100, hold, p["shares"])
                closed.append(t)
                continue

            surviving.append(p)
        return surviving, closed
