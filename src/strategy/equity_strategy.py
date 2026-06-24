"""
EquityStrategy — A 股选股策略模板 (借鉴 vnpy.alpha.strategy.strategies.equity_demo)
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .alpha_strategy import AlphaStrategy

if TYPE_CHECKING:
    from ..gateway import BarData, TradeData


logger = logging.getLogger(__name__)


class EquityStrategy(AlphaStrategy):
    """
    A 股选股策略模板 (借鉴 vnpy EquityDemoStrategy)

    子类必须实现:
      - generate_signals() -> DataFrame: columns=[vt_symbol, signal, ...]

    默认行为:
      - Top-K 持仓 (按 signal 排序)
      - 月度再平衡 (rebalance_days 个 bar)
      - 最少持有 min_days 天 (防抖动)
      - 等权分配 (cash_ratio / top_k)
      - 100 股整倍 (min_volume)
    """

    # ── 默认参数 (子类可在 setting 覆盖) ──────
    top_k: int = 30
    rebalance_days: int = 20
    min_days: int = 3
    cash_ratio: float = 0.95
    min_volume: int = 100
    open_rate: float = 0.00025
    close_rate: float = 0.00075
    min_commission: float = 5.0
    price_add: float = 0.001

    def __init__(
        self,
        strategy_engine: Any,
        strategy_name: str,
        vt_symbols: List[str],
        setting: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)
        self.holding_days: Dict[str, int] = defaultdict(int)
        self._bars_since_rebalance: int = 0

    @abstractmethod
    def generate_signals(self) -> "Any":
        """
        生成每只股票的信号

        Returns:
            DataFrame: columns=[vt_symbol, signal, ...]
            signal 越大越优先持仓 (Top-K 排序)
        """

    def filter_universe(self, vt_symbols: List[str], bars: Dict[str, "BarData"]) -> List[str]:
        return [s for s in vt_symbols if s in bars]

    def get_cash_per_stock(self, total_value: float) -> float:
        return total_value * self.cash_ratio / max(self.top_k, 1)

    def on_bars(self, bars: Dict[str, "BarData"]) -> None:
        # 1. 更新持仓天数
        for vt_symbol in list(self.pos_data.keys()):
            if self.pos_data[vt_symbol] > 0:
                self.holding_days[vt_symbol] += 1
            else:
                self.holding_days.pop(vt_symbol, None)

        # 2. 再平衡判断 (每 rebalance_days 个 bar 一次)
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < self.rebalance_days:
            return
        self._bars_since_rebalance = 0

        # 3. 过滤股票池 (当前 K 线切片里有数据的)
        universe = self.filter_universe(self.vt_symbols, bars)
        if len(universe) < self.top_k:
            self.write_log(
                f"可用股票数 {len(universe)} < top_k {self.top_k}, 跳过本次再平衡"
            )
            return

        # 4. 生成信号
        signals = self.generate_signals()
        if signals is None or signals.empty:
            return

        # 4.5 校验必需列 (2026-06-24 修复: 防子类返回缺列 DataFrame 时崩)
        required_cols = {"vt_symbol", "signal"}
        missing = required_cols - set(signals.columns)
        if missing:
            raise ValueError(
                f"{self.__class__.__name__}.generate_signals() "
                f"返回的 DataFrame 缺少必需列: {missing}; "
                f"实际 columns={list(signals.columns)}"
            )

        # 5. 取 Top-K (按 signal 降序)
        signals = signals.sort_values("signal", ascending=False).head(self.top_k)
        active_symbols = set(signals["vt_symbol"])

        # 6. 卖出列表: 持仓不在新 Top-K 且已超最少持有期
        sell_symbols: set = set()
        for vt_symbol, pos in list(self.pos_data.items()):
            if pos > 0 and vt_symbol not in active_symbols:
                if self.holding_days.get(vt_symbol, 0) >= self.min_days:
                    sell_symbols.add(vt_symbol)
                    continue
            if pos > 0:
                active_symbols.add(vt_symbol)

        # 7. 计算目标持仓 (等权 + 100 股整倍)
        total_value = self.get_portfolio_value()
        cash_per_stock = self.get_cash_per_stock(total_value)

        for vt_symbol in list(self.target_data.keys()):
            self.set_target(vt_symbol, 0)

        for vt_symbol in active_symbols:
            if vt_symbol in bars:
                price = bars[vt_symbol].close_price
                if price > 0:
                    target_vol = (
                        int(cash_per_stock / price / self.min_volume) * self.min_volume
                    )
                    if target_vol > 0:
                        self.set_target(vt_symbol, target_vol)

        # 8. 执行调仓
        self.execute_trading(bars, price_add=self.price_add)

        self.write_log(
            f"再平衡: 持仓 {len(active_symbols)}, 卖出 {len(sell_symbols)}, "
            f"现金/股 {cash_per_stock:.0f}"
        )

    def on_trade(self, trade: "TradeData") -> None:
        self.write_log(
            f"成交 {trade.direction.value} {trade.vt_symbol} "
            f"{trade.volume}股 @{trade.price}"
        )