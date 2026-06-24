"""
AlphaStrategy — 策略基类 (借鉴 vnpy.alpha.strategy.template.AlphaStrategy)

线程安全 & 类型注解补齐 (2026-06-24):
  - 所有公共方法补齐签名
  - Protocol 6 个方法补齐签名 (仍用 ... 占位, 但有类型)
  - 内部状态读写依赖 strategy_engine 实现, 本基类不做加锁
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
    """策略引擎协议 (回测/实盘 统一接口)

    回测时由 BacktestingEngine 实现, 实盘时由 MainEngine 实现。
    AlphaStrategy 仅依赖此协议, 不直接耦合到具体引擎。
    """

    def send_order(
        self,
        strategy: "AlphaStrategy",
        vt_symbol: str,
        direction: "Direction",
        offset: "Offset",
        price: float,
        volume: int,
    ) -> List[str]: ...
    def cancel_order(
        self, strategy: "AlphaStrategy", vt_orderid: str,
    ) -> None: ...
    def write_log(self, msg: str, strategy: "AlphaStrategy") -> None: ...
    def get_cash_available(self) -> float: ...
    def get_holding_value(self) -> float: ...
    def get_signal(self) -> Any: ...


class AlphaStrategy(metaclass=ABCMeta):
    """Alpha 策略模板基类

    子类必须实现:
      - on_init(): 初始化 (预计算指标等)
      - on_bars(bars): K 线推送时调用
      - on_trade(trade): 成交回报时调用

    持仓/订单管理:
      - pos_data[vt_symbol] = 当前持仓量
      - target_data[vt_symbol] = 目标持仓量
      - orders[vt_orderid] = 所有订单
      - active_orderids = 未终结订单

    交易包装:
      - buy / sell / cover / short → send_order
      - cancel_order / cancel_all
      - set_target + execute_trading → 自动调仓
    """

    def __init__(
        self,
        strategy_engine: Any,
        strategy_name: str,
        vt_symbols: List[str],
        setting: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.strategy_engine: Any = strategy_engine
        self.strategy_name: str = strategy_name
        self.vt_symbols: List[str] = vt_symbols

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
    def on_init(self) -> None:
        """初始化回调: 预计算指标、订阅合约等"""

    @abstractmethod
    def on_bars(self, bars: Dict[str, "BarData"]) -> None:
        """K 线推送回调: 每个 bar 周期触发一次"""

    @abstractmethod
    def on_trade(self, trade: "TradeData") -> None:
        """成交回报回调: 每次成交触发"""

    def on_start(self) -> None:
        """启动回调: 默认仅打日志"""
        self.write_log(f"策略 {self.strategy_name} 启动")

    def on_stop(self) -> None:
        """停止回调: 默认打日志 + 撤销所有活动委托"""
        self.write_log(f"策略 {self.strategy_name} 停止")
        self.cancel_all()

    def update_trade(self, trade: "TradeData") -> None:
        """成交回报处理: 更新持仓后回调 on_trade"""
        from ..gateway import Direction
        if trade.direction == Direction.LONG:
            self.pos_data[trade.vt_symbol] += trade.volume
        else:
            self.pos_data[trade.vt_symbol] -= trade.volume
        self.on_trade(trade)

    def update_order(self, order: Any) -> None:
        """订单回报处理: 更新 orders 字典, 终态订单移出 active_orderids"""
        self.orders[order.vt_orderid] = order
        is_active = getattr(order, "is_active", None)
        if callable(is_active) and not is_active():
            self.active_orderids.discard(order.vt_orderid)
        elif isinstance(is_active, bool) and not is_active:
            # OrderData.is_active 是 property, 直接 bool 即可
            self.active_orderids.discard(order.vt_orderid)

    def buy(self, vt_symbol: str, price: float, volume: int) -> List[str]:
        """买入开仓 (LONG + OPEN)"""
        from ..gateway import Direction, Offset
        return self.send_order(
            vt_symbol, Direction.LONG, Offset.OPEN, price, volume,
        )

    def sell(self, vt_symbol: str, price: float, volume: int) -> List[str]:
        """卖出平仓 (SHORT + CLOSE), A 股 = 卖出持仓"""
        from ..gateway import Direction, Offset
        return self.send_order(
            vt_symbol, Direction.SHORT, Offset.CLOSE, price, volume,
        )

    def short(self, vt_symbol: str, price: float, volume: int) -> List[str]:
        """融券卖出开仓 (SHORT + OPEN), A 股不允许做空, 仅作协议对齐"""
        from ..gateway import Direction, Offset
        return self.send_order(
            vt_symbol, Direction.SHORT, Offset.OPEN, price, volume,
        )

    def cover(self, vt_symbol: str, price: float, volume: int) -> List[str]:
        """买入平仓 (LONG + CLOSE), A 股 = 融券买入归还"""
        from ..gateway import Direction, Offset
        return self.send_order(
            vt_symbol, Direction.LONG, Offset.CLOSE, price, volume,
        )

    def send_order(
        self,
        vt_symbol: str,
        direction: "Direction",
        offset: "Offset",
        price: float,
        volume: int,
    ) -> List[str]:
        """报单: 委托 strategy_engine.send_order, 返回 vt_orderid 列表"""
        vt_orderids: List[str] = self.strategy_engine.send_order(
            self, vt_symbol, direction, offset, price, volume,
        )
        for oid in vt_orderids:
            self.active_orderids.add(oid)
        return vt_orderids

    def cancel_order(self, vt_orderid: str) -> None:
        """撤销单个委托"""
        self.strategy_engine.cancel_order(self, vt_orderid)

    def cancel_all(self) -> None:
        """撤销所有活动委托 (active_orderids)"""
        for vt_orderid in list(self.active_orderids):
            self.cancel_order(vt_orderid)

    def get_pos(self, vt_symbol: str) -> float:
        """获取当前持仓"""
        return self.pos_data[vt_symbol]

    def get_target(self, vt_symbol: str) -> float:
        """获取目标持仓"""
        return self.target_data[vt_symbol]

    def set_target(self, vt_symbol: str, target: float) -> None:
        """设置目标持仓"""
        self.target_data[vt_symbol] = target

    def execute_trading(
        self, bars: Dict[str, "BarData"], price_add: float = 0.001,
    ) -> None:
        """执行调仓: 按 target_data 与 pos_data 差值发单

        Args:
            bars: 当前 K 线字典 {vt_symbol: BarData}
            price_add: 委托价偏移率, 默认 +0.1% (买入) / -0.1% (卖出)
        """
        self.cancel_all()
        for vt_symbol, bar in bars.items():
            target: float = self.get_target(vt_symbol)
            pos: float = self.get_pos(vt_symbol)
            diff: float = target - pos
            if diff > 0:
                order_price: float = bar.close_price * (1 + price_add)
                cover_vol: float = 0.0
                buy_vol: float = 0.0
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
                sell_vol: float = 0.0
                if pos > 0:
                    sell_vol = min(abs(diff), pos)
                else:
                    sell_vol = abs(diff)
                if sell_vol:
                    self.sell(vt_symbol, order_price, int(sell_vol))

    def get_cash_available(self) -> float:
        """获取可用资金"""
        return self.strategy_engine.get_cash_available()

    def get_holding_value(self) -> float:
        """获取持仓市值"""
        return self.strategy_engine.get_holding_value()

    def get_portfolio_value(self) -> float:
        """获取组合总价值 (现金 + 持仓)"""
        return self.get_cash_available() + self.get_holding_value()

    def write_log(self, msg: str) -> None:
        """写日志 (委托给 strategy_engine)"""
        self.strategy_engine.write_log(msg, self)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.strategy_name} vt_symbols={len(self.vt_symbols)}>"
        )