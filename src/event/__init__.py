"""
事件类型 + EventEngine (借鉴 vnpy.trader.event + vnpy.event)

vnpy 用字符串前缀 + symbol 后缀区分全局/特定 symbol 事件:
  eTick (全局)
  eTick.000001.SZ (特定 symbol)

本项目简化: 只用全局事件, 不区分 symbol 后缀
"""
from .engine import EventEngine, Event

# ── 行情 / 数据 ─────────────────────────────
EVENT_TICK = "eTick"             # 行情推送 (实时)
EVENT_BAR = "eBar"               # K 线推送 (实时/历史)
EVENT_QUOTE = "eQuote"           # 五档行情

# ── 策略信号 ────────────────────────────────
EVENT_SIGNAL = "eSignal"         # 选股信号 (V6 / V龙头)
EVENT_TARGET = "eTarget"         # 目标持仓 (set_target)

# ── 交易 ────────────────────────────────────
EVENT_ORDER = "eOrder"           # 委托回报
EVENT_TRADE = "eTrade"           # 成交回报
EVENT_CANCEL = "eCancel"         # 撤单回报
EVENT_POSITION = "ePosition"     # 持仓变化
EVENT_ACCOUNT = "eAccount"       # 资金变化
EVENT_CONTRACT = "eContract"     # 合约信息

# ── 系统 ────────────────────────────────────
EVENT_LOG = "eLog"               # 日志
EVENT_ERROR = "eError"           # 错误
EVENT_TIMER = "eTimer"            # 定时器


__all__ = [
    "EventEngine", "Event",
    "EVENT_TICK", "EVENT_BAR", "EVENT_QUOTE",
    "EVENT_SIGNAL", "EVENT_TARGET",
    "EVENT_ORDER", "EVENT_TRADE", "EVENT_CANCEL",
    "EVENT_POSITION", "EVENT_ACCOUNT", "EVENT_CONTRACT",
    "EVENT_LOG", "EVENT_ERROR", "EVENT_TIMER",
]
