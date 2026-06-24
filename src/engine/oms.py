"""
OmsEngine — 订单管理系统 (借鉴 vnpy 4.4 trader.OmsEngine, 2026-06-24)

职责:
  - 全局缓存: ticks / orders / trades / positions / accounts / contracts
  - 订阅 EventEngine 6 类事件, 自动更新缓存
  - 提供单条/批量查询 API
  - 维护 active_orders 索引 (用于快速撤单)
  - A 股特化: 维护 yd_volume / td_volume (T+1 关键)

不实现 (与 vnpy 差异):
  - quotes (本项目无做市/期权场景)
  - OffsetConverter (A 股不允许做空, T+1 规则通过 available_volume 解决)

用法:
    from src.engine import OmsEngine

    me = MainEngine()
    oms = me.add_engine(OmsEngine)
    # 自动订阅 EVENT_TICK / EVENT_ORDER / EVENT_TRADE / EVENT_POSITION / EVENT_ACCOUNT / EVENT_CONTRACT
    # 直接查询:
    pos = oms.get_position("000001.SZ")
    orders = oms.get_all_active_orders()
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..event import (
    EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_ORDER, EVENT_POSITION,
    EVENT_TICK, EVENT_TRADE, Event,
)
from ..gateway import (
    AccountData, ContractData, OrderData, PositionData, TickData, TradeData,
)
from .base import BaseEngine

if TYPE_CHECKING:
    from ..event import EventEngine
    from ..gateway import MainEngine


class OmsEngine(BaseEngine):
    """
    订单管理引擎 — 全局缓存 + 事件订阅

    与 vnpy OmsEngine 差异:
      - 不缓存 quotes (本项目无做市/期权)
      - 不实现 OffsetConverter (A 股无做空, T+1 通过 available_volume 解决)
      - 增加 yd_volume / td_volume 字段维护 (T+1 关键)
    """

    def __init__(
        self,
        main_engine: "MainEngine" = None,
        event_engine: "EventEngine" = None,
        engine_name: str = "oms",
    ) -> None:
        super().__init__(main_engine, event_engine, engine_name)

        # ── 全局缓存 (借鉴 vnpy OmsEngine) ──
        self.ticks: Dict[str, TickData] = {}            # vt_symbol → TickData
        self.orders: Dict[str, OrderData] = {}          # vt_orderid → OrderData
        self.trades: Dict[str, TradeData] = {}          # vt_tradeid → TradeData
        self.positions: Dict[str, PositionData] = {}    # vt_symbol → PositionData
        self.accounts: Dict[str, AccountData] = {}      # vt_accountid → AccountData
        self.contracts: Dict[str, ContractData] = {}    # vt_symbol → ContractData

        # 活动订单索引 (未终结的, 用于快速撤单)
        self.active_orders: Dict[str, OrderData] = {}

    # ─────────────────────────────────────────
    #  生命周期 (override BaseEngine)
    # ─────────────────────────────────────────

    def start(self) -> None:
        """启动: 订阅 EventEngine 6 类事件"""
        if self.event_engine is None:
            raise RuntimeError(
                "OmsEngine 需要 event_engine 才能启动 (无 event_engine 无法订阅事件)"
            )
        # 启动前订阅
        self._register_events()
        # 调 super 设 is_active + log
        super().start()

    def stop(self) -> None:
        """停止: 解订阅 EventEngine 事件"""
        # 调 super 先设 is_active=False (防 stop 后还有事件)
        super().stop()
        # 解订阅
        if self.event_engine is not None:
            self._unregister_events()

    def _register_events(self) -> None:
        """订阅 6 类事件"""
        self.event_engine.register(EVENT_TICK, self._process_tick_event)
        self.event_engine.register(EVENT_ORDER, self._process_order_event)
        self.event_engine.register(EVENT_TRADE, self._process_trade_event)
        self.event_engine.register(EVENT_POSITION, self._process_position_event)
        self.event_engine.register(EVENT_ACCOUNT, self._process_account_event)
        self.event_engine.register(EVENT_CONTRACT, self._process_contract_event)

    def _unregister_events(self) -> None:
        """解订阅 6 类事件"""
        self.event_engine.unregister(EVENT_TICK, self._process_tick_event)
        self.event_engine.unregister(EVENT_ORDER, self._process_order_event)
        self.event_engine.unregister(EVENT_TRADE, self._process_trade_event)
        self.event_engine.unregister(EVENT_POSITION, self._process_position_event)
        self.event_engine.unregister(EVENT_ACCOUNT, self._process_account_event)
        self.event_engine.unregister(EVENT_CONTRACT, self._process_contract_event)

    # ─────────────────────────────────────────
    #  事件处理 (借鉴 vnpy OmsEngine)
    # ─────────────────────────────────────────

    def _process_tick_event(self, event: Event) -> None:
        tick: TickData = event.data
        self.ticks[tick.vt_symbol] = tick

    def _process_order_event(self, event: Event) -> None:
        order: OrderData = event.data
        self.orders[order.vt_orderid] = order

        # 活动订单索引维护
        if order.is_active:
            self.active_orders[order.vt_orderid] = order
        elif order.vt_orderid in self.active_orders:
            self.active_orders.pop(order.vt_orderid)

    def _process_trade_event(self, event: Event) -> None:
        trade: TradeData = event.data
        self.trades[trade.vt_tradeid] = trade

    def _process_position_event(self, event: Event) -> None:
        """持仓回报 — A 股特化: 维护 td_volume = volume - yd_volume"""
        position: PositionData = event.data
        # 自动计算今仓 (T+1 关键)
        position.td_volume = max(0.0, position.volume - position.yd_volume)
        self.positions[position.vt_symbol] = position

    def _process_account_event(self, event: Event) -> None:
        account: AccountData = event.data
        self.accounts[account.accountid] = account

    def _process_contract_event(self, event: Event) -> None:
        contract: ContractData = event.data
        self.contracts[contract.vt_symbol] = contract

    # ─────────────────────────────────────────
    #  单条查询 (借鉴 vnpy)
    # ─────────────────────────────────────────

    def get_tick(self, vt_symbol: str) -> Optional[TickData]:
        return self.ticks.get(vt_symbol)

    def get_order(self, vt_orderid: str) -> Optional[OrderData]:
        return self.orders.get(vt_orderid)

    def get_trade(self, vt_tradeid: str) -> Optional[TradeData]:
        return self.trades.get(vt_tradeid)

    def get_position(self, vt_symbol: str) -> Optional[PositionData]:
        return self.positions.get(vt_symbol)

    def get_account(self, vt_accountid: str = "") -> Optional[AccountData]:
        """默认返回第一个 account (A 股通常只有一个)"""
        if vt_accountid:
            return self.accounts.get(vt_accountid)
        if not self.accounts:
            return None
        return next(iter(self.accounts.values()))

    def get_contract(self, vt_symbol: str) -> Optional[ContractData]:
        return self.contracts.get(vt_symbol)

    # ─────────────────────────────────────────
    #  批量查询 (借鉴 vnpy)
    # ─────────────────────────────────────────

    def get_all_ticks(self) -> List[TickData]:
        return list(self.ticks.values())

    def get_all_orders(self) -> List[OrderData]:
        return list(self.orders.values())

    def get_all_trades(self) -> List[TradeData]:
        return list(self.trades.values())

    def get_all_positions(self) -> List[PositionData]:
        return list(self.positions.values())

    def get_all_accounts(self) -> List[AccountData]:
        return list(self.accounts.values())

    def get_all_contracts(self) -> List[ContractData]:
        return list(self.contracts.values())

    def get_all_active_orders(self) -> List[OrderData]:
        """获取所有活动订单 (未终结, 可撤)"""
        return list(self.active_orders.values())

    # ─────────────────────────────────────────
    #  A 股 T+1 工具
    # ─────────────────────────────────────────

    def get_sellable_volume(self, vt_symbol: str) -> float:
        """A 股可卖数量 (T+1)

        等同于 PositionData.available_volume
        """
        pos = self.get_position(vt_symbol)
        if pos is None:
            return 0.0
        return pos.available_volume

    def has_long_position(self, vt_symbol: str) -> bool:
        """是否有多头持仓 (A 股永远只有多)"""
        pos = self.get_position(vt_symbol)
        if pos is None:
            return False
        return pos.volume > 0

    # ─────────────────────────────────────────
    #  调试
    # ─────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "engine_name": self.engine_name,
            "is_active": self.is_active,
            "ticks": len(self.ticks),
            "orders": len(self.orders),
            "trades": len(self.trades),
            "positions": len(self.positions),
            "accounts": len(self.accounts),
            "contracts": len(self.contracts),
            "active_orders": len(self.active_orders),
        }