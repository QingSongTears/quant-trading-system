"""
src.utils — 通用工具函数
=========================

模块:
- market:  infer_market (SH/SZ/BJ 推断) — 消除 5+ 处硬编码 startswith
- keys:    normalize_keys (camelCase ↔ snake_case 适配器) — 消除 100+ 处字段名混用
- pricing: round_to / floor_to / ceil_to / get_digits — 借鉴 vnpy, 2026-06-24
"""
from __future__ import annotations
from .market import infer_market, normalize_code
from .keys import normalize_keys, snake_to_camel, camel_to_snake
from .pricing import (
    round_to, floor_to, ceil_to, get_digits,
    astock_round, astock_get_digits, ASTOCK_PRICETICK,
)

__all__ = [
    "infer_market",
    "normalize_code",
    "normalize_keys",
    "snake_to_camel",
    "camel_to_snake",
    "round_to",
    "floor_to",
    "ceil_to",
    "get_digits",
    "astock_round",
    "astock_get_digits",
    "ASTOCK_PRICETICK",
]