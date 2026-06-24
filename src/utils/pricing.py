"""
价格规整工具 — 借鉴 vnpy.trader.utility (round_to / floor_to / ceil_to / get_digits)

用 Decimal 而非 float 算术, 规避二进制浮点误差:
  - 0.1 + 0.2 = 0.30000000000000004 (float)
  - Decimal("0.1") + Decimal("0.2") = 0.3 (Decimal)

A 股场景:
  - 最小价格变动 0.01 元 (pricetick)
  - 调用 astock_round(price) 即可按 A 股 tick 规整
"""
from __future__ import annotations

import math
from decimal import Decimal


def round_to(value: float, target: float) -> float:
    """
    按 target 步长规整 value (四舍五入)

    Args:
        value: 待规整的数值
        target: 规整步长, A 股价格 = 0.01

    Returns:
        规整后的 float

    Example:
        >>> round_to(10.234, 0.01)
        10.23
        >>> round_to(10.235, 0.01)
        10.24
        >>> round_to(1234, 100)  # 大宗交易 100 元手
        1200.0
    """
    decimal_value: Decimal = Decimal(str(value))
    decimal_target: Decimal = Decimal(str(target))
    return float(
        int(round(float(decimal_value / decimal_target))) * decimal_target
    )


def floor_to(value: float, target: float) -> float:
    """
    按 target 步长向下取整

    Args:
        value: 待规整的数值
        target: 规整步长

    Returns:
        向下取整后的 float

    Example:
        >>> floor_to(10.239, 0.01)
        10.23
    """
    decimal_value: Decimal = Decimal(str(value))
    decimal_target: Decimal = Decimal(str(target))
    return float(
        int(math.floor(float(decimal_value / decimal_target))) * decimal_target
    )


def ceil_to(value: float, target: float) -> float:
    """
    按 target 步长向上取整

    Args:
        value: 待规整的数值
        target: 规整步长

    Returns:
        向上取整后的 float

    Example:
        >>> ceil_to(10.231, 0.01)
        10.24
    """
    decimal_value: Decimal = Decimal(str(value))
    decimal_target: Decimal = Decimal(str(target))
    return float(
        int(math.ceil(float(decimal_value / decimal_target))) * decimal_target
    )


def get_digits(value: float) -> int:
    """
    获取 value 的小数位数 (尾部 0 忽略)

    Args:
        value: 数值

    Returns:
        小数位数

    Example:
        >>> get_digits(10.23)
        2
        >>> get_digits(10.0)
        0
        >>> get_digits(0.0001)
        4
    """
    return Decimal(str(value)).as_tuple().exponent * -1


# ── A 股特化 helper ──────────────────────────


# A 股最小价格变动 (1 分钱)
ASTOCK_PRICETICK: float = 0.01


def astock_round(price: float, pricetick: float = ASTOCK_PRICETICK) -> float:
    """
    A 股价格按 pricetick 规整 (默认 0.01)

    等价于 round_to(price, 0.01), 表达更简洁

    Args:
        price: 原始价格
        pricetick: 最小价格变动, 默认 0.01 (A 股主板)
                   ETF/可转债可能不同, 可覆盖

    Returns:
        规整后的价格 (float, 2 位小数)

    Example:
        >>> astock_round(10.234)
        10.23
        >>> astock_round(10.235)
        10.24
    """
    return round_to(price, pricetick)


def astock_get_digits(price: float) -> int:
    """
    A 股价格小数位数 (默认 2)

    等价于 get_digits(price), 仅作语义明确

    Example:
        >>> astock_get_digits(10.23)
        2
    """
    return get_digits(price)