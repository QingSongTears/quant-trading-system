"""
RiskEngine — 下单前风控检查骨架 (VNPY-3, ADR-0007, 2026-06-27)

借鉴 vnpy.trader.engine.RiskManager, 实现单笔/单日风控:
  - check_order: 单笔股数 / 金额 / 持仓数 / 仓位比例 / **单标的集中度 / 单行业集中度**
  - check_daily_limit: 交易次数 / 亏损 / 日回撤熔断
  - 事件驱动: 订阅 EVENT_ORDER / EVENT_TRADE 更新状态
  - EVENT_ACCOUNT 异步注入余额 (启动时用 initial_balance 兜底)
  - 日初自动复位

单位约定 (PR2.2 起, 见 src/constants/risk.py):
  - 百分比统一用**小数** (0.05 = 5%, 与 STOP_LOSS_DEFAULT 一致)
  - max_order_pct=0.20, max_daily_drawdown=0.05
  - sector_concentration_pct=0.40 (ADR-0007: 风控阶段更宽容)
  - single_symbol_concentration_pct=0.22 (对齐 MAX_SINGLE_POSITION_PCT)

用法:
    from src.risk import RiskEngine, RiskConfig
    from src.event import EventEngine, EVENT_ORDER, EVENT_TRADE, EVENT_ACCOUNT

    config = RiskConfig(max_order_pct=0.20, max_daily_trades=20,
                       initial_balance=200_000)
    risk = RiskEngine(event_engine, config)
    ok, msg = risk.check_order(req)
    ok, msg = risk.check_daily_limit()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..event import Event, EventEngine

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """风控配置

    所有阈值都是"上限", 触发即拒绝下单:
      - 单笔: 单股仓位比例 / 股数 / 金额 / **单标的占比 / 单行业占比**
      - 单日: 交易次数 / 亏损金额 / 日内净值回撤
      - 全局: 最大持仓数 (新开仓受限, 已持仓加仓放行)

    单位:
      - 百分比统一**小数** (0.05 = 5%, 见 src/constants/risk.py)
      - 金额单位: 元 (人民币)
    """
    # ── 单笔控制 ──
    max_order_pct: float = 0.20          # 单股最大仓位比例 (小数, 0.20=20%)
    max_order_volume: int = 100_000_000  # 单笔最大股数 (A 股单笔上限)
    max_order_amount: float = 5_000_000  # 单笔最大金额 (5百万, 小账户够用)

    # ── 单日控制 ──
    max_daily_trades: int = 50           # 日内最大交易次数 (双向)
    max_daily_drawdown: float = 0.05     # 日内净值回撤熔断 (小数, 0.05=5%)
    max_daily_loss: float = 100_000      # 日内最大亏损金额 (绝对值)

    # ── 全局控制 ──
    max_positions: int = 10              # 同时最大持仓数 (新开仓数)

    # ── 集中度 (ADR-0007 修复 3) ──
    # 注意: selection 阶段用 0.30 (更严, 防选股时过度集中)
    #       风控阶段用 0.40 (更宽容, 允许策略层加仓至单行业 40%)
    sector_concentration_pct: float = 0.40   # 单行业最大占比 (小数)
    # 与 src/constants/risk.py:44 MAX_SINGLE_POSITION_PCT 对齐
    single_symbol_concentration_pct: float = 0.22  # 单标的占比 (小数)

    # ── 账户兜底 (ADR-0007 D1②) ──
    initial_balance: float = 0.0         # 启动时账户余额 (EVENT_ACCOUNT 未到账前兜底)

    # ── 可注入 ──
    # sector_map: vt_symbol -> sector 字符串 (默认 src.risk.sector_map.get_sector)
    sector_map: Callable[[str], str] | None = None  # type: ignore[assignment]


class RiskEngine:
    """
    风控引擎 — 下单前拦截 (借鉴 vnpy RiskManager, 简写 RiskEngine)

    设计:
      - 单例绑定 EventEngine, __init__ 自动订阅 EVENT_ORDER / EVENT_TRADE
      - check_order() 在 send_order 之前调用, 返回 (通过, 原因)
      - check_daily_limit() 检查日内限制是否触发
      - 日初自动复位 (_ensure_daily_reset)
      - 持仓从成交事件流累计维护
      - 账户余额: 优先 EVENT_ACCOUNT 异步注入, 首次注入后注销回调省 CPU
                  未到账时用 config.initial_balance 兜底
      - 集中度: 单标的 + 单行业 (基于最后成交价 _last_prices 估算)
      - 双通道告警: check_order/daily_limit 拒绝时 put EVENT_RISK_ALERT
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

        # ── 集中度查表 (可注入) ──
        if self.config.sector_map is None:
            from .sector_map import get_sector as _default_sector_map
            self._sector_map: Callable[[str], str] = _default_sector_map
        else:
            self._sector_map = self.config.sector_map

        # ── 日统计 (每日 00:00 复位) ──
        self._daily_trades: int = 0
        self._daily_pnl: float = 0.0
        self._daily_peak: float = 0.0    # 日内累计 PnL 峰值 (熔断基准)
        self._today: date = date.min

        # ── 实时持仓 (从成交事件流累计) ──
        # vt_symbol -> 净持仓 (正=多头)
        self._positions: dict[str, int] = {}

        # ── 最近成交价 (集中度计算用, ADR-0007 修复 3) ──
        # vt_symbol -> last_price, EVENT_TRADE 更新, 无则 fallback 到当前订单价
        self._last_prices: dict[str, float] = {}

        # ── 账户余额 (ADR-0007 D1②) ──
        # 优先 EVENT_ACCOUNT 注入, 兜底用 config.initial_balance
        self._account_balance: float = self.config.initial_balance
        self._account_subscribed: bool = True  # False 时已注销回调

        # 注册事件 (vnpy 风格: 显式订阅, 不强制要求实现类在 main_engine)
        self._event_engine.register(EVENT_ORDER, self.on_order)
        self._event_engine.register(EVENT_TRADE, self.on_trade)

        # 尝试注册账户回调 (EVENT_ACCOUNT 可选, 测试场景可能没有)
        try:
            from ..event import EVENT_ACCOUNT
            self._event_engine.register(EVENT_ACCOUNT, self.on_account)
        except ImportError:
            logger.warning(
                "EVENT_ACCOUNT 不可用, 使用 initial_balance 兜底: "
                f"{self.config.initial_balance:,.0f}"
            )

        logger.info(
            f"RiskEngine 初始化完成: "
            f"max_vol={self.config.max_order_volume}, "
            f"max_daily_trades={self.config.max_daily_trades}, "
            f"sector_cap={self.config.sector_concentration_pct:.0%}, "
            f"single_cap={self.config.single_symbol_concentration_pct:.0%}, "
            f"initial_balance={self.config.initial_balance:,.0f}"
        )

    # ─────────────────────────────────────────
    #  事件回调
    # ─────────────────────────────────────────

    def on_order(self, event: "Event") -> None:
        """订单回报回调 — 仅 ALLTRADED 时累加日内交易次数 (ADR-0007 修复 4)

        vnpy 默认也是这样: 部分成交不算交易, 撤单/拒绝不计交易次数。
        部分成交通过 on_trade 累加, 此处只统计"完成交易的订单"。
        """
        from ..gateway.object import OrderStatus

        order = event.data
        if order is None:
            return
        if getattr(order, "status", None) != OrderStatus.ALLTRADED:
            return
        self._ensure_daily_reset()
        self._daily_trades += 1

    def on_trade(self, event: "Event") -> None:
        """成交回报回调 — 更新持仓 / 累计 PnL / 净值峰值 / 最近成交价"""
        self._ensure_daily_reset()
        trade = event.data
        if trade is None:
            return

        vt_symbol = getattr(trade, "vt_symbol", "")
        volume = getattr(trade, "volume", 0)
        price = getattr(trade, "price", 0.0)
        pnl = getattr(trade, "pnl", 0.0)

        if vt_symbol and volume:
            # 简化: 不区分多空方向, 用 volume 直接累加
            # A 股 T+1 单边, 实际是单向做多, 等于净持仓
            self._positions[vt_symbol] = self._positions.get(vt_symbol, 0) + int(volume)

        if vt_symbol and price > 0:
            # 集中度计算用最近成交价 (无行情推送时的合理 fallback)
            self._last_prices[vt_symbol] = float(price)

        if pnl:
            self._daily_pnl += pnl
            # 峰值只升不降 (用于日内回撤熔断)
            if self._daily_pnl > self._daily_peak:
                self._daily_peak = self._daily_pnl

    def on_account(self, event: "Event") -> None:
        """账户回报回调 — 首次注入余额后注销回调 (ADR-0007 D1②)

        设计:
          - 启动时 account 推送可能尚未触发, 用 initial_balance 兜底
          - 一旦收到有效余额 (balance > 0), 锁定并注销回调, 省 CPU
          - 注销失败不影响主流程 (容错: try/except 包住)
        """
        account = event.data
        if account is None:
            return
        balance = getattr(account, "balance", 0.0)
        if balance <= 0:
            # 异常账户数据, 不锁定 (继续监听)
            logger.debug(f"on_account: balance={balance} ≤ 0, 跳过")
            return

        self._account_balance = balance

        # 首次拿到有效余额后注销回调, 避免每个 timer tick 都触发
        if self._account_subscribed:
            try:
                from ..event import EVENT_ACCOUNT
                self._event_engine.unregister(EVENT_ACCOUNT, self.on_account)
                self._account_subscribed = False
                logger.info(f"账户余额已锁定, 注销 EVENT_ACCOUNT 回调: {balance:,.0f}")
            except Exception as e:  # noqa: BLE001 — 容错, 不阻断主流程
                logger.warning(f"注销 EVENT_ACCOUNT 回调失败 (继续监听): {e}")

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

        拒绝时同步 put EVENT_RISK_ALERT (level="warn") (ADR-0007 D3)
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
            return self._reject_order(
                vt_symbol,
                f"超单笔最大股数: {vol} > {self.config.max_order_volume}",
            )

        # 2. 单笔金额上限
        if amount > self.config.max_order_amount:
            return self._reject_order(
                vt_symbol,
                f"超单笔最大金额: {amount:,.0f} > {self.config.max_order_amount:,.0f}",
            )

        # 3. 最大持仓数 (新开仓受限, 已持仓加仓放行)
        current_positions = sum(1 for v in self._positions.values() if v > 0)
        held = self._positions.get(vt_symbol, 0)
        if held <= 0 and current_positions >= self.config.max_positions:
            return self._reject_order(
                vt_symbol,
                f"超最大持仓数: {current_positions} >= "
                f"{self.config.max_positions}, 新开仓被拒",
            )

        # 4. 单股仓位比例校验 (ADR-0007 修复 2)
        #    仅当账户余额已知时生效 (兜底 initial_balance=0 时跳过)
        if self._account_balance > 0:
            pct = amount / self._account_balance
            if pct > self.config.max_order_pct:
                return self._reject_order(
                    vt_symbol,
                    f"超单股仓位比例: {pct:.2%} > "
                    f"{self.config.max_order_pct:.2%}",
                )

        # 5. 单标的集中度校验 (ADR-0007 修复 3) — amount/balance > cap 则拒
        if self._account_balance > 0:
            single_cap = self.config.single_symbol_concentration_pct
            single_pct = amount / self._account_balance
            if single_pct > single_cap:
                return self._reject_order(
                    vt_symbol,
                    f"超单标的集中度: {single_pct:.2%} > {single_cap:.2%}",
                )

        # 6. 单行业集中度校验 (ADR-0007 修复 3)
        #    同行业已持仓 + 本笔金额 / 余额 > cap 则拒
        if self._account_balance > 0:
            cap = self.config.sector_concentration_pct
            sector = self._sector_map(vt_symbol)
            held_amount = self._sector_held_amount(sector)
            total_pct = (held_amount + amount) / self._account_balance
            if total_pct > cap:
                return self._reject_order(
                    vt_symbol,
                    f"超单行业集中度({sector}): "
                    f"已持 {held_amount:,.0f} + 本笔 {amount:,.0f} = "
                    f"{total_pct:.2%} > {cap:.2%}",
                )

        return True, ""

    def check_daily_limit(self) -> tuple[bool, str]:
        """
        今日限制检查 (触发熔断返回 False)

        Returns:
            (通过: bool, 原因: str)

        熔断时同步 put EVENT_RISK_ALERT (level="error") (ADR-0007 D3)
        """
        self._ensure_daily_reset()

        # 1. 交易次数
        if self._daily_trades >= self.config.max_daily_trades:
            return self._reject_daily(
                f"日内交易次数达上限: {self._daily_trades} "
                f">= {self.config.max_daily_trades}",
            )

        # 2. 亏损金额熔断
        if self._daily_pnl <= -self.config.max_daily_loss:
            return self._reject_daily(
                f"日内亏损达熔断线: {self._daily_pnl:,.0f} "
                f"<= -{self.config.max_daily_loss:,.0f}",
            )

        # 3. 日内回撤熔断 (基于日净值峰值, 小数化: 0.05 = 5%)
        if self._daily_peak > 0:
            drawdown = (self._daily_peak - self._daily_pnl) / self._daily_peak
            if drawdown >= self.config.max_daily_drawdown:
                return self._reject_daily(
                    f"日内回撤达熔断线: {drawdown:.2%} "
                    f">= {self.config.max_daily_drawdown:.2%}",
                )

        return True, ""

    # ─────────────────────────────────────────
    #  内部辅助
    # ─────────────────────────────────────────

    def _sector_held_amount(self, sector: str) -> float:
        """计算指定行业的当前持仓金额

        优先级:
          - 该行业下所有持仓的 _last_prices[vt_symbol] * volume
          - 若该 vt_symbol 无最近成交价, fallback 到 0 (保守: 忽略未知价持仓)

        未知行业 ("未知") 单独成一类, 不会与已有行业混算
        """
        total = 0.0
        for sym, vol in self._positions.items():
            if vol <= 0:
                continue
            if self._sector_map(sym) != sector:
                continue
            price = self._last_prices.get(sym, 0.0)
            total += price * vol
        return total

    def _reject_order(self, vt_symbol: str, reason: str) -> tuple[bool, str]:
        """单笔拒绝 → put EVENT_RISK_ALERT (warn) + 返回 (False, reason)"""
        self._emit_alert(reason=reason, level="warn", vt_symbol=vt_symbol)
        return False, reason

    def _reject_daily(self, reason: str) -> tuple[bool, str]:
        """日熔断拒绝 → put EVENT_RISK_ALERT (error) + 返回 (False, reason)"""
        self._emit_alert(reason=reason, level="error", vt_symbol="")
        return False, reason

    def _emit_alert(self, reason: str, level: str, vt_symbol: str) -> None:
        """put EVENT_RISK_ALERT — 容错, 失败不影响主流程 (ADR-0007 D3)"""
        try:
            from ..event import EVENT_RISK_ALERT
            from .event_data import RiskAlert
            self._event_engine.put(
                EVENT_RISK_ALERT,
                RiskAlert(reason=reason, level=level, vt_symbol=vt_symbol),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"EVENT_RISK_ALERT 推送失败 (主流程不受影响): {e}")

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
            "max_daily_drawdown": self.config.max_daily_drawdown,
            "sector_concentration_pct": self.config.sector_concentration_pct,
            "single_symbol_concentration_pct": self.config.single_symbol_concentration_pct,
            "position_count": sum(1 for v in self._positions.values() if v > 0),
            "account_balance": self._account_balance,
            "today": self._today.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<RiskEngine trades={self._daily_trades}/{self.config.max_daily_trades} "
            f"positions={sum(1 for v in self._positions.values() if v > 0)} "
            f"balance={self._account_balance:,.0f}>"
        )