"""
双均线交叉策略 (Moving Average Cross)
======================================

经典趋势跟踪策略。
短期均线上穿长期均线 → 买入信号
短期均线下穿长期均线 → 卖出信号

来源: 基于移动平均线交叉概念，广泛应用于技术分析。
参考文献: Murphy, J. J. (1999). Technical Analysis of the Financial Markets.
"""
from ...backtest.base_strategy import BaseStrategy


class MACrossStrategy(BaseStrategy):
    """双均线交叉策略"""

    name: str = "双均线交叉"
    description: str = "快线上穿慢线买入，下穿卖出"
    source: str = "Murphy, J. J. (1999). Technical Analysis of the Financial Markets"

    # 策略参数（可在 YAML 配置中覆盖）
    fast_period = 5
    slow_period = 20

    def init(self):
        # 计算快慢均线
        self.fast_ma = self.I(self.sma, self.data.Close, self.fast_period)
        self.slow_ma = self.I(self.sma, self.data.Close, self.slow_period)

    def next(self):
        # 跳过均线未完全计算的前期
        if len(self.fast_ma) < self.slow_period:
            return

        # 金叉买入: 快线从下方上穿慢线
        if (self.fast_ma[-1] > self.slow_ma[-1] and
                self.fast_ma[-2] <= self.slow_ma[-2]):
            if not self.position:
                self.buy()

        # 死叉卖出: 快线从上方下穿慢线
        elif (self.fast_ma[-1] < self.slow_ma[-1] and
                self.fast_ma[-2] >= self.slow_ma[-2]):
            if self.position:
                self.position.close()
