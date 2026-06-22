"""
操盘策略配置模型
================
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


PositionMethod = Literal["fixed", "kelly", "atr", "turtle"]
StopLossMethod = Literal["fixed", "atr", "both"]
TakeProfitMethod = Literal["fixed", "trailing_atr"]


@dataclass
class TradingConfig:
    """操盘策略配置 — 可通过 dict() 从 JSON 反序列化"""

    # ── 仓位管理 ──
    position_method: PositionMethod = "atr"
    """仓位管理方法: fixed/kelly/atr/turtle"""

    risk_per_trade: float = 0.01
    """单笔风险比例 (1%)"""

    max_position_pct: float = 0.20
    """单股最大仓位比例 (20%)"""

    max_positions: int = 5
    """最多同时持仓数"""

    initial_capital: float = 100000.0
    """初始资金"""

    # ── 止损 ──
    stop_loss_method: StopLossMethod = "atr"
    """止损方式: fixed/atr"""

    atr_stop_mult: float = 2.0
    """ATR 止损倍数"""

    fixed_stop_pct: float = 0.08
    """固定止损比例 (8%)"""

    # ── 止盈 ──
    take_profit_method: TakeProfitMethod = "trailing_atr"
    """止盈方式: fixed/trailing_atr"""

    trailing_atr_mult: float = 2.0
    """移动止盈 ATR 倍数"""

    fixed_tp_pct: float = 0.15
    """固定止盈比例 (15%)"""

    # ── 时间止损 ──
    time_stop_enabled: bool = True
    """是否启用时间止损"""

    max_holding_days: int = 20
    """最多持有天数"""

    no_profit_days: int = 10
    """N 天不涨则清仓"""

    # ── 分批建仓 ──
    pyramiding_enabled: bool = False
    """是否启用金字塔加仓"""

    add_on_atr_mult: float = 2.0
    """每涨 N 倍 ATR 加仓一次"""

    max_adds: int = 2
    """最多加仓次数"""

    slippage_bps: float = 5.0
    """滑点 (bps, 默认 5bps=0.05%)"""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> TradingConfig:
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)
