"""
RSI 超买超卖策略 (RSI Reversal)
================================

均值回归型策略。
当 RSI 低于超卖阈值时 → 市场过度悲观 → 买入
当 RSI 高于超买阈值时 → 市场过度乐观 → 卖出

来源: Wilder, J. W. (1978). New Concepts in Technical Trading Systems.
"""
from ...backtest.base_strategy import BaseStrategy


class RSIReversalStrategy(BaseStrategy):
    """RSI 超买超卖策略"""

    name: str = "RSI超买超卖"
    description: str = "RSI低于超卖阈值买入，高于超买阈值卖出"
    source: str = "Wilder, J. W. (1978). New Concepts in Technical Trading Systems"

    rsi_period = 14
    oversold_threshold = 30
    overbought_threshold = 70

    def init(self):
        self.rsi = self.I(self.rsi, self.data.Close, self.rsi_period)

    def next(self):
        if len(self.rsi) < self.rsi_period:
            return

        current_rsi = self.rsi[-1]
        prev_rsi = self.rsi[-2]

        # RSI 从超卖区域回升 → 买入信号
        if (current_rsi > self.oversold_threshold and
                prev_rsi <= self.oversold_threshold):
            if not self.position:
                self.buy()

        # RSI 从超买区域回落 → 卖出信号
        elif (current_rsi < self.overbought_threshold and
                prev_rsi >= self.overbought_threshold):
            if self.position:
                self.position.close()
