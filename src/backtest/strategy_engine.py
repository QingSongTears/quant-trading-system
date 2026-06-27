"""
BacktestStrategyEngine — 回测用的 StrategyEngine 实现

实现 StrategyEngine Protocol，让 EquityStrategy 能在回测环境跑通。
构造 Dict[str, BarData] 喂给 on_bars()，自动驱动策略逻辑。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

from ..data.manager import get_data_manager
from ..gateway import BarData, Direction, Offset, OrderData, TradeData

if TYPE_CHECKING:
    from ..strategy.alpha_strategy import AlphaStrategy

logger = logging.getLogger(__name__)


class _InternalOrder:
    """引擎内部订单记录"""
    def __init__(
        self,
        vt_orderid: str,
        vt_symbol: str,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: int,
    ) -> None:
        self.vt_orderid = vt_orderid
        self.vt_symbol = vt_symbol
        self.direction = direction
        self.offset = offset
        self.price = price
        self.volume = volume
        self.traded: int = 0
        self.status: str = "SUBMITTED"

    def is_active(self) -> bool:
        return self.status in ("SUBMITTED", "PART_TRADED")


class BacktestStrategyEngine:
    """
    回测策略引擎 — 实现 StrategyEngine Protocol

    设计:
      - 用 LocalDatafeed 加载 K 线，构造 {vt_symbol: BarData}
      - 按交易历驱动 strategy.on_bars(bars)
      - 订单以「当日收盘价全部成交」简化模拟
      - 记录持仓、资金曲线、交易明细
    """
    def __init__(self, initial_cash: float = 1_000_000) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.pos_data: Dict[str, int] = defaultdict(int)
        self._strategies: Dict[str, "AlphaStrategy"] = {}
        self._orders: Dict[str, _InternalOrder] = {}
        self._order_seq: int = 0
        self._trade_seq: int = 0
        self._data_mgr = get_data_manager()
        self._equity_curve: list[dict] = []
        self._trade_records: list[dict] = []

    # ── Strategy 管理 ──────────────────────────────

    def add_strategy(
            self,
            strategy_class: Type["AlphaStrategy"],
            strategy_name: str,
            vt_symbols: List[str] | None = None,
            setting: Dict[str, Any] | None = None,
    ) -> "AlphaStrategy":
        """添加策略（实现 StrategyEngine Protocol 的注册接口）"""
        strategy = strategy_class(
            strategy_engine=self,
            strategy_name=strategy_name,
            vt_symbols=vt_symbols or [],
            setting=setting,
        )
        self._strategies[strategy_name] = strategy
        logger.info("注册策略: %s", strategy_name)
        return strategy

    def get_strategy(self, name: str = "") -> "AlphaStrategy":
        if not name:
            return next(iter(self._strategies.values()))
        return self._strategies[name]

    # ── 数据加载 ──────────────────────────────

    def load_bars(self, trade_date: date | str,
                        vt_symbols: List[str] | None = None) -> Dict[str, BarData]:
        """
        加载指定交易日的所有 K 线，返回 {vt_symbol: BarData}
        """
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)

        if vt_symbols is None:
            vt_symbols = []
            for s in self._strategies.values():
                vt_symbols.extend(s.vt_symbols)
            vt_symbols = list(set(vt_symbols))

        datafeed = self._data_mgr.datafeed
        bars: Dict[str, BarData] = {}
        for vt_symbol in vt_symbols:
            try:
                result = datafeed.get_bars(
                    vt_symbol, interval="1d",
                    start=trade_date, end=trade_date,
                )
                if result:
                    bars[vt_symbol] = result[0]
            except Exception as e:
                logger.warning("load_bars %s %s: %s", vt_symbol, trade_date, e)

        return bars

    # ── StrategyEngine Protocol 实现 ──────────────────────────────

    def send_order(
            self,
            strategy: "AlphaStrategy",
            vt_symbol: str,
            direction: Direction,
            offset: Offset,
            price: float,
            volume: int,
    ) -> List[str]:
        """回测环境：当日收盘价模拟全部成交"""
        order_id = f"{strategy.strategy_name}_{self._order_seq}"
        self._order_seq += 1

        order = _InternalOrder(order_id, vt_symbol, direction, offset, price, volume)
        self._orders[order_id] = order

        self._fill_order(order)

        return [order_id]

    def cancel_order(
            self, strategy: "AlphaStrategy", vt_orderid: str,
    ) -> None:
        if vt_orderid in self._orders:
            self._orders[vt_orderid].status = "CANCELLED"
            strategy.active_orderids.discard(vt_orderid)

    def write_log(self, msg: str, strategy: "AlphaStrategy" | None = None) -> None:
        name = strategy.strategy_name if strategy else "engine"
        logger.info("[%s] %s", name, msg)

    def get_cash_available(self) -> float:
        return self.cash

    def get_holding_value(self) -> float:
        return 0.0

    def get_portfolio_value(self) -> float:
        return self.cash + self.get_holding_value()

    # ── 内部成交模拟 ──────────────────────────────

    def _fill_order(self, order: _InternalOrder) -> None:
        """简化成交模拟（回测用）"""
        if order.direction == Direction.LONG:
            cost = order.price * order.volume
            if cost > self.cash:
                logger.warning("资金不足: 需要 %.0f, 可用 %.0f", cost, self.cash)
                return
            commission = max(cost * 0.00025, 5.0)
            self.cash -= cost + commission
            self.pos_data[order.vt_symbol] += order.volume
            order.traded = order.volume
            order.status = "ALL_TRADED"

        elif order.direction == Direction.SHORT:
            holding = self.pos_data.get(order.vt_symbol, 0)
            sell_vol = min(order.volume, holding)
            if sell_vol <= 0:
                return
            amount = order.price * sell_vol
            commission = max(amount * 0.00025, 5.0)
            tax = amount * 0.0005
            self.cash += amount - commission - tax
            self.pos_data[order.vt_symbol] -= sell_vol
            order.traded = sell_vol
            order.status = "ALL_TRADED"

        # 生成 TradeData 回调
        from ..gateway import Direction as _D
        trade = TradeData(
            gateway_name="BACKTEST",
            vt_orderid=order.vt_orderid,
            vt_symbol=order.vt_symbol,
            direction=order.direction,
            offset=order.offset,
            price=order.price,
            volume=order.traded,
        )
        strategy_name = order.vt_orderid.split("_")[0]
        strategy = self._strategies.get(strategy_name)
        if strategy:
            strategy.update_trade(trade)

        self._trade_seq += 1

    # ── 报告 ──────────────────────────────

    def get_report(self) -> dict:
        """获取回测报告"""
        return {
            "initial_cash": self.initial_cash,
            "final_cash": self.cash,
            "total_return": (self.cash / self.initial_cash - 1) * 100,
            "trades": len(self._trade_records),
            "equity_curve": self._equity_curve,
        }
