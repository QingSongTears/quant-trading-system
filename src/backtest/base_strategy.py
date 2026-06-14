"""
策略基类
=======

所有量化策略必须继承 BaseStrategy。
BaseStrategy 继承自 backtesting.Strategy，并封装了:
- 常用技术指标计算
- 交易成本感知
- 策略元信息

策略来源文献标注在策略类的 docstring 中。
"""
from backtesting import Strategy
import pandas as pd
import numpy as np


class BaseStrategy(Strategy):
    """
    策略基类 — 所有自定义策略的父类
    
    子类只需实现:
    - init(): 计算技术指标
    - next():  每个交易周期调用一次，编写买卖决策逻辑
    
    策略元信息 (必须覆盖):
    - name: str        策略名称
    - description: str 策略描述
    - source: str      策略来源 (文献/论文引用，用于可考证)
    """

    # 子类必须覆盖的属性
    name: str = "base"
    description: str = "策略基类"
    source: str = ""

    # 可选的参数（子类覆盖）
    # 例如: fast_period = 5

    def init(self):
        """
        初始化 — 在此方法中计算技术指标
        
        示例:
            self.sma_fast = self.I(self.sma, self.data.Close, self.fast_period)
        """
        pass

    def next(self):
        """
        每个交易周期执行一次 — 在此方法中编写买卖逻辑
        
        示例:
            if self.sma_fast[-1] > self.sma_slow[-1] and self.sma_fast[-2] <= self.sma_slow[-2]:
                self.buy()
            elif self.sma_fast[-1] < self.sma_slow[-1] and self.sma_fast[-2] >= self.sma_slow[-2]:
                self.sell()
        """
        pass

    # ===== 技术指标辅助方法 =====

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """简单移动平均 (Simple Moving Average)"""
        return series.rolling(window=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """指数移动平均 (Exponential Moving Average)"""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """
        相对强弱指标 (Relative Strength Index)
        来源: Wilder, J. W. (1978). New Concepts in Technical Trading Systems
        """
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        """
        MACD 指标
        来源: Appel, G. (1979). The Moving Average Convergence-Divergence Method
        
        Returns:
            (macd_line, signal_line, histogram)
        """
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
        """
        布林带 (Bollinger Bands)
        来源: Bollinger, J. (2001). Bollinger on Bollinger Bands
        
        Returns:
            (middle_band, upper_band, lower_band)
        """
        middle = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        return middle, upper, lower

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        平均真实波幅 (Average True Range)
        来源: Wilder, J. W. (1978)
        """
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def highest(series: pd.Series, period: int) -> pd.Series:
        """N周期最高值"""
        return series.rolling(window=period).max()

    @staticmethod
    def lowest(series: pd.Series, period: int) -> pd.Series:
        """N周期最低值"""
        return series.rolling(window=period).min()

    # ===== A股特殊规则辅助方法 =====

    @staticmethod
    def is_limit_up(close: pd.Series, prev_close: pd.Series) -> pd.Series:
        """
        判断是否涨停
        A股主板 ±10%，科创/创业 ±20%
        """
        limit_ratio = close.copy()
        limit_ratio[:] = 0.10  # 默认10%，此处简化
        return close >= prev_close * (1 + limit_ratio)

    @staticmethod
    def is_limit_down(close: pd.Series, prev_close: pd.Series) -> pd.Series:
        """判断是否跌停"""
        limit_ratio = close.copy()
        limit_ratio[:] = 0.10
        return close <= prev_close * (1 - limit_ratio)
