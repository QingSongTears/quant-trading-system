"""
综合评分收口 — 消除 5+ 种 combined_score 公式并存
==================================================

⚠️ 历史口径混乱 (PR2.5 修复):
- src/selection/pipeline.py:286  加权平均 sum(s*w)/sum(w)
- scripts/regenerate_combined_scores.py:162  算术平均 .mean(axis=1)
- scripts/param_server.py:925  百分位排名(可能)
- src/strategies/stock_screener/backtest/optimizer.py:27  硬编码
  收益*0.6 + 夏普*0.3 + 胜率*0.1
- src/models/shield_spear.py:67  矛/盾动态权重

5+ 种公式输出**不可直接比较**,同一组 6 维分数不同公式得分排序都不同。

本模块提供单一收口:
- combine(scores, weights, method="weighted_mean")
- method 可选: weighted_mean / mean / weighted_product
- 推荐 weighted_mean (默认),与项目主流用法对齐
"""
from __future__ import annotations

import math



def combine(
    scores: Mapping[str, float] | Sequence[float],
    weights: Mapping[str, float] | Sequence[float] | None = None,
    method: str = "weighted_mean",
) -> float:
    """
    综合多维度评分为单一分数

    Args:
        scores: 各维度分数,如 {"tech": 80, "flow": 60, "fund": 70}
                也可以是 [80, 60, 70] (按位置对应 weights)
        weights: 各维度权重,如 {"tech": 0.5, "flow": 0.3, "fund": 0.2}
                 None 或空时退化为等权;接受 list 时按位置对应
        method:
          - "weighted_mean": 加权平均 sum(s*w)/sum(w) (默认,推荐)
          - "mean": 算术平均 sum(s)/n (等权)
          - "weighted_product": 几何加权 prod(s_norm^w) (鼓励多维均衡,
            适合多维度都不能偏科)

    Returns:
        float,综合分;空输入返回 0

    Raises:
        ValueError: method 未知

    Examples:
        >>> combine({"a": 80, "b": 60}, {"a": 0.7, "b": 0.3})
        74.0
        >>> combine({"a": 80, "b": 60})  # 等权
        70.0
        >>> combine({"a": 80, "b": 60, "c": 0}, method="mean")
        46.666666666666664
    """
    if not scores:
        return 0.0

    # 归一化 scores 为 (key, value) 列表
    if isinstance(scores, Mapping):
        keys = list(scores.keys())
        values = [float(v) for v in scores.values()]
    else:
        keys = [str(i) for i in range(len(scores))]
        values = [float(v) for v in scores]

    if not values:
        return 0.0

    # 归一化 weights
    if weights is None:
        w_values = [1.0] * len(values)
    elif isinstance(weights, Mapping):
        w_values = [float(weights.get(k, 0.0)) for k in keys]
    else:
        # Sequence,按位置对应;长度不一致时尾部补 0
        w_list = [float(w) for w in weights]
        w_values = w_list + [0.0] * (len(values) - len(w_list))

    # 过滤掉权重 ≤ 0 的维度 (不影响综合分)
    pairs = [(s, w) for s, w in zip(values, w_values) if w > 0]
    if not pairs:
        return 0.0

    if method == "weighted_mean":
        total_w = sum(w for _, w in pairs)
        if total_w <= 0:
            return 0.0
        return sum(s * w for s, w in pairs) / total_w

    if method == "mean":
        return sum(s for s, _ in pairs) / len(pairs)

    if method == "weighted_product":
        # 几何加权,要求 score > 0;否则归一化到 (0, 1] 区间后计算
        # 不归一化以保留语义:score=0 直接拉低到 0
        # 数学: prod(s^w) = exp(sum(w * log(s)))
        any_zero = any(s <= 0 for s, _ in pairs)
        if any_zero:
            return 0.0
        log_sum = sum(w * math.log(s) for s, w in pairs)
        total_w = sum(w for _, w in pairs)
        return math.exp(log_sum / total_w) if total_w > 0 else 0.0

    raise ValueError(
        f"Unknown method: {method!r}, must be 'weighted_mean'/'mean'/'weighted_product'"
    )