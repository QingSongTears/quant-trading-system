"""
src.risk — 风控模块 (VNPY-3, 2026-06-27)

提供下单前风控检查, 在 set_target 之后拦截超限订单:
  - RiskEngine: 单笔/单日风控骨架
  - RiskAlert:  EVENT_RISK_ALERT 事件载荷 (ADR-0007 D3)
  - RiskConfig: 阈值配置 (单位: 百分比统一小数, 见 src/constants/risk.py)
"""
from .engine import RiskEngine, RiskConfig
from .event_data import RiskAlert

__all__ = ["RiskEngine", "RiskConfig", "RiskAlert"]