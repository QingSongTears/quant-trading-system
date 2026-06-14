"""
海龟交易法则 (Turtle Trading)
==============================

经典趋势跟踪策略，由 Richard Dennis 在 1983 年提出。
- 入场: 价格突破 N 日高点 → 做多
- 出场: 价格跌破 M 日低点 → 平仓
- 止损: ATR × 倍数

来源: Faith, C. (2007). Way of the Turtle. McGraw-Hill.
"""
from ..backtest.base_strategy import BaseStrategy


class TurtleTradingStrategy(BaseStrategy):
    """海龟交易法则"""

    name: str = "海龟交易法则"
    description: str = "唐奇安通道突破入场，ATR动态止损"
    source: str = "Faith, C. (2007). Way of the Turtle. McGraw-Hill"

    entry_period = 20      # 入场通道周期
    exit_period = 10       # 出场通道周期
    atr_period = 20        # ATR 周期
    atr_multiplier = 2.0   # ATR 止损倍数

    def init(self):
        high = self.data.High
        low = self.data.Low
        close = self.data.Close

        # 唐奇安通道
        self.entry_high = self.I(self.highest, high, self.entry_period)
        self.exit_low = self.I(self.lowest, low, self.exit_period)

        # ATR
        self.atr = self.I(self.atr, high, low, close, self.atr_period)

        self.stop_price = 0

    def next(self):
        n = max(self.entry_period, self.atr_period)
        if len(self.data.Close) < n:
            return

        # 入场: 突破N日高点
        if (not self.position and
                self.data.Close[-1] > self.entry_high[-2]):
            self.buy()
            # 设置初始止损
            self.stop_price = self.data.Close[-1] - self.atr_multiplier * self.atr[-1]

        # 出场: 跌破M日低点 或 触发止损
        elif self.position:
            # 移动止损（仅向上移动）
            current_stop = self.data.Close[-1] - self.atr_multiplier * self.atr[-1]
            if current_stop > self.stop_price:
                self.stop_price = current_stop

            # 退出条件
            if (self.data.Close[-1] < self.exit_low[-1] or
                    self.data.Close[-1] < self.stop_price):
                self.position.close()
                self.stop_price = 0
