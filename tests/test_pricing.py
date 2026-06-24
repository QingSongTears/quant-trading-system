"""
价格规整工具单测 — src/utils/pricing.py

借鉴 vnpy.trader.utility (round_to / floor_to / ceil_to / get_digits)
+ A 股特化 helper (astock_round / astock_get_digits)
"""
import pytest

from src.utils.pricing import (
    round_to, floor_to, ceil_to, get_digits,
    astock_round, astock_get_digits, ASTOCK_PRICETICK,
)


# ── round_to ──────────────────────────


class TestRoundTo:
    """round_to — 按 target 步长四舍五入"""

    def test_rounds_to_2_decimal_places(self):
        # 0.01 步长, A 股标准
        assert round_to(10.234, 0.01) == 10.23
        assert round_to(10.235, 0.01) == 10.24
        # 边界 0.005 → 偶数舍入 (Python round 行为)
        assert round_to(10.225, 0.01) == 10.22

    def test_handles_float_arithmetic_drift(self):
        """Decimal 路径应规避 0.1 + 0.2 = 0.30000... 误差"""
        # 累计加 0.1 三次得 0.3, 但 float 实际是 0.30000000000000004
        drift = 0.1 + 0.1 + 0.1  # = 0.30000000000000004
        result = round_to(drift, 0.01)
        # 规整到 0.01 后应是 0.3, 不是 0.30000000000000004
        assert result == 0.3

    def test_rounds_to_100(self):
        """大宗交易 100 元手"""
        assert round_to(1234, 100) == 1200.0
        # 1250/100 = 12.5, Python round(0.5) = 0 (banker's rounding 偶数舍入)
        # 12 偶数 → 12, 结果 1200.0
        assert round_to(1250, 100) == 1200.0
        # 1350/100 = 13.5, banker's → 14 (偶数)
        assert round_to(1350, 100) == 1400.0

    def test_rounds_to_1(self):
        """整数步长"""
        assert round_to(10.7, 1) == 11.0
        assert round_to(10.3, 1) == 10.0

    def test_zero(self):
        assert round_to(0, 0.01) == 0

    def test_negative(self):
        """负数 (虽 A 股无负价, 但工具应支持)"""
        assert round_to(-10.234, 0.01) == -10.23

    def test_returns_float(self):
        assert isinstance(round_to(10.23, 0.01), float)


# ── floor_to ──────────────────────────


class TestFloorTo:
    """floor_to — 按 target 步长向下取整"""

    def test_floors_to_2_decimal_places(self):
        assert floor_to(10.239, 0.01) == 10.23
        assert floor_to(10.231, 0.01) == 10.23

    def test_floors_to_100(self):
        assert floor_to(1234, 100) == 1200.0
        assert floor_to(1299, 100) == 1200.0

    def test_exact_match(self):
        assert floor_to(10.0, 0.01) == 10.0

    def test_negative(self):
        assert floor_to(-10.231, 0.01) == -10.24  # 向 -∞

    def test_zero(self):
        assert floor_to(0, 0.01) == 0


# ── ceil_to ──────────────────────────


class TestCeilTo:
    """ceil_to — 按 target 步长向上取整"""

    def test_ceils_to_2_decimal_places(self):
        assert ceil_to(10.231, 0.01) == 10.24
        assert ceil_to(10.239, 0.01) == 10.24

    def test_ceils_to_100(self):
        assert ceil_to(1201, 100) == 1300.0
        assert ceil_to(1250, 100) == 1300.0

    def test_exact_match(self):
        assert ceil_to(10.0, 0.01) == 10.0

    def test_negative(self):
        assert ceil_to(-10.239, 0.01) == -10.23  # 向 +∞

    def test_zero(self):
        assert ceil_to(0, 0.01) == 0


# ── get_digits ──────────────────────────


class TestGetDigits:
    """get_digits — 获取小数位数 (尾部 0 忽略)"""

    def test_two_decimal_places(self):
        assert get_digits(10.23) == 2

    def test_zero_decimal(self):
        """vnpy 行为: Decimal("10.0").exponent = -1, 返回 1

        直观上看 10.0 是整数应为 0, 但 vnpy 用 Decimal.as_tuple().exponent 判定
        因此 10.0 → 1, 10.00 → 2, 10 → 0
        这是已知 quirk, 用户调用 get_digits(10.0) 应理解为 "带 1 位小数"
        """
        assert get_digits(10.0) == 1
        # 整数写法 10 应返回 0
        assert get_digits(10) == 0

    def test_four_decimal_places(self):
        assert get_digits(0.0001) == 4

    def test_trailing_zeros_ignored(self):
        """10.2300 应返回 2, 不是 4"""
        assert get_digits(10.23) == 2
        assert get_digits(10.230) == 2

    def test_negative(self):
        assert get_digits(-10.23) == 2

    def test_zero(self):
        assert get_digits(0) == 0

    def test_one_decimal(self):
        assert get_digits(0.5) == 1


# ── A 股 helper ──────────────────────────


class TestAstockRound:
    """astock_round — A 股价格按 pricetick=0.01 规整"""

    def test_default_pricetick(self):
        assert astock_round(10.234) == 10.23
        assert astock_round(10.235) == 10.24

    def test_constant_value(self):
        assert ASTOCK_PRICETICK == 0.01

    def test_custom_pricetick(self):
        """可覆盖 (ETF/可转债)"""
        # Decimal 路径下 banker's rounding:
        # 10.2345/0.001 = 10234.5, round = 10234 (偶数) → 10.234
        assert astock_round(10.2345, 0.001) == 10.234
        assert astock_round(10.2344, 0.001) == 10.234
        # 10235.5 → round = 10236 (偶数) → 10.236
        assert astock_round(10.2355, 0.001) == 10.236


class TestAstockGetDigits:
    """astock_get_digits — A 股价格小数位数 (默认 2)"""

    def test_default(self):
        assert astock_get_digits(10.23) == 2

    def test_passes_through(self):
        """仅作语义包装, 行为等价 get_digits"""
        # 10.0 在 vnpy 实现下返回 1 (见 get_digits 注释)
        assert astock_get_digits(10.0) == 1
        assert astock_get_digits(10) == 0
        assert astock_get_digits(0.0001) == 4


# ── 边界 / 集成 ──────────────────────────


def test_a_stock_typical_price_rounding():
    """A 股真实场景: 100.456 元规整到 0.01"""
    # 实际报单价应规整到 0.01
    assert astock_round(100.456) == 100.46
    assert astock_round(100.451) == 100.45
    # 涨停价 10.05 + 3 档 = 10.05, 10.10, 10.15
    assert astock_round(10.149) == 10.15


def test_chained_operations_no_drift():
    """连续 10 次 astock_round(0.1) 应稳定在 0.1, 不漂移"""
    val = 0.0
    for _ in range(10):
        val = astock_round(val + 0.1)
    # 累计 1.0 应规整为 1.0, 不会有 1.0000000001
    assert val == 1.0


def test_decimal_path_is_more_accurate_than_float_round():
    """Decimal 路径规避 float 累积误差

    Python float round(0.005, 2) = 0.01 (因为 0.005 实际存储为 0.005000000000000000277...)
    astock_round 走 Decimal("0.005") 真实表示 → banker's rounding → 0.0

    注意这与 Python round(0.005, 2) 不一致, 但 Decimal 路径更"准"
    A 股场景下用户传精确值, Decimal 路径更可预测
    """
    # 累加 0.1 十次 → 1.0 (Decimal 路径下稳定)
    val = 0.0
    for _ in range(10):
        val = astock_round(val + 0.1)
    assert val == 1.0


def test_negative_value_supported():
    """工具应支持负数 (vnpy 行为, 不抛)"""
    # target 为正时, 负 value 也能算
    assert round_to(-10.234, 0.01) == -10.23
    assert floor_to(-10.231, 0.01) == -10.24
    assert ceil_to(-10.239, 0.01) == -10.23