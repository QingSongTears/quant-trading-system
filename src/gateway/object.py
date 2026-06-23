"""
Gateway 数据对象 (借鉴 vnpy.trader.object, 适配 A 股)

vnpy 用 dataclass + BaseData 基类, 我们沿用此模式:
  - 所有数据对象继承 BaseData
  - 含 gateway_name 标识来源
  - 标准字段跨券商/模拟器一致

设计要点:
  - TickData / BarData: 行情数据
  - OrderRequest / OrderData / TradeData: 交易回报
  - PositionData / AccountData: 资金持仓
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


# ── 枚举常量 (借鉴 vnpy.trader.constant) ──────


class Direction(Enum):
    """买卖方向"""
    LONG = "多"      # 买入 / 持有多头
    SHORT = "空"     # 卖出 / 持有空头


class Offset(Enum):
    """开平标志"""
    OPEN = "开"
    CLOSE = "平"
    CLOSE_TODAY = "平今"
    CLOSE_YESTERDAY = "平昨"


class OrderType(Enum):
    """委托类型 (A 股常用)"""
    LIMIT = "限价"        # 限价单
    MARKET = "市价"        # 市价单 (科创板/创业板支持)
    BEST = "最优"          # 即时成交剩余撤销


class OrderStatus(Enum):
    """委托状态"""
    SUBMITTING = "提交中"
    NOTTRADED = "未成交"
    PARTTRADED = "部分成交"
    ALLTRADED = "全部成交"
    CANCELLED = "已撤单"
    REJECTED = "已拒绝"


ACTIVE_STATUSES = {OrderStatus.SUBMITTING, OrderStatus.NOTTRADED, OrderStatus.PARTTRADED}


# ── 基础数据类 ──────────────────────────────


@dataclass
class BaseData:
    """所有数据对象的基类 (借鉴 vnpy)"""
    gateway_name: str = ""

    extra: Optional[dict] = field(default=None, init=False)

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}


# ── 行情数据 ────────────────────────────────


@dataclass
class TickData(BaseData):
    """Tick 行情 (盘口 + 最新价)"""
    symbol: str = ""               # 股票代码 (e.g. "000001")
    exchange: str = ""            # 交易所 (SZ / SH / BJ)
    datetime: datetime = field(default_factory=datetime.now)
    name: str = ""

    last_price: float = 0.0       # 最新价
    last_volume: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    pre_close: float = 0.0

    volume: float = 0.0           # 累计成交量 (股)
    turnover: float = 0.0         # 累计成交额 (元)
    open_interest: float = 0.0    # A 股无意义, 保留字段

    limit_up: float = 0.0         # 涨停价
    limit_down: float = 0.0       # 跌停价

    bid_price_1: float = 0.0
    bid_volume_1: float = 0.0
    ask_price_1: float = 0.0
    ask_volume_1: float = 0.0

    @property
    def vt_symbol(self) -> str:
        """vnpy 风格 vt_symbol: SYMBOL.EXCHANGE"""
        return f"{self.symbol}.{self.exchange}"


@dataclass
class BarData(BaseData):
    """K 线数据"""
    symbol: str = ""
    exchange: str = ""
    datetime: datetime = field(default_factory=datetime.now)
    interval: str = "1d"          # 1d / 1m / 5m etc.
    name: str = ""

    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    close_price: float = 0.0

    volume: float = 0.0
    turnover: float = 0.0
    open_interest: float = 0.0

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


# ── 交易请求 ────────────────────────────────


@dataclass
class OrderRequest:
    """报单请求 (调用 gateway.send_order 的入参)"""
    symbol: str
    exchange: str
    direction: Direction
    offset: Offset
    order_type: OrderType
    price: float = 0.0
    volume: int = 0              # 100 的整数倍
    reference: str = ""          # 用户备注


# ── 交易回报 ────────────────────────────────


@dataclass
class OrderData(BaseData):
    """委托回报"""
    symbol: str = ""
    exchange: str = ""
    orderid: str = ""

    direction: Optional[Direction] = None
    offset: Optional[Offset] = None
    order_type: Optional[OrderType] = None
    status: Optional[OrderStatus] = None

    price: float = 0.0
    volume: float = 0.0
    traded: float = 0.0          # 已成交
    left: float = 0.0            # 剩余

    datetime: Optional[datetime] = None
    reference: str = ""

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"

    @property
    def vt_orderid(self) -> str:
        """vnpy 风格 vt_orderid: gateway.orderid"""
        return f"{self.gateway_name}.{self.orderid}"

    @property
    def is_active(self) -> bool:
        """是否仍可成交"""
        return self.status in ACTIVE_STATUSES


@dataclass
class TradeData(BaseData):
    """成交回报"""
    symbol: str = ""
    exchange: str = ""
    orderid: str = ""
    tradeid: str = ""

    direction: Optional[Direction] = None
    offset: Optional[Offset] = None

    price: float = 0.0
    volume: float = 0.0

    datetime: Optional[datetime] = None

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"

    @property
    def vt_orderid(self) -> str:
        return f"{self.gateway_name}.{self.orderid}"


# ── 资金 / 持仓 ──────────────────────────────


@dataclass
class PositionData(BaseData):
    """持仓"""
    symbol: str = ""
    exchange: str = ""
    direction: Optional[Direction] = None

    volume: float = 0.0          # 持仓数量 (股)
    frozen: float = 0.0          # 冻结数量
    price: float = 0.0           # 平均成本
    pnl: float = 0.0             # 浮动盈亏

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


@dataclass
class AccountData(BaseData):
    """资金账户"""
    accountid: str = ""

    balance: float = 0.0         # 总资产
    frozen: float = 0.0          # 冻结资金
    available: float = 0.0       # 可用资金
    commission: float = 0.0      # 累计手续费
    margin: float = 0.0          # 占用保证金 (A 股无, 保留)


# ── 合约 / 订阅 ──────────────────────────────


@dataclass
class ContractData(BaseData):
    """合约 (A 股 = 股票基本信息)"""
    symbol: str = ""
    exchange: str = ""
    name: str = ""
    product: str = "STOCK"        # A 股都是 STOCK

    size: int = 1                # 合约乘数 (A 股 = 1)
    pricetick: float = 0.01      # 最小价格变动 (A 股 = 0.01)
    min_volume: int = 100        # 最小交易单位 (A 股 = 100)

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


@dataclass
class SubscribeRequest:
    """订阅请求"""
    symbol: str
    exchange: str = ""

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange}"


@dataclass
class CancelRequest:
    """撤单请求"""
    orderid: str
    symbol: str = ""
    exchange: str = ""
