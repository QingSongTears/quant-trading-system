"""
simulator 包 (ADR-0011 #82)

公开 API (向后兼容):
  - Signal / TradeRecord / Position / SimulationResult (4 个 dataclass)
  - SignalAdapter (信号适配)
  - Simulator (主类别名 -> SimulatorEngine)

拆分结构:
  - event_loop.py: 每日循环 (simulate_one)
  - portfolio.py: 仓位 + 绩效 (close_position + calc_performance)
  - portfolio_types.py: Position / TradeRecord dataclass
  - result_types.py: SimulationResult dataclass
  - persistence.py: DB 写入 (save_to_db)
  - signal_adapter.py: Signal dataclass + SignalAdapter
  - engine.py: SimulatorEngine 主类 (薄壳 facade)

向后兼容:
  from src.strategies.simulator import Signal, TradeRecord, Position,
                                         SimulationResult, SignalAdapter, Simulator
  全部仍可用 (从子模块 re-export).
"""
from __future__ import annotations

# 公开 dataclass
from .portfolio_types import Position, TradeRecord
from .result_types import SimulationResult
from .signal_adapter import Signal, SignalAdapter

# 主类 + 别名 (向后兼容)
from .engine import SimulatorEngine

# Simulator 是 SimulatorEngine 的别名 (公开 API 名)
Simulator = SimulatorEngine


__all__ = [
    "Signal",
    "TradeRecord",
    "Position",
    "SimulationResult",
    "SignalAdapter",
    "Simulator",
    "SimulatorEngine",
]