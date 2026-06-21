"""
字段命名规范化 — camelCase ↔ snake_case 适配器
=================================================

⚠️ 历史口径混乱 (PR2.4 修复):
- 同一指标在 3+ 种命名中并存:
  sharpe / sharpe_ratio / sharpeRatio / sharpe_ratio
  max_drawdown / maxDrawdown / max_dd
  win_rate / winRate / wr
  profit_factor / profitFactor / pf
- 100+ 处 dict.get("sharpeRatio") / summary.get("sharpe") / r.sharpe_ratio 并存
- scripts/batch_backtest_v2/v3/bull_backtest_v3 全部用 camelCase, 与项目 snake_case 不一致

本模块提供收口函数,允许在 API 边界或脚本内统一转换。
"""
from __future__ import annotations
import re
from typing import Any


def camel_to_snake(name: str) -> str:
    """camelCase / PascalCase → snake_case

    Examples:
        >>> camel_to_snake("sharpeRatio") == "sharpe_ratio"
        >>> camel_to_snake("maxDrawdown") == "max_drawdown"
        >>> camel_to_snake("WinRate") == "win_rate"
        >>> camel_to_snake("already_snake") == "already_snake"
        >>> camel_to_snake("PF") == "p_f"  # 缩写按字符分隔
    """
    if not name:
        return name
    # 在小写字母/数字与大写字母之间插入下划线
    s1 = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    # 处理连续大写(如 PF → P_F)
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s1)
    return s2.lower()


def snake_to_camel(name: str, capitalize_first: bool = False) -> str:
    """snake_case → camelCase (或 PascalCase)

    Examples:
        >>> snake_to_camel("sharpe_ratio") == "sharpeRatio"
        >>> snake_to_camel("max_drawdown") == "maxDrawdown"
        >>> snake_to_camel("win_rate", capitalize_first=True) == "WinRate"
    """
    if not name or "_" not in name:
        return name.capitalize() if capitalize_first else name
    parts = name.split("_")
    first = parts[0].capitalize() if capitalize_first else parts[0]
    rest = "".join(p.capitalize() for p in parts[1:])
    return first + rest


def normalize_keys(d: dict | None, scheme: str = "snake") -> dict:
    """统一字典键名格式 — 收口 dict.get("sharpe") / dict.get("sharpeRatio") 等混用

    Args:
        d: 输入字典(可为 None)
        scheme: 'snake' (默认,推荐,符合项目惯例) 或 'camel' / 'pascal'

    Returns:
        新字典(不修改原 dict),键名已转换;值为 dict 时递归处理

    Examples:
        >>> normalize_keys({"sharpeRatio": 1.5, "maxDrawdown": -0.25}) == \
        ... {"sharpe_ratio": 1.5, "max_drawdown": -0.25}
        >>> normalize_keys({"sharpe_ratio": 1.5}, scheme="camel") == {"sharpeRatio": 1.5}
        >>> normalize_keys(None) == {}
    """
    if d is None:
        return {}
    if scheme == "snake":
        converter = camel_to_snake
    elif scheme in ("camel", "pascal"):
        capitalize = scheme == "pascal"
        converter = lambda k: snake_to_camel(k, capitalize_first=capitalize)  # noqa: E731
    else:
        raise ValueError(f"Unknown scheme: {scheme!r}, must be 'snake'/'camel'/'pascal'")

    result = {}
    for k, v in d.items():
        new_key = converter(str(k))
        # 递归处理嵌套 dict
        if isinstance(v, dict):
            result[new_key] = normalize_keys(v, scheme)
        elif isinstance(v, list):
            result[new_key] = [
                normalize_keys(item, scheme) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            result[new_key] = v
    return result