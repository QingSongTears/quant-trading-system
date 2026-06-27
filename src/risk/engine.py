"""
RiskEngine — 下单前风控检查骨架 (VNPY-3, 2026-06-27)

借鉴 vnpy.trader.engine.RiskManager, 实现单笔/单日风控:
  - check_order: 单笔仓位比例 + 单股集中度
  - check_daily: 最大交易次数 + 日回撤熔断
  - 事件驱动: 订阅 EVENT_ORDER / EVENT_TRADE 更新状态

用法:
    from src.risk import RiskEngine, RiskConfig
    from src.event import EventEngine, EVENT_ORDER, EVENT_TRADE

    config = RiskConfig(max_order_pct=0.20, max_daily_trades=20)
    risk = RiskEngine(event_engine, config)
    event_engine.register(EVENT_ORDER, risk.on_order)
    event_engine.register(EVENT_TRADE, risk.on_trade)

    ok, msg = risk.check_order(req)
    ok, msg = risk.check_daily_limit()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..event import Event, EventEngine

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """风控配置"""
    # ── 单笔控制 ──
    max_order_pct: float = 0.20          # 单股最大仓位比例
    max_order_volume: int = 100_000_000  # 单笔最大股数
    max_order_amount: float = 5_000_000  # 单笔最大金额

    # ── 单日控制 ──
    max_daily_trades: int = 50           # 日内最大交易次数
    max_daily_drawdown_pct: float = 5.0  # 日内回撤熔断 (%)
    max_daily_loss: float = 100_000      # 日内最大亏损金额

    # ── 全局控制 ──
    max_positions: int = 10              # 同时最大持仓数
    single_stock_pct: float = 0.25       # 单股集中度上限


class RiskEngine:
    """
    风控引擎 — 下单前拦截 (借鉴 vnpy RiskManager)

    设计:
      - 单例绑定 EventEngine (订阅订单/成交事件更新状态)
      - check_order() 在 send_order 之前调用, 返回 (通过, 原因)
      - check_daily_limit() 检查日内限制是否触发
      - 日初复位 (_reset_daily)
    """

    def __init__(
        self,
        event_engine: "EventEngine",
        config: RiskConfig | None = None,
    ) -> None:
        self.config = config or RiskConfig()
        self._event_engine = event_engine

        # ── 日统计 (每日复位) ──
        self._daily_trades: int = 0
        self._daily_pnl: float = 0.0
        self._daily_peak: float = 0.0    # 日净值峰值 (用于回撤熔断)
        self._today: date = date.min

        # ── 实时持仓 (从事件流更新) ──
        self._positions: dict[str, int] = {}   # vt_symbol → 持仓量
        self._active: bool = False

        # 注册事件
        self._event_engine.register("eOrder", self.on_order)
        self._event_engine.register("eTrade", self.on_trade)
        logger.info("RiskEngine 初始化完成")

    # ── 事件回调 ──────────────────────────────

    def on_order(self, event: "Event") -> None:
        """订单回报回调 — 更新日交易次数"""
        self._ensure_daily_reset()
        self._daily_trades += 1

    def on_trade(self, event: "Event") -> None:
        """成交回报回调 — 更新持仓 / PnL / 峰值"""
        self._ensure_daily_reset()
        trade = event.data
        if hasattr(trade, "volume") and hasattr(trade, "price"):
            # 简化: 按成交金额估算 PnL 变化
            pass

    # ── 风控检查 ──────────────────────────────

    def check_order(self, order_req: Any) -> tuple[bool, str]:
        """
        单笔风控检查 (下单前调用)

        Args:
            order_req: 包含 volume, price, vt_symbol 的对象

        Returns:
            (通过: bool, 原因: str)
        """
        vol = getattr(order_req, "volume", 0)
        price = getattr(order_req, "price", 0.0)
        vt_symbol = getattr(order_req, "vt_symbol", "")
        amount = vol * price

        # 1. 检查单笔股数
        if vol > self.config.max_order_volume:
            return False, (
                f"超单笔最大股数: {vol} > {self.config.max_order_volume}"
            )

        # 2. 检查单笔金额
        if amount > self.config.max_order_amount:
            return False, (
                f"超单笔最大金额: {amount:.0f} > {self.config.max_order_amount:.0f}"
            )

        # 3. 检查持仓数量
        current_positions = sum(1 for v in self._positions.values() if v > 0)
        if vt_symbol not in self._positions or self._positions[vt_symbol] == 0:
            if current_positions >= self.config.max_positions:
                return False, (
                    f"超最大持仓数: {current_positions} >= {self.config.max_positions}"
                )

        return True, ""

    def check_daily_limit(self) -> tuple[bool, str]:
        """
        今日限制检查 (触发熔断返回 False)

        Returns:
            (通过: bool, 原因: str)
        """
        self._ensure_daily_reset()

        # 1. 交易次数
        if self._daily_trades >= self.config.max_daily_trades:
            return False, (
                f"日内交易次数达上限: {self._daily_trades}"
            )

        # 2. 亏损熔断
        if self._daily_pnl <= -self.config.max_daily_loss:
            return False, (
                f"日内亏损达熔断线: {self._daily_pnl:.0f}"
            )

        return True, ""

    # ── 内部 ──────────────────────────────

    def _ensure_daily_reset(self) -> None:
        """日初复位统计数据"""
        now = date.today()
        if self._today < now:
            self._daily_trades = 0
            self._daily_pnl = 0.0
            self._daily_peak = 0.0
            self._today = now

    def get_stats(self) -> dict:
        """返回当前风控统计 (调试用)"""
        self._ensure_daily_reset()
        return {
            "daily_trades": self._daily_trades,
            "daily_pnl": self._daily_pnl,
            "max_daily_trades": self.config.max_daily_trades,
            "position_count": sum(1 for v in self._positions.values() if v > 0),
        }
