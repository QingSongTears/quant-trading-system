"""
Research 层特征工程模块 (2026-06-25)

包含:
  - LeaderFeatureBuilder: 实时从 K 线算 5 维技术指标 (归一化 0-1)
                        替代 v_leader_features._load_tech_features 的 DB 查询
                        训练/推理分布一致 (用 IndicatorRegistry 同源)

设计原则:
  - 5 维归一化: rsi14/100, kdj/100, boll_pos 0-1 (与 train_xgb_v4.py 严格一致)
  - 单股接口: build(bars_df) -> dict[5 维]
  - 批量接口: build_batch(bars_dict) -> dict[code, dict[5 维]]
  - 状态: KDJ 用 instance 状态 (reset=True 切换股票)
"""
from .leader_features import (
    LeaderFeatureBuilder,
    TECH_COLS,
    TECH_DEFAULTS,
)

__all__ = [
    "LeaderFeatureBuilder",
    "TECH_COLS",
    "TECH_DEFAULTS",
]
