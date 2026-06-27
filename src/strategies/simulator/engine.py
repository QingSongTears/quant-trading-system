"""
simulator.engine — SimulatorEngine 主类 (ADR-0011 #82)

从 simulator.py 拆分的 Simulator 主类 (薄壳 facade).
持有运行时状态 + 调度 event_loop / portfolio / persistence.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from ..trading.config import TradingConfig
from .event_loop import simulate_one
from .persistence import save_to_db
from .portfolio import calc_performance
from .portfolio_types import Position, TradeRecord
from .result_types import SimulationResult
from .signal_adapter import Signal, SignalAdapter

if TYPE_CHECKING:
    from ...risk.engine import RiskEngine
    from ...event import EventEngine

logger = logging.getLogger(__name__)


class SimulatorEngine:
    """模拟交易引擎 (拆分后版本)

    设计:
      - 薄壳 facade: 不持有实现细节, 只调度 event_loop / portfolio / persistence
      - 运行时状态: run_id / capital / positions / trades / equity_curve
      - RiskEngine 可选注入 (ADR-0011 D4-A): 不传则跳过风控, 保持向后兼容
      - 调用约定: run(codes, start_date, end_date, strategy_id) -> SimulationResult
    """

    def __init__(
        self,
        config: TradingConfig | None = None,
        risk_engine: "RiskEngine | None" = None,
        event_engine: "EventEngine | None" = None,
    ):
        self.config = config or TradingConfig()
        self.signal_adapter = SignalAdapter()
        self.risk_engine = risk_engine
        # ADR-0012 #83 D4-A: 可选 EventEngine 注入 (向后兼容)
        self.event_engine = event_engine

        # 运行时状态
        self.run_id = ""
        self.capital = self.config.initial_capital
        self.positions: dict[str, Position] = {}
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[dict] = []

    def run(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        strategy_id: int = 6,
    ) -> SimulationResult:
        """运行模拟交易"""
        self.run_id = uuid.uuid4().hex[:12]
        self.capital = self.config.initial_capital
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        result = SimulationResult(
            run_id=self.run_id,
            model=f"strategy_{strategy_id}",
            config=self.config.to_dict(),
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.config.initial_capital,
            status="running",
        )

        try:
            for code in codes:
                simulate_one(self, code, start_date, end_date, strategy_id)

            # 绩效计算
            calc_performance(
                result,
                initial_capital=self.config.initial_capital,
                final_capital=self.capital,
                equity_curve=self.equity_curve,
                trades=self.trades,
            )
            result.status = "done"

        except Exception as e:
            logger.exception("Simulator.run 失败")
            result.status = "error"
            result.error = str(e)

        # 持久化
        try:
            save_to_db(result, self.equity_curve, self.trades)
        except Exception as e:
            # 持久化失败不影响 result.status (保留 run 状态)
            logger.warning(f"save_to_db 失败 (run 结果仍可用): {e}")

        return result


__all__ = ["SimulatorEngine"]