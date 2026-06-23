"""
Strategy 包 — 借鉴 vnpy.alpha.strategy 设计

vnpy 4.4 的策略三层架构:
  AlphaStrategy (基类)
    ├── EquityStrategy (选股策略, 类似 vnpy.alpha.strategy.strategies.equity_demo)
    └── [CTA / Portfolio / Pair Trading 等其他子类]

本项目当前聚焦:
  - AlphaStrategy: 通用策略接口 (on_init / on_bars / on_trade + buy/sell/cover/set_target)
  - EquityStrategy: 选股策略模板 (V6 / V龙头 后续可继承此模板)

与 vnpy 差异:
  - vnpy 用 Polars, 我们保留 pandas (兼容现有回测引擎)
  - vnpy 的 strategy_engine 强绑定 BacktestingEngine, 我们提供 Protocol 抽象
    支持 live trading 时切换到 MainEngine + Gateway
"""
from .alpha_strategy import AlphaStrategy
from .equity_strategy import EquityStrategy

__all__ = ["AlphaStrategy", "EquityStrategy"]
