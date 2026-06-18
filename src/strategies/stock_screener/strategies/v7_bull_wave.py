"""
七维共振牛股波段模型 v7.0 — Bull Wave Catcher
===================================================
基于A股历史翻倍牛股的7维共性特征，捕捉主升浪启动期。

维度权重 (来自设计文档):
  技术面 20% | 资金面 20% | 情绪面 10%
  消息面 15% | 大盘行情 10% | 机构参与度 10% | 板块地位 15%

信号等级 → 仓位倍数:
  ≥70分: 强烈共振 → ×1.5
  60-69: 中等共振 → ×1.0
  50-59: 弱共振   → ×0.5
  <50:  不操作   → ×0

硬性共振条件 (不满足则降级):
  1. 技术面+资金面 ≥ 22分
  2. 板块地位 ≥ 8分
  3. 大盘行情 ≥ 5分
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[3]))

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings("ignore")

from core.data_loader import load_kline, load_quotes, load_finance
from core.indicators import precompute_indicators


# ============================================================
# 维度评分器
# ============================================================

class TechnicalScorer:
    """技术面 v7 — 满分20分"""
    def score(self, row: pd.Series, market: dict) -> Tuple[int, dict]:
        pts, detail = 0, {}
        # T1: 底部横盘 (满分3)
        if row.get('consolidation_60d'):
            rng = (row['consolidation_max'] - row['consolidation_min']) / row['consolidation_min']
            if rng < 0.20: pts += 3; detail['T1'] = f"横盘{rng*100:.1f}%"
        # T2: 倍量突破 (满分4)
        if row.get('volume_ratio', 0) > 2.0:
            pts += 4; detail['T2'] = f"量比{row['volume_ratio']:.1f}x"
        # T3: 均线多头 (满分3)
        if row.get('ma_alignment'):
            pts += 3; detail['T3'] = "MA多头"
        # T4: 筹码集中 (满分2)
        if row.get('concentration', 0) > 0.70:
            pts += 2; detail['T4'] = f"筹码{row['concentration']*100:.0f}%"
        # T5: 缩量回调 (满分3)
        if row.get('pullback_low_vol') and not row.get('break_ma10'):
            pts += 3; detail['T5'] = f"缩量回{row.get('pullback_pct',0)*100:.1f}%"
        # T6: 换手率健康 (满分3)
        if 3 <= row.get('turnover_rate', 100) <= 15:
            pts += 3; detail['T6'] = f"换手{row['turnover_rate']:.1f}%"
        # T7: RPS强势 (满分2)
        if row.get('rps_rank', 0) > 90:
            pts += 2; detail['T7'] = f"RPS{row['rps_rank']}"
        return min(pts, 20), detail


class FundFlowScorer:
    """资金面 v7 — 满分20分"""
    def score(self, row: pd.Series, dragon_data: list) -> Tuple[int, dict]:
        pts, detail = 0, {}
        code = row.get('code', '')
        # F1: 主力净流入 (满分5)
        if row.get('main_force_inflow', 0) > 50000000:  # 5000万
            pts += 5; detail['F1'] = f"主净{row['main_force_inflow']/1e8:.1f}亿"
        # F2: 大单占比 (满分3)
        if row.get('big_order_ratio', 0) > 0.30:
            pts += 3; detail['F2'] = f"大单{row['big_order_ratio']*100:.0f}%"
        # F3: 龙虎榜净买 (满分4) — 需要 dragon_tiger 数据
        dg = [d for d in dragon_data if d.get('code') == code]
        if dg:
            net = sum(d.get('net_buy', 0) for d in dg[:5])
            if net > 0 and (dg[0].get('buy_types', [''])[0] in ('机构', '游资'):
                pts += 4; detail['F3'] = f"龙虎净{net/1e8:.1f}亿"
        # F4: 龙虎榜席位质量 (满分3)
        if dg:
            quality = sum(1 for d in dg[:5] if d.get('buy_types', [''])[0] in ('机构', '外资'))
            if quality >= 2: pts += 3; detail['F4'] = f"席位{quality}类"
        # F5: 封板质量 (满分3)
        if row.get('limit_up_quality'):
            pts += 3; detail['F5'] = "封板优"
        # F6: 北向增持 (满分2) — 需要 margin_trading 数据
        if row.get('northbound_increase'):
            pts += 2; detail['F6'] = "北向+"
        return min(pts, 20), detail


class SentimentScorer:
    """情绪面 v7 — 满分10分 (反向+确认信号)"""
    def score(self, row: pd.Series) -> Tuple[int, dict]:
        pts, detail = 0, {}
        # S1: 股吧热度 (满分3 — 反向：过热=0分)
        heat = row.get('bar_heat', 1.0)
        if 1.0 <= heat <= 3.0:  # 温和不热
            pts += 3; detail['S1'] = f"股吧{heat:.1f}x"
        elif heat > 5.0:  # 过热，不加分
            pass
        # S2: 多空比 (满分3)
        if 1.5 <= row.get('bull_bear_ratio', 0) <= 3.0:
            pts += 3; detail['S2'] = f"多空{row['bull_bear_ratio']:.1f}"
        # S3: 新增关注 (满分2)
        if row.get('new_followers_pct', 0) > 0.50:
            pts += 2; detail['S3'] = "关注+"
        # S4: 雪球热度 (满分2)
        if row.get('xueqiu_heat_rankup', 0) > 100:
            pts += 2; detail['S4'] = "雪球+"
        return min(pts, 10), detail


class NewsScorer:
    """消息面 v7 — 满分15分"""
    def score(self, row: pd.Series) -> Tuple[int, dict]:
        pts, detail = 0, {}
        # N1: 业绩超预期 (满分5)
        if row.get('earnings_beat'):
            pts += 5; detail['N1'] = "业绩+"
        # N2: 政策催化 (满分4)
        if row.get('policy_catalyst'):
            pts += 4; detail['N2'] = "政策+"
        # N3: 重大合同 (满分3)
        if row.get('major_contract'):
            pts += 3; detail['N3'] = "合同+"
        # N4: 题材纯粹度 (满分3)
        if row.get('theme_purity', 0) > 0.70:
            pts += 3; detail['N4'] = f"纯粹{row['theme_purity']*100:.0f}%"
        return min(pts, 15), detail


class MarketScorer:
    """大盘行情 v7 — 满分10分"""
    def score(self, row: pd.Series, index_data: pd.DataFrame) -> Tuple[int, dict]:
        pts, detail = 0, {}
        if index_data is None or index_data.empty:
            return 0, {}
        latest = index_data.iloc[-1]
        # M1: 大盘趋势 (满分4)
        if latest.get('hs300_above_ma60') and latest.get('hs300_ma20_gt_ma60'):
            pts += 4; detail['M1'] = "大盘多头"
        # M2: 市场量能 (满分3)
        if latest.get('total_volume', 0) > 800e8:  # 8000亿
            pts += 3; detail['M2'] = "量能+"
        # M3: 上涨家数 (满分2)
        if latest.get('advance_ratio', 0) > 0.50:
            pts += 2; detail['M3'] = "上涨+"
        # M4: 涨停家数 (满分1)
        if latest.get('limit_up_count', 0) > 50:
            pts += 1; detail['M4'] = "涨停+"
        return min(pts, 10), detail


class InstitutionalScorer:
    """机构参与度 v7 — 满分10分"""
    def score(self, row: pd.Series) -> Tuple[int, dict]:
        pts, detail = 0, {}
        # I1: 基金持仓 (满分3)
        if row.get('fund_holding_pct_change', 0) > 0:
            pts += 3; detail['I1'] = "基金+"
        # I2: 分析师覆盖 (满分2)
        if row.get('analyst_count', 0) > 3:
            pts += 2; detail['I2'] = f"研报{row['analyst_count']}篇"
        # I3: 股东户数 (满分3)
        if row.get('shareholder_change_pct', 1) < 0.90:  # 减少>10%
            pts += 3; detail['I3'] = "股东-"
        # I4: 融资余额 (满分2)
        if row.get('margin_balance_increase'):
            pts += 2; detail['I4'] = "融资+"
        return min(pts, 10), detail


class SectorScorer:
    """板块地位 v7 — 满分15分"""
    def score(self, row: pd.Series, sector_data: pd.DataFrame) -> Tuple[int, dict]:
        pts, detail = 0, {}
        sector = row.get('sector', '')
        if sector_data is None or sector_data.empty:
            return 0, {}
        sd = sector_data[sector_data['sector'] == sector]
        if sd.empty:
            return 0, {}
        s = sd.iloc[0]
        # P1: 板块RPS (满分4)
        if s.get('sector_rps', 0) > 90:
            pts += 4; detail['P1'] = "板块RPS+"
        # P2: 板块涨停梯队 (满分4)
        if s.get('sector_limit_up_count', 0) >= 3:
            pts += 4; detail['P2'] = f"梯队{s['sector_limit_up_count']}家"
        # P3: 个股价位 (满分3)
        if s.get('price_rank_in_sector', 100) <= 5:
            pts += 3; detail['P3'] = "板块前5"
        # P4: 板块资金 (满分2)
        if s.get('sector_fund_inflow', 0) > 5e8:
            pts += 2; detail['P4'] = "板块资金+"
        # P5: 板块联动 (满分2)
        if s.get('sector_correlation', 0) > 0.6:
            pts += 2; detail['P5'] = "板块联动+"
        return min(pts, 15), detail


# ============================================================
# 主策略
# ============================================================

@dataclass
class Signal:
    """统一信号结构"""
    code: str
    close: float
    name: str = ""
    score: float = 0.0
    strategy: str = ""
    extra: Dict[str, any] = field(default_factory=dict)


@dataclass
class Trade:
    """统一交易记录"""
    code: str
    name: str
    strategy: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    return_pct: float
    hold_days: int
    _shares: int = 0


class BullWaveStrategy:
    """
    七维共振牛股波段模型 v7.0

    在 run_daily 中计算7个维度分数，判断是否触发买入/卖出。
    硬性共振条件：
      - 技术面+资金面 ≥ 22分
      - 板块地位 ≥ 8分
      - 大盘行情 ≥ 5分
    """
    name = "七维共振牛股波段v7"
    version = "v7.0"
    author = "QingSongTears"

    def __init__(self,
                 initial_capital: float = 1_000_000,
                 max_positions: int = 10,
                 stop_loss_pct: float = -8.0,
                 take_profit_pct: float = 0.0,  # 0=移动止盈
                 max_hold_days: int = 60):
        self.initial_capital = initial_capital
        self.max_positions = max_positions
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_days = max_hold_days
        # 加载评分器
        self.scorers = {
            'technical': TechnicalScorer(),
            'fund': FundFlowScorer(),
            'sentiment': SentimentScorer(),
            'news': NewsScorer(),
            'market': MarketScorer(),
            'institutional': InstitutionalScorer(),
            'sector': SectorScorer(),
        }
        self.weights = {
            'technical': 0.20,
            'fund': 0.20,
            'sentiment': 0.10,
            'news': 0.15,
            'market': 0.10,
            'institutional': 0.10,
            'sector': 0.15,
        }
        # 硬性共振阈值
        self.hard_tech_fund_min = 22   # 技术+资金
        self.hard_sector_min = 8       # 板块地位
        self.hard_market_min = 5       # 大盘行情

    def run_daily(self,
                      kline_dict: Dict[str, pd.DataFrame],
                      quotes_dict: dict,
                      finance_dict: dict,
                      today: pd.Timestamp,
                      held_codes: set,
                      dragon_data: list = None,
                      index_data: pd.DataFrame = None,
                      sector_data: pd.DataFrame = None) -> List[Signal]:
        """
        每日扫描，返回买入信号列表。
        参数：
            kline_dict: {code: DataFrame} 预计算的K线+指标
            quotes_dict: {code: {name, mcap, ...}}
            finance_dict: {code: finance_summary row}
            today: 当前日期
            held_codes: 已持仓代码集合
            dragon_data: 龙虎榜数据（可选）
            index_data: 大盘指数数据（可选）
            sector_data: 板块数据（可选）
        """
        signals = []
        for code, df in kline_dict.items():
            if code in held_codes:
                continue  # 已持仓，skip
            if len(df) < 60:
                continue
            row = df.iloc[-1]  # 最新一根K线

            # ── 计算7维分数 ──
            scores = {}
            details = {}
            total = 0.0

            for key, scorer in self.scorers.items():
                if key == 'fund':
                    s, d = scorer.score(row, dragon_data or [])
                elif key == 'market':
                    s, d = scorer.score(row, index_data)
                elif key == 'sector':
                    s, d = scorer.score(row, sector_data)
                else:
                    s, d = scorer.score(row)
                scores[key] = s
                details[key] = d
                total += s * self.weights[key]

            total = round(total, 1)

            # ── 硬性共振条件检查 ──
            tech_fund = scores.get('technical', 0) + scores.get('fund', 0)
            sector = scores.get('sector', 0)
            market = scores.get('market', 0)

            hard_ok = (tech_fund >= self.hard_tech_fund_min and
                       sector >= self.hard_sector_min and
                       market >= self.hard_market_min)

            if not hard_ok:
                # 降级：只降1级
                if total >= 60:
                    total = min(total - 10, 59)  # 降为弱共振
                else:
                    continue  # 不触发

            # ── 信号等级 ──
            if total >= 70:
                position_mult = 1.5
                grade = "强烈共振"
            elif total >= 60:
                position_mult = 1.0
                grade = "中等共振"
            elif total >= 50:
                position_mult = 0.5
                grade = "弱共振"
            else:
                continue  # <50分不操作

            # ── 生成信号 ──
            extra = {
                'total_score': total,
                'grade': grade,
                'position_mult': position_mult,
                'scores': scores,
                'details': details,
                'atr_pct': row.get('atr_14_d', 0) * 100,
            }
            signals.append(Signal(
                code=code,
                close=row['close'],
                name=quotes_dict.get(code, {}).get('name', code),
                score=total,
                strategy=self.name,
                extra=extra,
            ))

        return signals

    def check_exit(self,
                      position: dict,
                      row: pd.Series,
                      today_str: str) -> Optional[Trade]:
        """
        检查持仓是否需要卖出。
        返回 Trade 或 None。
        """
        entry = position.get('entry_price', 0)
        if entry <= 0: return None
        price = row['close']
        ret = (price - entry) / entry * 100
        hold_days = (pd.Timestamp(today_str) - pd.Timestamp(position.get('entry_date', today_str))).days

        reason = None

        # 止损 (-8%)
        if ret <= self.stop_loss_pct:
            reason = f"止损 {ret:.1f}%≤{self.stop_loss_pct}%"

        # 移动止盈 (从最高点回撤>15%)
        if reason is None and self.take_profit_pct == 0:
            max_price = position.get('max_price', entry)
            if price > max_price:
                position['max_price'] = price
                position['max_ret'] = (price - entry) / entry * 100
            max_ret = position.get('max_ret', 0)
            if max_ret > 0 and (price - entry) / entry * 100 < max_ret - 15:
                reason = f"移动止盈 回撤{max_ret - ret:.1f}%>15%"

        # 固定止盈
        if reason is None and self.take_profit_pct > 0:
            if ret >= self.take_profit_pct:
                reason = f"止盈 {ret:.1f}%≥{self.take_profit_pct}%"

        # 趋势破坏 (跌破MA20)
        if reason is None and not row.get('above_ma20', True):
            reason = "趋势破坏 跌破MA20"

        # 量价背离 (创新高但量萎缩>50%)
        if reason is None:
            if row.get('new_high') and row.get('volume_ratio', 1) < 0.5:
                reason = "量价背离 量萎缩"

        # 时间止盈 (持仓>60日)
        if reason is None and hold_days > self.max_hold_days:
            reason = f"时间止盈 {hold_days}天"

        if reason:
            return Trade(
                code=position['code'],
                name=position.get('name', ''),
                strategy=self.name,
                entry_date=position['entry_date'],
                exit_date=today_str,
                entry_price=entry,
                exit_price=price,
                exit_reason=reason,
                return_pct=round(ret, 2),
                hold_days=hold_days,
            )
        return None

    def compute_indicators(self, kline: pd.DataFrame) -> pd.DataFrame:
        """
        为单只股票计算7维所需的所有指标。
        对标设计文档中的子指标定义。
        """
        df = kline.copy()
        df = df.sort_values('date').reset_index(drop=True)
        if len(df) < 60:
            return df

        # ── 技术面指标 ──
        # T1: 底部横盘 (60日最高/最低<20%)
        df['consolidation_min'] = df['close'].rolling(60).min()
        df['consolidation_max'] = df['close'].rolling(60).max()
        df['consolidation_60d'] = (df['consolidation_max'] - df['consolidation_min']) / df['consolidation_min'] < 0.20
        df['consolidation_days'] = df['close'].rolling(60).apply(
            lambda x: (x.max() - x.min()) / x.min() < 0.20)

        # T2: 量比
        df['ma5_vol'] = df['volume'].rolling(5).mean()
        df['volume_ratio'] = df['volume'] / (df['ma5_vol'] + 1e-10)

        # T3: 均线多头
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma60'] = df['close'].rolling(60).mean()
        df['ma_alignment'] = (df['ma5'] > df['ma10']) & (df['ma10'] > df['ma20']) & (df['ma20'] > df['ma60'])

        # T4: 筹码集中度 (需要 chip_distribution 数据，这里用波动率代替)
        df['volatility_20d'] = df['close'].pct_change().rolling(20).std()
        df['concentration'] = (df['volatility_20d'] < 0.02).astype(float)  # 低波动=筹码集中

        # T5: 缩量回调
        df['pullback_pct'] = (df['close'] - df['ma20']) / df['ma20']
        df['pullback_low_vol'] = (df['volume_ratio'] < 0.8) & (df['pullback_pct'] > -0.08)

        # T6: 换手率
        if 'turnover' in df.columns:
            df['turnover_rate'] = df['turnover']
        else:
            df['turnover_rate'] = 0

        # T7: RPS (需要全市场排名，这里用自身涨幅代替)
        df['ret_20d'] = df['close'].pct_change(20)
        df['rps_rank'] = 90  # placeholder，需外部计算

        # ── 大盘指标 ──
        # M1: 与MA60关系 (外部传入 index_data)

        return df


# ============================================================
# 快速测试函数
# ============================================================

def quick_backtest(symbol: str = '000001',
                 start: str = '2024-01-01',
                 end: str = '2026-06-12'):
    """用单只股票快速验证指标计算"""
    print(f"📊 七维共振模型 v7.0 快速测试")
    print(f"   股票: {symbol} | 期间: {start} ~ {end}")

    kline = load_kline()
    kline = kline[kline['code'] == symbol]
    kline = kline[(kline['date'] >= start) & (kline['date'] <= end)]
    if kline.empty:
        print("⚠️ 无K线数据"); return

    kline = precompute_indicators(kline)
    strategy = BullWaveStrategy()
    df = strategy.compute_indicators(kline)

    print(f"✅ 指标计算完成，共 {len(df)} 根K线")
    print(f"\n📋 最新指标快照 ({df.iloc[-1]['date'].strftime('%Y-%m-%d')}):")
    row = df.iloc[-1]
    for key in ['consolidation_60d', 'volume_ratio', 'ma_alignment',
                 'concentration', 'pullback_low_vol', 'turnover_rate', 'rps_rank']:
        print(f"   {key}: {row.get(key, 'N/A')}")

    # 模拟评分
    print(f"\n📊 维度评分 (模拟）:")
    ts = TechnicalScorer()
    t_score, t_detail = ts.score(row, {})
    print(f"   技术面: {t_score}/20 → {t_detail}")

    print(f"\n✅ 快速测试完成。完整回测需接入全市场数据。")


if __name__ == '__main__':
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else '000001'
    quick_backtest(symbol)
