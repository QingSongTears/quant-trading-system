"""
src.risk — 风控模块 (VNPY-3, 2026-06-27)

提供下单前风控检查, 在 set_target 之后拦截超限订单:
  - RiskEngine: 单笔/单日风控骨架 (含集中度校验)
  - RiskAlert:  EVENT_RISK_ALERT 事件载荷 (ADR-0007 D3)
  - RiskConfig: 阈值配置 (单位: 百分比统一小数, 见 src/constants/risk.py)
  - sector_map: A 股 TOP20 持仓股行业字典 (v2.2 简化版, ADR-0007 修复 3)
"""
from .engine import RiskEngine, RiskConfig
from .event_data import RiskAlert
from . import sector_map as _sector_map

__all__ = ["RiskEngine", "RiskConfig", "RiskAlert", "sector_map"]