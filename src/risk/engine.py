"""
RiskEngine — 下单前风控检查骨架 (VNPY-3, 2026-06-27)

借鉴 vnpy.trader.engine.RiskManager, 实现单笔/单日风控:
  - check_order: 单笔股数 / 金额 / 持仓数
  - check_daily_limit: 交易次数 / 亏损 / 日回撤熔断
  - 事件驱动: 订阅 EVENT_ORDER / EVENT_TRADE 更新状态
  - 日初自动复位

用法:
    from src.risk import RiskEngine, RiskConfig
    from src.event import EventEngine, EVENT_ORDER, EVENT_TRADE

    config = RiskConfig(max_order_pct=0.20, max_daily_trades=20)
    risk = RiskEngine(event_engine, config)
    # 注: 上面的 register 由 __init__ 自动完成
    ok, msg = risk.check_order(req)
    ok, msg = risk.check_daily_limit()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..event import Event, EventEngine

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """风控配置

    所有阈值都是"上限", 触发即拒绝下单:
      - 单笔: 单股仓位比例 / 股数 / 金额
      - 单日: 交易次数 / 亏损金额 / 日内净值回撤
      - 全局: 最大持仓数 (新开仓受限, 已持仓加仓放行)
    """
    # ── 单笔控制 ──
    max_order_pct: float = 0.20          # 单股最大仓位比例 (相对账户总资产, 留给将来)
    max_order_volume: int = 100_000_000  # 单笔最大股数 (A 股单笔上限)
    max_order_amount: float = 5_000_000  # 单笔最大金额 (5百万, 小账户够用)

    # ── 单日控制 ──
    max_daily_trades: int = 50           # 日内最大交易次数 (双向)
    max_daily_drawdown_pct: float = 5.0  # 日内净值回撤熔断 (%)
    max_daily_loss: float = 100_000      # 日内最大亏损金额 (绝对值)

    # ── 全局控制 ──
    max_positions: int = 10              # 同时最大持仓数 (新开仓数)


class RiskEngine:
    """
    风控引擎 — 下单前拦截 (借鉴 vnpy RiskManager)

    设计:
      - 单例绑定 EventEngine, __init__ 自动订阅 EVENT_ORDER / EVENT_TRADE
      - check_order() 在 send_order 之前调用, 返回 (通过, 原因)
      - check_daily_limit() 检查日内限制是否触发
      - 日初自动复位 (_ensure_daily_reset)
      - 持仓从成交事件流累计维护
    """

    def __init__(
        self,
        event_engine: "EventEngine",
        config: RiskConfig | None = None,
    ) -> None:
        # 延迟导入: 防循环依赖
        from ..event import EVENT_ORDER, EVENT_TRADE

        self.config = config or RiskConfig()
        self._event_engine = event_engine

        # ── 日统计 (每日 00:00 复位) ──
        self._daily_trades: int = 0
        self._daily_pnl: float = 0.0
        self._daily_peak: float = 0.0    # 日内累计 PnL 峰值 (熔断基准)
        self._today: date = date.min

        # ── 实时持仓 (从成交事件流累计) ──
        # vt_symbol -> 净持仓 (正=多头)
        self._positions: dict[str, int] = {}
        self._active: bool = False

        # 注册事件 (vnpy 风格: 显式订阅, 不强制要求实现类在 main_engine)
        self._event_engine.register(EVENT_ORDER, self.on_order)
        self._event_engine.register(EVENT_TRADE, self.on_trade)
        logger.info(
            f"RiskEngine 初始化完成: "
            f"max_vol={self.config.max_order_volume}, "
            f"max_daily_trades={self.config.max_daily_trades}"
        )

    # ─────────────────────────────────────────
    #  事件回调
    # ─────────────────────────────────────────

    def on_order(self, event: "Event") -> None:
        """订单回报回调 — 累加日内交易次数

        注: 严格说应该用 ALLTRADED 状态计数, 简化版: 任何订单回报 +1。
        vnpy 也是这样做的(可用参数配置, 我们默认简单策略)。
        """
        self._ensure_daily_reset()
        self._daily_trades += 1

    def on_trade(self, event: "Event") -> None:
        """成交回报回调 — 更新持仓 / 累计 PnL / 净值峰值"""
        self._ensure_daily_reset()
        trade = event.data
        if trade is None:
            return

        vt_symbol = getattr(trade, "vt_symbol", "")
        volume = getattr(trade, "volume", 0)
        pnl = getattr(trade, "pnl", 0.0)

        if vt_symbol and volume:
            # 简化: 不区分多空方向, 用 volume 直接累加
            # A 股 T+1 单边, 实际是单向做多, 等于净持仓
            self._positions[vt_symbol] = self._positions.get(vt_symbol, 0) + int(volume)

        if pnl:
            self._daily_pnl += pnl
            # 峰值只升不降 (用于日内回撤熔断)
            if self._daily_pnl > self._daily_peak:
                self._daily_peak = self._daily_pnl

    # ─────────────────────────────────────────
    #  风控检查 (下单前调用)
    # ─────────────────────────────────────────

    def check_order(self, order_req: Any) -> tuple[bool, str]:
        """
        单笔风控检查 (下单前调用)

        Args:
            order_req: 包含 volume / price / vt_symbol 的对象
                       (兼容 OrderRequest / DummyOrder / dict)

        Returns:
            (通过: bool, 原因: str) — msg 为空表示通过
        """
        # 兼容 dict 和 对象两种调用方式
        if isinstance(order_req, dict):
            vol = order_req.get("volume", 0)
            price = order_req.get("price", 0.0)
            vt_symbol = order_req.get("vt_symbol", "")
        else:
            vol = getattr(order_req, "volume", 0)
            price = getattr(order_req, "price", 0.0)
            vt_symbol = getattr(order_req, "vt_symbol", "")
        amount = vol * price

        # 1. 单笔股数上限 (A 股 100 万股是单笔上限, 设为更严的阈值)
        if vol > self.config.max_order_volume:
            return False, (
                f"超单笔最大股数: {vol} > {self.config.max_order_volume}"
            )

        # 2. 单笔金额上限
        if amount > self.config.max_order_amount:
            return False, (
                f"超单笔最大金额: {amount:,.0f} > {self.config.max_order_amount:,.0f}"
            )

        # 3. 最大持仓数 (新开仓受限, 已持仓加仓放行)
        current_positions = sum(1 for v in self._positions.values() if v > 0)
        held = self._positions.get(vt_symbol, 0)
        if held <= 0 and current_positions >= self.config.max_positions:
            return False, (
                f"超最大持仓数: {current_positions} >= "
                f"{self.config.max_positions}, 新开仓被拒"
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
                f"日内交易次数达上限: {self._daily_trades} "
                f">= {self.config.max_daily_trades}"
            )

        # 2. 亏损金额熔断
        if self._daily_pnl <= -self.config.max_daily_loss:
            return False, (
                f"日内亏损达熔断线: {self._daily_pnl:,.0f} "
                f"<= -{self.config.max_daily_loss:,.0f}"
            )

        # 3. 日内回撤熔断 (基于日净值峰值)
        if self._daily_peak > 0:
            drawdown = (self._daily_peak - self._daily_pnl) / self._daily_peak * 100
            if drawdown >= self.config.max_daily_drawdown_pct:
                return False, (
                    f"日内回撤达熔断线: {drawdown:.1f}% "
                    f">= {self.config.max_daily_drawdown_pct}%"
                )

        return True, ""

    # ─────────────────────────────────────────
    #  内部 / 调试
    # ─────────────────────────────────────────

    def _ensure_daily_reset(self) -> None:
        """跨日期时复位日内统计"""
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
            "daily_peak": self._daily_peak,
            "max_daily_trades": self.config.max_daily_trades,
            "max_daily_drawdown_pct": self.config.max_daily_drawdown_pct,
            "position_count": sum(1 for v in self._positions.values() if v > 0),
            "today": self._today.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<RiskEngine trades={self._daily_trades}/{self.config.max_daily_trades} "
            f"positions={sum(1 for v in self._positions.values() if v > 0)}>"
        )