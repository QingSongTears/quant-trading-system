import sys, time
sys.path.insert(0, '.')

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from datetime import date

# Equal weight
from src.strategies.small_cap import SmallCapStrategy

engine = PortfolioBacktestEngine()

print('Running equal weight backtest...')
t0 = time.time()
report_eq = engine.run(
    strategy=SmallCapStrategy(),
    start_date=date(2024, 7, 1),
    end_date=date(2026, 6, 16),
    initial_capital=1000000,
)
print(f'Equal weight: return={report_eq.total_return:.2f}%, sharpe={report_eq.sharpe_ratio:.2f}, max_dd={report_eq.max_drawdown:.2f}%')
print(f'  time: {time.time()-t0:.1f}s')

# Volatility weighted
from src.backtest.base_selection_strategy import BaseSelectionStrategy
from src.strategies.small_cap import SmallCapStrategy

class VolWeightedStrategy(SmallCapStrategy):
    def select_with_weights(self, rebalance_date, universe_df):
        return self.select_volatility_weighted(
            rebalance_date, universe_df,
            max_weight=0.15, min_weight=0.05,
        )

print()
print('Running volatility weighted backtest...')
t0 = time.time()
report_vw = engine.run(
    strategy=VolWeightedStrategy(),
    start_date=date(2024, 7, 1),
    end_date=date(2026, 6, 16),
    initial_capital=1000000,
)
print(f'Vol weighted: return={report_vw.total_return:.2f}%, sharpe={report_vw.sharpe_ratio:.2f}, max_dd={report_vw.max_drawdown:.2f}%')
print(f'  time: {time.time()-t0:.1f}s')

print()
print('=== Comparison ===')
print(f'Sharpe improvement: {report_vw.sharpe_ratio - report_eq.sharpe_ratio:+.2f}')
print(f'Max drawdown improvement: {report_eq.max_drawdown - report_vw.max_drawdown:+.2f}%')
