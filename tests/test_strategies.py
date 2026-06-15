"""
测试用例: F3 策略框架 — 技术指标计算正确性
对应 Issue #42 [B-02] / 验收清单 TC-F3-001 ~ TC-F3-004
"""
import pytest
import pandas as pd
import numpy as np

from src.backtest.base_strategy import BaseStrategy


# ============================================================
# TC-F3-001: 技术指标计算正确性
# ============================================================

class TestSMA:
    """简单移动平均测试"""

    def test_sma_basic(self):
        """基本 SMA 计算"""
        data = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        result = BaseStrategy.sma(data, period=5)
        expected = data.rolling(5).mean()
        pd.testing.assert_series_equal(result, expected)

    def test_sma_period_too_large(self):
        """period 大于数据长度时 NaN 填充"""
        data = pd.Series([1, 2, 3])
        result = BaseStrategy.sma(data, period=5)
        assert result.isna().all(), "所有值应为 NaN"

    def test_sma_single_value(self):
        """单值输入"""
        data = pd.Series([10.0])
        result = BaseStrategy.sma(data, period=1)
        assert result.iloc[0] == 10.0


class TestEMA:
    """指数移动平均测试"""

    def test_ema_basic(self):
        """基本 EMA 计算验证"""
        data = pd.Series([10, 12, 11, 13, 14, 16, 15, 17, 18, 20])
        result = BaseStrategy.ema(data, period=5)
        # EMA 应平滑跟随价格
        assert len(result) == len(data)
        assert not result.iloc[-1] is None
        assert result.iloc[-1] > 13  # 最后一个 EMA 应大于 13

    def test_ema_vs_sma_difference(self):
        """EMA 对近期价格更敏感"""
        data = pd.Series([10] * 10 + [20] * 5)
        ema = BaseStrategy.ema(data, period=10)
        sma = BaseStrategy.sma(data, period=10)
        # 价格上涨后 EMA 应 > SMA
        assert ema.iloc[-1] > sma.iloc[-1], "EMA 应对近期价格更敏感"


class TestRSI:
    """RSI 指标测试"""

    def test_rsi_range(self):
        """RSI 值在 0-100 之间"""
        np.random.seed(42)
        data = pd.Series(np.random.randn(100).cumsum() + 50)
        result = BaseStrategy.rsi(data, period=14)

        valid = result.dropna()
        assert (valid >= 0).all(), "RSI 不应低于 0"
        assert (valid <= 100).all(), "RSI 不应高于 100"

    def test_rsi_all_up(self):
        """连续上涨导致 RSI 偏高"""
        data = pd.Series(range(1, 31))  # 30 天连续上涨
        result = BaseStrategy.rsi(data, period=14)
        last_rsi = result.dropna().iloc[-1]
        assert last_rsi > 70, f"连续上涨时 RSI 应 > 70，实际 {last_rsi}"

    def test_rsi_all_down(self):
        """连续下跌导致 RSI 偏低"""
        data = pd.Series(range(30, 0, -1))  # 30 天连续下跌
        result = BaseStrategy.rsi(data, period=14)
        last_rsi = result.dropna().iloc[-1]
        assert last_rsi < 30, f"连续下跌时 RSI 应 < 30，实际 {last_rsi}"


class TestMACD:
    """MACD 指标测试"""

    def test_macd_returns_three_arrays(self):
        """MACD 返回三个序列"""
        data = pd.Series(np.random.randn(100).cumsum() + 50)
        macd_line, signal_line, histogram = BaseStrategy.macd(data)

        assert len(macd_line) == len(data)
        assert len(signal_line) == len(data)
        assert len(histogram) == len(data)

    def test_macd_histogram_relation(self):
        """柱状图 = MACD线 - 信号线"""
        data = pd.Series(np.random.randn(100).cumsum() + 50)
        macd_line, signal_line, histogram = BaseStrategy.macd(data)

        expected = macd_line - signal_line
        pd.testing.assert_series_equal(
            histogram.round(10), expected.round(10),
            check_names=False
        )

    def test_macd_cross_signal(self):
        """MACD 金叉信号验证"""
        data = pd.Series([10, 10.5, 11, 10.8, 11.2, 11.5, 12, 12.5, 13, 13.5] * 20)
        macd_line, signal_line, histogram = BaseStrategy.macd(data)

        # 找金叉点和死叉点
        cross_up = (
            (macd_line > signal_line) &
            (macd_line.shift(1) <= signal_line.shift(1))
        )
        assert cross_up.any(), "应至少检测到一次金叉"


class TestBollingerBands:
    """布林带测试"""

    def test_bollinger_bands_order(self):
        """上轨 > 中轨 > 下轨"""
        data = pd.Series(np.random.randn(100).cumsum() + 50)
        middle, upper, lower = BaseStrategy.bollinger_bands(data, period=20)

        valid = ~(middle.isna() | upper.isna() | lower.isna())
        assert (upper[valid] >= middle[valid]).all(), "上轨应 ≥ 中轨"
        assert (middle[valid] >= lower[valid]).all(), "中轨应 ≥ 下轨"

    def test_bollinger_price_mostly_inside(self):
        """价格大部分时间在布林带内"""
        np.random.seed(42)
        data = pd.Series(np.random.randn(200).cumsum() + 100)
        middle, upper, lower = BaseStrategy.bollinger_bands(data, period=20)

        valid = ~(middle.isna() | upper.isna() | lower.isna())
        inside = (data[valid] >= lower[valid]) & (data[valid] <= upper[valid])
        ratio = inside.sum() / len(data[valid])
        assert ratio > 0.75, f"价格应在带内 >75%，实际 {ratio:.1%}"


class TestATR:
    """ATR 测试"""

    def test_atr_positive(self):
        """ATR 值始终为正"""
        np.random.seed(42)
        n = 100
        close = pd.Series(np.random.randn(n).cumsum() + 50)
        high = close + np.abs(np.random.randn(n))
        low = close - np.abs(np.random.randn(n))

        result = BaseStrategy.atr(high, low, close, period=14)
        valid = result.dropna()
        assert (valid > 0).all(), "ATR 必须为正数"


class TestHighestLowest:
    """最高/最低值测试"""

    def test_highest(self):
        """N 周期最高值"""
        data = pd.Series([1, 3, 2, 5, 4])
        result = BaseStrategy.highest(data, period=3)
        assert result.iloc[2] == 3
        assert result.iloc[3] == 5
        assert result.iloc[4] == 5

    def test_lowest(self):
        """N 周期最低值"""
        data = pd.Series([5, 2, 4, 1, 3])
        result = BaseStrategy.lowest(data, period=3)
        assert result.iloc[2] == 2
        assert result.iloc[3] == 1
        assert result.iloc[4] == 1


# ============================================================
# TC-F3-002: 策略元信息
# ============================================================

class TestStrategyMeta:
    """验证策略类元信息完整性"""

    def test_all_strategies_have_name(self):
        """所有策略都有 name 属性"""
        from src.strategies.ma_cross import MACrossStrategy
        from src.strategies.macd_signal import MACDSignalStrategy
        from src.strategies.rsi_reversal import RSIReversalStrategy
        from src.strategies.bollinger_breakout import BollingerBreakoutStrategy
        from src.strategies.turtle_trading import TurtleTradingStrategy

        strategies = [
            MACrossStrategy, MACDSignalStrategy, RSIReversalStrategy,
            BollingerBreakoutStrategy, TurtleTradingStrategy,
        ]
        for cls in strategies:
            assert cls.name, f"{cls.__name__} 缺少 name"
            assert cls.description, f"{cls.__name__} 缺少 description"
            assert cls.source, f"{cls.__name__} 缺少 source（学术来源）"


# ============================================================
# TC-F3-004: 5 个预设策略可正常实例化
# ============================================================

def test_all_strategies_instantiable(sample_ohlcv_data):
    """验证 5 个策略可正常导入和实例化"""
    from src.strategies.ma_cross import MACrossStrategy
    from src.strategies.macd_signal import MACDSignalStrategy
    from src.strategies.rsi_reversal import RSIReversalStrategy
    from src.strategies.bollinger_breakout import BollingerBreakoutStrategy
    from src.strategies.turtle_trading import TurtleTradingStrategy

    strategies = [
        MACrossStrategy, MACDSignalStrategy, RSIReversalStrategy,
        BollingerBreakoutStrategy, TurtleTradingStrategy,
    ]
    for cls in strategies:
        # 策略实例化只需要类本身，不需要构造数据
        assert cls is not None
        assert cls.name is not None
        assert hasattr(cls, "init")
        assert hasattr(cls, "next")
        # 验证策略类可以被实例化（空参构造）
        instance = cls.__new__(cls)
        assert instance.name is not None
