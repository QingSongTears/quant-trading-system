"""
布林带突破策略 (Bollinger Bands Breakout)
=========================================

波动率策略。
价格跌破布林带下轨 → 超卖 → 买入
价格突破布林带上轨 → 超买 → 卖出

来源: Bollinger, J. (2001). Bollinger on Bollinger Bands. McGraw-Hill.
"""
from __future__ import annotations
from ..backtest.base_strategy import BaseStrategy


class BollingerBreakoutStrategy(BaseStrategy):
    """布林带突破策略"""

    name: str = "布林带突破"
    description: str = "价格突破布林带下轨买入，突破上轨卖出"
    source: str = "Bollinger, J. (2001). Bollinger on Bollinger Bands"

    period = 20
    std_dev = 2.0

    def init(self):
        close = self.data.Close
        self.middle, self.upper, self.lower = self.I(
            self.bollinger_bands, close, self.period, self.std_dev
        )

    def next(self):
        if len(self.data.Close) < self.period:
            return

        # 价格跌破下轨 → 买入
        if self.data.Close[-1] < self.lower[-1]:
            if not self.position:
                self.buy()

        # 价格突破上轨 → 卖出
        elif self.data.Close[-1] > self.upper[-1]:
            if self.position:
                self.position.close()

        # 价格回归中轨 → 止盈出场
        elif (self.position and
              self.data.Close[-2] > self.middle[-2] and
              self.data.Close[-1] <= self.middle[-1]):
            self.position.close()
