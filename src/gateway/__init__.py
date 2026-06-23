"""
Gateway 包 — A 股券商网关抽象 (借鉴 vnpy)

模块:
  - object: 统一数据对象 (TickData, OrderData, TradeData ...)
  - base_gateway: BaseGateway 抽象基类
  - main_engine: MainEngine (持有 EventEngine + 所有 gateways)

事件类型:
  - src.event 统一管理 (EVENT_TICK, EVENT_ORDER 等)

具体券商实现 (后续 PR 添加):
  - xtp_gateway.py (中泰证券 XTP)
  - ptrade_gateway.py (恒生 PTrade)
  - qmt_gateway.py (迅投 QMT)

详见 docs/Vnpy_Optimization_Notes.md
"""
from .object import (
    AccountData, BarData, BaseData, CancelRequest,
    ContractData, OrderData, OrderRequest, OrderStatus,
    OrderType, Offset, PositionData, SubscribeRequest,
    TickData, TradeData, Direction, ACTIVE_STATUSES,
)
from .base_gateway import BaseGateway
from .main_engine import MainEngine

__all__ = [
    # Enums
    "Direction", "Offset", "OrderType", "OrderStatus", "ACTIVE_STATUSES",
    # Data classes
    "BaseData", "TickData", "BarData",
    "OrderRequest", "OrderData", "TradeData",
    "PositionData", "AccountData", "ContractData",
    "SubscribeRequest", "CancelRequest",
    # Engines
    "BaseGateway", "MainEngine",
]
