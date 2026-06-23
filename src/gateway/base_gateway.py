"""
BaseGateway — A 股券商网关基类 (借鉴 vnpy.trader.BaseGateway)

vnpy BaseGateway 设计要点 (已采纳):
  - 抽象方法 + 必须实现的接口清单
  - on_X 回调发布 Event 到 EventEngine
  - thread-safe + non-blocking 要求
  - 自动重连

A 股特化:
  - 国内券商: xtp / ptrade / qmt (中泰/恒生/迅投)
  - T+1 交易规则 (不允许当日卖)
  - 100 股整倍 (min_volume)
  - 涨跌停限制
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Type

from ..event import (
    Event, EventEngine,
    EVENT_TICK, EVENT_ORDER, EVENT_TRADE,
    EVENT_POSITION, EVENT_ACCOUNT, EVENT_CONTRACT,
)
from .object import (
    AccountData, CancelRequest, ContractData,
    OrderData, OrderRequest, PositionData,
    SubscribeRequest, TickData, TradeData,
)

logger = logging.getLogger(__name__)


class BaseGateway(ABC):
    """
    A 股券商网关基类

    继承此类实现具体券商 (xtp / ptrade / qmt):

        class XtpGateway(BaseGateway):
            default_name = "XTP"
            default_setting = {...}
            exchanges = [Exchange.SZ, Exchange.SH]

            def connect(self, setting): ...
            def send_order(self, req): ...

    借鉴 vnpy 设计:
      - default_name / default_setting / exchanges: 类属性声明
      - 抽象方法 + 必须实现的接口清单
      - on_X 回调: publish Event 到 EventEngine
      - thread-safe + non-blocking
    """

    # 子类必须覆盖
    default_name: str = ""
    default_setting: Dict[str, str] = {}
    exchanges: List[str] = []

    def __init__(self, event_engine: EventEngine, gateway_name: str = "") -> None:
        """
        Args:
            event_engine: 全局事件引擎
            gateway_name: 网关实例名 (default_name 为空时使用)
        """
        self.event_engine: EventEngine = event_engine
        self.gateway_name: str = gateway_name or self.default_name

        # 缓存最新数据
        self.accounts: Dict[str, AccountData] = {}
        self.positions: Dict[str, PositionData] = {}
        self.orders: Dict[str, OrderData] = {}
        self.trades: Dict[str, TradeData] = {}
        self.contracts: Dict[str, ContractData] = {}
        self.ticks: Dict[str, TickData] = {}

        # 连接状态
        self._connected: bool = False

    # ─────────────────────────────────────────
    #  必须实现的抽象方法
    # ─────────────────────────────────────────

    @abstractmethod
    def connect(self, setting: dict) -> None:
        """
        连接券商柜台 (非阻塞, 异步)

        Args:
            setting: 券商配置 (账号 / 密码 / 地址 等)
        Returns:
            None (连接结果通过 on_account / on_contract 回调推送)
        """

    @abstractmethod
    def close(self) -> None:
        """断开连接, 清理资源"""

    @abstractmethod
    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""

    @abstractmethod
    def send_order(self, req: OrderRequest) -> str:
        """
        报单

        Args:
            req: 报单请求

        Returns:
            orderid (vnpy 风格的 vt_orderid 的一部分, gateway 内唯一)
        """

    @abstractmethod
    def cancel_order(self, req: CancelRequest) -> None:
        """撤单"""

    @abstractmethod
    def query_account(self) -> None:
        """查询资金 (结果通过 on_account 回调推送)"""

    @abstractmethod
    def query_position(self) -> None:
        """查询持仓 (结果通过 on_position 回调推送)"""

    @abstractmethod
    def query_orders(self) -> None:
        """查询未成交委托 (结果通过 on_order 回调推送)"""

    @abstractmethod
    def query_trades(self) -> None:
        """查询当日成交 (结果通过 on_trade 回调推送)"""

    # ─────────────────────────────────────────
    #  回调: 发布 Event 到 EventEngine
    # ─────────────────────────────────────────

    def on_event(self, type: str, data) -> None:
        """通用事件发布"""
        self.event_engine.put(Event(type, data))

    def on_tick(self, tick: TickData) -> None:
        """行情推送"""
        self.ticks[tick.vt_symbol] = tick
        self.on_event(EVENT_TICK, tick)

    def on_trade(self, trade: TradeData) -> None:
        """成交回报"""
        self.trades[trade.vt_orderid] = trade
        self.on_event(EVENT_TRADE, trade)

    def on_order(self, order: OrderData) -> None:
        """委托回报"""
        self.orders[order.vt_orderid] = order
        self.on_event(EVENT_ORDER, order)

    def on_position(self, position: PositionData) -> None:
        """持仓回报"""
        key = position.vt_symbol
        self.positions[key] = position
        self.on_event(EVENT_POSITION, position)

    def on_account(self, account: AccountData) -> None:
        """资金回报"""
        self.accounts[account.accountid] = account
        self.on_event(EVENT_ACCOUNT, account)

    def on_contract(self, contract: ContractData) -> None:
        """合约回报"""
        self.contracts[contract.vt_symbol] = contract
        self.on_event(EVENT_CONTRACT, contract)

    # ─────────────────────────────────────────
    #  工具方法
    # ─────────────────────────────────────────

    def get_account(self) -> AccountData | None:
        """获取默认账户"""
        if not self.accounts:
            return None
        return next(iter(self.accounts.values()))

    def get_position(self, symbol: str, exchange: str = "") -> PositionData | None:
        """获取持仓 (按 vt_symbol)"""
        vt_symbol = f"{symbol}.{exchange}" if exchange else symbol
        return self.positions.get(vt_symbol)

    @property
    def connected(self) -> bool:
        return self._connected

    def write_log(self, msg: str, level: str = "info") -> None:
        """日志输出 (借鉴 vnpy Gateway.write_log)"""
        log_fn = getattr(logger, level, logger.info)
        log_fn(f"[{self.gateway_name}] {msg}")
