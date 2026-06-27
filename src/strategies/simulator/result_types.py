"""
simulator.result_types — SimulationResult dataclass (ADR-0011 #82)

公开 dataclass: 从 simulator.py 顶层迁出.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimulationResult:
    """模拟交易结果"""
    run_id: str = ""
    model: str = ""
    config: dict = field(default_factory=dict)
    status: str = "running"  # running / done / error
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 0.0
    final_capital: float = 0.0
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    error: str = ""


__all__ = ["SimulationResult"]