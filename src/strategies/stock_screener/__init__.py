"""
⚠️ LEGACY — A股波段选股策略模块（Stock Screener）

状态: 归档保留，不再积极维护。详见 LEGACY.md。

新策略请写到 src/strategies/ 主系统（使用 BaseSelectionStrategy 基类）。
新数据加载请用 src.models.repository.DataRepository。

主推策略位置:
  - V6超卖反转:     src.strategies.v6_reversal_selection
  - V6多维融合:     src.strategies.v6_pipeline_hybrid
  - V龙头主升:      src.strategies.v_leader_main_surge (骨架)

跨模块导入（向后兼容）:
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
