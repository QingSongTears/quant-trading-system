"""
策略基类
=======

所有量化策略必须继承 BaseStrategy。
BaseStrategy 继承自 backtesting.Strategy，并封装了:
- 常用技术指标计算 (SMA/EMA/RSI/MACD/布林带/ATR)
- 交易成本感知
- 策略元信息 (名称/描述/学术来源)

策略来源文献标注在策略类的 docstring 中，确保可考证。
"""
from __future__ import annotations
from backtesting import Strategy
import pandas as pd
import numpy as np


class BaseStrategy(Strategy):
    """
    策略基类 — 所有自定义策略的父类
    
    子类只需实现:
    - init(): 计算技术指标，使用 self.I() 注册
    - next(): 每个交易周期调用一次，编写买卖决策逻辑
    
    策略元信息 (必须覆盖):
    - name: str        策略名称
    - description: str 策略描述
    - source: str      策略来源 (文献/论文引用，用于可考证)
    """

    # 子类必须覆盖的属性
    name: str = "base"
    description: str = "策略基类"
    source: str = ""

    def init(self):
        """初始化 — 在此方法中使用 self.I() 注册指标"""
        pass

    def next(self):
        """每个交易周期执行一次 — 买卖决策"""
        pass

    # ===== 技术指标 (static, 兼容 Backtesting.py 的 _Array) =====

    @staticmethod
    def sma(series, period: int):
        """简单移动平均 (Simple Moving Average)"""
        return pd.Series(series).rolling(window=period).mean()

    @staticmethod
    def ema(series, period: int):
        """指数移动平均 (Exponential Moving Average)"""
        return pd.Series(series).ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series, period: int = 14):
        """
        相对强弱指标 (RSI)
        来源: Wilder, J. W. (1978). New Concepts in Technical Trading Systems
        """
        s = pd.Series(series)
        delta = s.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(series, fast: int = 12, slow: int = 26, signal: int = 9):
        """
        MACD 指标
        来源: Appel, G. (1979). The Moving Average Convergence-Divergence Method
        Returns: (macd_line, signal_line, histogram)
        """
        s = pd.Series(series)
        ema_fast = s.ewm(span=fast, adjust=False).mean()
        ema_slow = s.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(series, period: int = 20, std: float = 2.0):
        """
        布林带 (Bollinger Bands)
        来源: Bollinger, J. (2001). Bollinger on Bollinger Bands
        Returns: (middle, upper, lower)
        """
        s = pd.Series(series)
        middle = s.rolling(window=period).mean()
        std_dev = s.rolling(window=period).std()
        return middle, middle + std * std_dev, middle - std * std_dev

    @staticmethod
    def atr(high, low, close, period: int = 14):
        """
        平均真实波幅 (ATR)
        来源: Wilder, J. W. (1978)
        """
        h, l, c = pd.Series(high), pd.Series(low), pd.Series(close)
        tr = pd.concat([
            (h - l).abs(),
            (h - c.shift()).abs(),
            (l - c.shift()).abs()
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def highest(series, period: int):
        """N周期最高值"""
        return pd.Series(series).rolling(window=period).max()

    @staticmethod
    def lowest(series, period: int):
        """N周期最低值"""
        return pd.Series(series).rolling(window=period).min()
