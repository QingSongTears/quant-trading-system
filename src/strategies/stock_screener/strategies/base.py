"""
策略基类 — 引擎与策略之间的标准接口
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
from typing import List, Tuple, Dict, Any
from dataclasses import dataclass, field
from backtest.engine import Signal, Trade, BaseStrategy


__all__ = ["Signal", "Trade", "BaseStrategy"]
