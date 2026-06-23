"""
AlphaStrategy — 策略基类 (借鉴 vnpy.alpha.strategy.template.AlphaStrategy)
"""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Set

if TYPE_CHECKING:
    from ..gateway import BarData, Direction, Offset, TradeData


logger = logging.getLogger(__name__)


class StrategyEngine(Protocol):
    """策略引擎协议 (回测/实盘 统一接口)"""

    def send_order(self, strategy, vt_symbol, direction, offset, price, volume): ...
    def cancel_order(self, strategy, vt_orderid): ...
    def write_log(self, msg, strategy): ...
    def get_cash_available(self): ...
    def get_holding_value(self): ...
    def get_signal(self): ...


class AlphaStrategy(metaclass=ABCMeta):
    """Alpha 策略模板基类"""

    def __init__(self, strategy_engine, strategy_name, vt_symbols, setting=None):
        self.strategy_engine = strategy_engine
        self.strategy_name = strategy_name
        self.vt_symbols = vt_symbols

        self.pos_data: Dict[str, float] = defaultdict(float)
        self.target_data: Dict[str, float] = defaultdict(float)
        self.orders: Dict[str, Any] = {}
        self.active_orderids: Set[str] = set()

        self.inited: bool = False
        self.trading: bool = False

        for k, v in (setting or {}).items():
            if hasattr(self, k):
                setattr(self, k, v)

    @abstractmethod
    def on_init(self) -> None: ...
    @abstractmethod
    def on_bars(self, bars: Dict[str, "BarData"]) -> None: ...
    @abstractmethod
    def on_trade(self, trade: "TradeData") -> None: ...

    def on_start(self) -> None:
        self.write_log(f"策略 {self.strategy_name} 启动")

    def on_stop(self) -> None:
        self.write_log(f"策略 {self.strategy_name} 停止")
        self.cancel_all()

    def update_trade(self, trade: "TradeData") -> None:
        from ..gateway import Direction
        if trade.direction == Direction.LONG:
            self.pos_data[trade.vt_symbol] += trade.volume
        else:
            self.pos_data[trade.vt_symbol] -= trade.volume
        self.on_trade(trade)

    def update_order(self, order: Any) -> None:
        self.orders[order.vt_orderid] = order
        is_active = getattr(order, "is_active", None)
        if callable(is_active) and not is_active():
            self.active_orderids.discard(order.vt_orderid)

    def buy(self, vt_symbol, price, volume):
        from ..gateway import Direction, Offset
        return self.send_order(vt_symbol, Direction.LONG, Offset.OPEN, price, volume)

    def sell(self, vt_symbol, price, volume):
        from ..gateway import Direction, Offset
        return self.send_order(vt_symbol, Direction.SHORT, Offset.CLOSE, price, volume)

    def short(self, vt_symbol, price, volume):
        from ..gateway import Direction, Offset
        return self.send_order(vt_symbol, Direction.SHORT, Offset.OPEN, price, volume)

    def cover(self, vt_symbol, price, volume):
        from ..gateway import Direction, Offset
        return self.send_order(vt_symbol, Direction.LONG, Offset.CLOSE, price, volume)

    def send_order(self, vt_symbol, direction, offset, price, volume):
        vt_orderids = self.strategy_engine.send_order(self, vt_symbol, direction, offset, price, volume)
        for oid in vt_orderids:
            self.active_orderids.add(oid)
        return vt_orderids

    def cancel_order(self, vt_orderid):
        self.strategy_engine.cancel_order(self, vt_orderid)

    def cancel_all(self):
        for vt_orderid in list(self.active_orderids):
            self.cancel_order(vt_orderid)

    def get_pos(self, vt_symbol):
        return self.pos_data[vt_symbol]

    def get_target(self, vt_symbol):
        return self.target_data[vt_symbol]

    def set_target(self, vt_symbol, target):
        self.target_data[vt_symbol] = target

    def execute_trading(self, bars, price_add=0.001):
        self.cancel_all()
        for vt_symbol, bar in bars.items():
            target = self.get_target(vt_symbol)
            pos = self.get_pos(vt_symbol)
            diff = target - pos
            if diff > 0:
                order_price = bar.close_price * (1 + price_add)
                cover_vol = 0
                buy_vol = 0
                if pos < 0:
                    cover_vol = min(diff, abs(pos))
                    buy_vol = diff - cover_vol
                else:
                    buy_vol = diff
                if cover_vol:
                    self.cover(vt_symbol, order_price, int(cover_vol))
                if buy_vol:
                    self.buy(vt_symbol, order_price, int(buy_vol))
            elif diff < 0:
                order_price = bar.close_price * (1 - price_add)
                sell_vol = 0
                if pos > 0:
                    sell_vol = min(abs(diff), pos)
                else:
                    sell_vol = abs(diff)
                if sell_vol:
                    self.sell(vt_symbol, order_price, int(sell_vol))

    def get_cash_available(self):
        return self.strategy_engine.get_cash_available()

    def get_holding_value(self):
        return self.strategy_engine.get_holding_value()

    def get_portfolio_value(self):
        return self.get_cash_available() + self.get_holding_value()

    def write_log(self, msg):
        self.strategy_engine.write_log(msg, self)

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.strategy_name} vt_symbols={len(self.vt_symbols)}>"
