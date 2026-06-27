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

# ── 风控 ────────────────────────────────────
EVENT_RISK_ALERT = "eRiskAlert"  # 风控告警 (下单前拦截/日内熔断触发, ADR-0007 D3)

# ── 监控 (ADR-0012 #83) ──────────────────────
EVENT_PNL_UPDATE = "ePnlUpdate"          # PnL 推送 (simulator 每日推送)
EVENT_POSITION_UPDATE = "ePositionUpdate"  # 持仓推送 (持仓变化时)
EVENT_ANOMALY = "eAnomaly"               # 异常事件 (数据延迟/API 失败/订单超时)
EVENT_ALERT = "eAlert"                   # 报警事件 (AlertRule 触发后)


__all__ = [
    "EventEngine", "Event",
    "EVENT_TICK", "EVENT_BAR", "EVENT_QUOTE",
    "EVENT_SIGNAL", "EVENT_TARGET",
    "EVENT_ORDER", "EVENT_TRADE", "EVENT_CANCEL",
    "EVENT_POSITION", "EVENT_ACCOUNT", "EVENT_CONTRACT",
    "EVENT_LOG", "EVENT_ERROR", "EVENT_TIMER",
    "EVENT_RISK_ALERT",
    "EVENT_PNL_UPDATE", "EVENT_POSITION_UPDATE",
    "EVENT_ANOMALY", "EVENT_ALERT",
]
