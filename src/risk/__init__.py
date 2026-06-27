"""
src.risk — 风控模块 (VNPY-3, 2026-06-27)

提供下单前风控检查, 在 set_target 之后拦截超限订单:
  - RiskEngine: 单笔/单日风控骨架
"""
from .engine import RiskEngine, RiskConfig

__all__ = ["RiskEngine", "RiskConfig"]
