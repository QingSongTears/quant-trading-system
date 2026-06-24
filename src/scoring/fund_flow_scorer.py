"""
资金面评分器 v2.1 — 持续吸筹模型 (权重 20%)
===========================================

版本演进:
  v1 (七维资金流): 主力流入=好, 大单=好, 背离=好 → 总分负相关(-0.059) ❌
  v2 (持续吸筹): 持续流入+稳定低波动→好, 抛弃单日流入/背离/中单反向 ✅
  v2.1: 移除 flow_reversal (-0.031) → 6维度聚焦"持续+稳定"

核心洞察 (2,137样本 × 资金面子指标归因):
  - consecutive_inflow +0.035: 唯一正向维度, 持续>单日
  - flow_intensity -0.022: 绝对金额大≠好, 可能是出货放量
  - medium_contrarian -0.039: 中单流出+主力流入≠吸筹, 可能是对倒
  - flow_divergence -0.003: 价跌+资金流入≠背离吸筹
  - 单日主力净流入追入=接飞刀, 连续5日+才是真正吸筹

v2.1 设计理念:
  追寻"持续稳定的主力吸筹"而非单日异动。
  移除 flow_reversal (流出反转, 相关性-0.031) — 反转信号在技术面v3已充分覆盖,
  资金面聚焦于"持续"和"稳定"两个核心维度。

6个子指标 (每项 0-3 分，满分 18，归一化到 0-20):
  1. 持续流入   (0-3): 连续主力净流入天数 (核心正向 +0.035)
  2. 流入稳定性 (0-3): 近10日主力净流入的日间波动 (低波动=稳定吸筹 +0.023)
  3. 正向占比   (0-3): 近10日主力净流入的阳线天数占比 (最强 +0.046)
  4. 智能资金   (0-3): 超大单流入+大单流出 (机构vs游资)
  5. 流入加速度 (0-3): 近5日 vs 近20日均流入对比
  6. 相对规模   (0-3): 主力净流入/流通市值 (替代绝对金额)

用法:
    scorer = FundFlowScorer()
    result = scorer.score(code="000001", as_of_date="2025-12-15")
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text  # PR3.2: create_engine 由 base.py 通过 get_engine 单例提供, text

from ..config import get_config, get_db_url
from ..db.sql_utils import read_sql
from .base import BaseScorer


class FundFlowScorer(BaseScorer):
    """资金面评分器 v2 — 持续吸筹模型"""

    name = "fund_flow"
    label_zh = "资金面"
    weight = 0.20
    max_raw = 18

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self._data_available = self._check_data()

    def _check_data(self) -> bool:
        """检查 fund_flow_data 表是否有数据"""
        try:
            cnt = read_sql(
                "SELECT COUNT(*) as n FROM fund_flow_data", self.engine
            ).iloc[0, 0]
            if cnt == 0:
                print("[WARN] FundFlowScorer: fund_flow_data 表为空，"
                      "评分将返回 0。待 #65 导入资金流数据。")
            return cnt > 0
        except Exception:
            return False

    def _load_flow_data(
        self, code: str, as_of_date_str: str, lookback: int = 30
    ) -> pd.DataFrame:
        sql = """
            SELECT trade_date, main_net, super_large_net, large_net,
                   medium_net, small_net
            FROM fund_flow_data
            WHERE code = :code
              AND trade_date <= :as_of
            ORDER BY trade_date DESC
            LIMIT :lookback
        """
        df = read_sql(sql, self.engine, {
            "code": code,
            "as_of": as_of_date_str,
            "lookback": int(lookback),
        })
        if df.empty:
            return df
        # 数据源(东方财富)对停牌/无成交股票返回 '-',需转 NaN
        for col in ["main_net", "super_large_net", "large_net", "medium_net", "small_net"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    def _load_market_cap(self, code: str) -> float:
        """获取流通市值 — 优先级: DB > WeStock CLI > 绝对金额兜底
        
        1. 先查 stock_profile.circulating_shares (可从WeStock批量采集)
        2. 若没有, 通过 WeStock Data CLI 实时获取 regCapital
        3. 都失败, 返回 NaN (触发绝对金额判断)
        """
        try:
            # 1. 查 DB
            df = read_sql(
                "SELECT circulating_shares FROM stock_profile WHERE code = :code",
                self.engine, {"code": code}
            )
            if not df.empty:
                shares = df.iloc[0, 0]
                if pd.notna(shares) and shares > 0:
                    return float(shares)
        except Exception:
            pass
        
        # 2. 通过 WeStock Data CLI 实时获取
        try:
            import subprocess as _sp
            # 转换代码格式
            if code.startswith("6"):
                wscode = "sh" + code
            elif code.startswith("0") or code.startswith("3"):
                wscode = "sz" + code
            elif code.startswith("8") or code.startswith("4"):
                wscode = "bj" + code
            else:
                return np.nan
            
            result = _sp.run(
                ["npx", "-y", "westock-data-clawhub@1.0.4", "profile", wscode],
                capture_output=True, text=True, timeout=20,
                env={**__import__('os').environ, "NODE_OPTIONS": ""},
                # Windows: 隐藏 npx.cmd 弹出的黑色 cmd 窗口
                creationflags=0x08000000 if __import__('sys').platform == "win32" else 0,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) >= 3:
                    headers = [h.strip() for h in lines[0].split("|")[1:-1]]
                    values = [v.strip() for v in lines[2].split("|")[1:-1]]
                    data = dict(zip(headers, values))
                    reg_capital_wan = data.get("regCapital", "")
                    if reg_capital_wan:
                        shares = float(reg_capital_wan.replace(",", "")) * 10000  # 万元→元
                        # 缓存到 DB
                        try:
                            with self.engine.connect() as _c:
                                _c.execute(
                                    text("UPDATE stock_profile SET circulating_shares = :s WHERE code = :c"),
                                    {"s": shares, "c": code}
                                )
                                _c.commit()
                        except Exception:
                            pass
                        return shares
        except Exception:
            pass
        
        return np.nan

    def _load_bulk_flow_data(
        self, codes: list[str], as_of_date_str: str, lookback: int = 30
    ) -> dict[str, pd.DataFrame]:
        if not codes:
            return {}
        sql = """
            SELECT code, trade_date, main_net, super_large_net, large_net,
                   medium_net, small_net
            FROM fund_flow_data
            WHERE code IN :codes
              AND trade_date <= :as_of
            ORDER BY code, trade_date DESC
        """
        df = read_sql(sql, self.engine, {
            "codes": list(codes),
            "as_of": as_of_date_str,
        })
        if df.empty:
            return {}

        # 数据源对停牌股返回 '-',需转 NaN
        for col in ["main_net", "super_large_net", "large_net", "medium_net", "small_net"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        result = {}
        for code, group in df.groupby("code"):
            group = group.sort_values("trade_date").reset_index(drop=True)
            if len(group) >= 5:
                result[code] = group.tail(lookback)
        return result

    # ============================================================
    #  1. 持续流入 (0-3) — v1唯一正向维度, v2强化极端值
    # ============================================================
    def _score_persistent_inflow(self, df: pd.DataFrame) -> int:
        """
        连续主力净流入天数。

        实证: +0.035 相关(60d), 是v1唯一正向维度。
        v2强化: 连续5+天 = 3分, 按tech v3"极端>温和"逻辑。

        3分: 连续 ≥5 天 (持续吸筹, 高度确定)
        2分: 连续 3-4 天
        1分: 连续 1-2 天
        0分: 当日净流出
        """
        if df.empty:
            return 0

        main = df["main_net"].values.astype(np.float64)
        consecutive = 0
        for val in reversed(main):
            if val > 0:
                consecutive += 1
            else:
                break

        if consecutive >= 5:
            return 3
        elif consecutive >= 3:
            return 2
        elif consecutive >= 1:
            return 1
        return 0

    # ============================================================
    #  2. 流入稳定性 (0-3) — NEW: 低波动=稳定吸筹
    # ============================================================
    def _score_flow_stability(self, df: pd.DataFrame) -> int:
        """
        主力净流入的日间波动。

        逻辑: 稳定的持续小买 > 忽大忽小的异动。
        日均流入1000万±200万 比 日均±1亿 更可信。

        3分: 近10日 std/mean < 1.5 (非常稳定)
        2分: std/mean < 2.5
        1分: std/mean < 4.0
        0分: std/mean >= 4.0 (剧烈波动)
        """
        if len(df) < 10:
            return 0

        main = df["main_net"].values.astype(np.float64)[-10:]
        mean_flow = np.mean(main)
        std_flow = np.std(main)

        if mean_flow <= 0:
            # 净流出阶段, 稳定性无意义
            return 0

        cv = std_flow / mean_flow if mean_flow > 0 else 999

        if cv < 1.5:
            return 3
        elif cv < 2.5:
            return 2
        elif cv < 4.0:
            return 1
        return 0

    # ============================================================
    #  3. 正向占比 (0-3) — NEW: 10日中主力净流入的天数比例
    # ============================================================
    def _score_positive_ratio(self, df: pd.DataFrame) -> int:
        """
        近10日主力净流入的阳线天数占比。

        逻辑: 10天中8天净流入 > 10天中3天净流入(但总额相同)。
        高占比 = 机构在持续买入，而非一次性拉抬。

        3分: ≥80% 天数净流入
        2分: 60-80%
        1分: 40-60%
        0分: <40%
        """
        if len(df) < 5:
            return 0

        n = min(10, len(df))
        main = df["main_net"].values.astype(np.float64)[-n:]
        positive_days = np.sum(main > 0)
        ratio = positive_days / n

        if ratio >= 0.8:
            return 3
        elif ratio >= 0.6:
            return 2
        elif ratio >= 0.4:
            return 1
        return 0

    # ============================================================
    #  4. 智能资金 (0-3) — 超大单+大单背离 (保留并强化)
    # ============================================================
    def _score_smart_money(self, df: pd.DataFrame) -> int:
        """
        超大单 vs 大单背离信号。

        逻辑: 超大单=机构/长线, 大单=游资/短线。
        超大单流入 + 大单流出 = 机构在吃货, 游资在出 → 更健康。
        
        v2改进: 只在主力总体净流入时才给高分。

        3分: 近5日超大单流入>0 + 大单流出 + 主力净流入>0 (完美配置)
        2分: 超大单占主力流入>60%
        1分: 近5日超大单净流入>0
        0分: 超大单净流出
        """
        if len(df) < 5:
            return 0

        super_large = df["super_large_net"].values.astype(np.float64)
        large = df["large_net"].values.astype(np.float64)
        main = df["main_net"].values.astype(np.float64)

        sl_5d = np.sum(super_large[-5:])
        l_5d = np.sum(large[-5:])
        m_5d = np.sum(main[-5:])

        # 完美配置
        if sl_5d > 0 and l_5d < 0 and m_5d > 0:
            return 3

        # 超大单占比高
        if sl_5d > 0 and m_5d > 0:
            sl_ratio = sl_5d / m_5d
            if sl_ratio > 0.6:
                return 2
            return 1

        # 超大单负 = 机构在出货
        if sl_5d < 0:
            return 0

        return 1

    # ============================================================
    #  5. 流入加速度 (0-3) — 保留但加条件
    # ============================================================
    def _score_flow_acceleration(self, df: pd.DataFrame) -> int:
        """
        主力净流入加速度。

        v2改进: 只在流入背景下才有意义。
        从流出转为流入 = 最大加速度 (比在流入中加速更重要)。

        3分: 5日均流入>0 AND 20日均流入<0 (从空转多, 最大加速度)
        2分: 5日均流入 > 20日均流入 * 2 (急剧加速)
        1分: 5日均流入 > 20日均流入 (温和加速)
        0分: 5日均流入 < 20日均流入 (减速)
        """
        if len(df) < 10:
            return 0

        main = df["main_net"].values.astype(np.float64)

        avg_5d = np.mean(main[-5:])
        avg_20d = np.mean(main[-20:]) if len(main) >= 20 else np.mean(main)

        # 从流出转为流入 = 最大加速度
        if avg_5d > 0 and avg_20d < 0:
            return 3

        # 在流入中加速
        if avg_5d > 0 and avg_20d > 0:
            ratio = avg_5d / avg_20d if avg_20d > 0 else 0
            if ratio > 2.0:
                return 2
            elif ratio > 1.2:
                return 1
            return 0

        # 都在流出
        if avg_5d < 0:
            return 0

        return 1

    # ============================================================
    #  6. 相对规模 (0-3) — 替代flow_intensity
    # ============================================================
    def _score_relative_scale(self, df: pd.DataFrame, code: str) -> int:
        """
        主力净流入相对于流通市值的比例。

        v1的flow_intensity用绝对金额(-0.022相关, 最差)，
        绝对金额大可能是大盘股自然波动。
        相对规模才是真正的"资金诚意"。

        3分: 5日净流入 > 流通市值的0.3% (大规模吸筹)
        2分: > 0.1%
        1分: > 0%
        0分: 净流出
        """
        if len(df) < 5:
            return 0

        main = df["main_net"].values.astype(np.float64)
        flow_5d = np.sum(main[-5:])

        if flow_5d <= 0:
            return 0

        # 获取流通市值
        shares = self._load_market_cap(code)
        if pd.isna(shares) or shares <= 0:
            # 无法获取市值时, 用流入额本身做粗略判断
            if flow_5d > 100_000_000:  # >1亿
                return 2
            elif flow_5d > 10_000_000:
                return 1
            return 0

        # 流通市值 ≈ shares(万股) * 近似均价
        # 这里用 shares(万股) * 10元 作为粗略估计
        # shares 单位: 万股 (如某股票: 10000万股 = 1亿股)
        approx_mcap = shares * 10  # 粗略市值(万元)

        if approx_mcap <= 0:
            return 0

        relative_flow = flow_5d / approx_mcap

        if relative_flow > 0.003:  # 0.3%
            return 3
        elif relative_flow > 0.001:  # 0.1%
            return 2
        elif relative_flow > 0:
            return 1
        return 0

    # ============================================================
    #  主评分入口
    # ============================================================
    def score(self, code: str, as_of_date: str) -> dict[str, Any]:
        df = self._load_flow_data(code, as_of_date)

        if df.empty or len(df) < 5:
            return {
                "code": code,
                "as_of_date": as_of_date,
                "total": 0,
                "weighted": 0.0,
                "sub_scores": {},
                "error": "资金流数据不足" if df.empty else f"仅{len(df)}天数据, 需≥5",
            }

        sub_scores = {
            "persistent_inflow": self._score_persistent_inflow(df),
            "flow_stability": self._score_flow_stability(df),
            "positive_ratio": self._score_positive_ratio(df),
            "smart_money": self._score_smart_money(df),
            "flow_acceleration": self._score_flow_acceleration(df),
            "relative_scale": self._score_relative_scale(df, code),
        }

        total = sum(sub_scores.values())
        weighted = round(total / 18 * 20, 1)  # 6维 × 3分 = 18满分 → 归一化到0-20

        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": total,
            "weighted": weighted,
            "sub_scores": sub_scores,
            "error": None,
        }

    def batch_score(
        self, codes, as_of_date: str | None = None, verbose: bool = False
    ) -> pd.DataFrame:
        # 统一接口适配: 兼容旧版 codes_and_dates: list[tuple[str, str]]
        if as_of_date is None and isinstance(codes, list) and codes and isinstance(codes[0], (list, tuple)):
            codes_and_dates = codes
        else:
            codes_and_dates = [(c, as_of_date) for c in codes]
        results = []
        for i, (code, dt) in enumerate(codes_and_dates):
            r = self.score(code, dt)
            if r["error"] is None and r["sub_scores"]:
                results.append({
                    "code": code,
                    "as_of_date": dt,
                    "total": r["total"],
                    "weighted": r["weighted"],
                    **{f"fund_{k}": v for k, v in r["sub_scores"].items()},
                })
            if verbose and (i + 1) % 50 == 0:
                print(f"  ... 已评分 {i + 1}/{len(codes_and_dates)}")
        return pd.DataFrame(results)
