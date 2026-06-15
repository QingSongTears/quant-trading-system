"""
A股波段选股策略模块 — Stock Screener

独立运行:
    cd src/strategies/stock_screener && python main.py

跨模块导入:
    from src.strategies.stock_screener.core.strategy import StockScreenerStrategy
"""


def _ensure_project_root():
    """确保项目根目录在 sys.path 中，支持独立运行"""
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


_ensure_project_root()
