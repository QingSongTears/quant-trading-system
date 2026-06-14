"""
MACD 金叉死叉策略
=================

基于 MACD 指标的经典趋势跟踪策略。
MACD 线上穿信号线 → 买入
MACD 线下穿信号线 → 卖出

来源: Appel, G. (1979). The Moving Average Convergence-Divergence Method.
"""
from ...backtest.base_strategy import BaseStrategy


class MACDSignalStrategy(BaseStrategy):
    """MACD 金叉死叉策略"""

    name: str = "MACD金叉死叉"
    description: str = "MACD线上穿信号线买入，下穿卖出"
    source: str = "Appel, G. (1979). The Moving Average Convergence-Divergence Method"

    fast_period = 12
    slow_period = 26
    signal_period = 9

    def init(self):
        close = self.data.Close

        # EMA快慢线
        ema_fast = self.I(self.ema, close, self.fast_period)
        ema_slow = self.I(self.ema, close, self.slow_period)

        # MACD 线 = 快EMA - 慢EMA
        self.macd_line = ema_fast - ema_slow

        # 信号线 = MACD 线的 EMA
        self.signal_line = self.I(self.ema, self.macd_line, self.signal_period)

        # 柱状图 = MACD线 - 信号线
        self.histogram = self.macd_line - self.signal_line

    def next(self):
        if len(self.macd_line) < self.signal_period + 1:
            return

        # 金叉: MACD线从下方上穿信号线
        if (self.macd_line[-1] > self.signal_line[-1] and
                self.macd_line[-2] <= self.signal_line[-2]):
            if not self.position:
                self.buy()

        # 死叉: MACD线从上方下穿信号线
        elif (self.macd_line[-1] < self.signal_line[-1] and
                self.macd_line[-2] >= self.signal_line[-2]):
            if self.position:
                self.position.close()
