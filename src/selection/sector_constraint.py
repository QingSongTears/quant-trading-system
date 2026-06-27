"""
行业暴露约束 (T3.2)
====================

防止组合过度集中在单一行业，降低尾部风险。

设计:
  - 输入: 候选股票列表 + 每只股票的 industry
  - 输出: 满足行业分散约束的最终选股列表

约束:
  - 默认: 单行业持仓权重 ≤ 30%
  - 单行业最多选 max_per_industry 只 (可选)
  - 无行业信息的股票视为单独行业 "未知"

算法:
  - 按原始评分排序候选股
  - 按行业累计: 每选一只 → 该行业计数+1 → 超限则跳过
  - 直到选满或候选耗尽

用法:
    from src.selection.sector_constraint import SectorConstraint, apply_sector_cap

    constraint = SectorConstraint(max_pct=0.30, max_count=3)
    selected = apply_sector_cap(candidates, constraint)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

UNKNOWN = "未知"


@dataclass
class SectorConstraint:
    """行业暴露约束配置

    Attributes:
        max_pct: 单行业最大权重占比 (0.30 = 30%)
        max_count: 单行业最大持股数 (None = 不限)
        min_per_industry: 单行业最少持股数 (可选, 用于强制分散)
    """
    max_pct: float = 0.30
    max_count: int | None = None
    min_per_industry: int = 1


@dataclass
class SectorSelectionResult:
    """约束应用结果 — 便于回测中记录被拒原因"""
    selected: list[str] = field(default_factory=list)
    rejected_by_industry: dict[str, list[str]] = field(default_factory=dict)
    final_industry_counts: dict[str, int] = field(default_factory=dict)

    @property
    def n_rejected(self) -> int:
        return sum(len(v) for v in self.rejected_by_industry.values())

    @property
    def n_selected(self) -> int:
        return len(self.selected)


def apply_sector_cap(
    candidates: pd.DataFrame,
    constraint: SectorConstraint,
    industry_col: str = "industry",
    target_n: int | None = None,
) -> SectorSelectionResult:
    """应用行业暴露约束

    Args:
        candidates: 候选股票 DataFrame, 必须包含 industry 列和 code 列
        constraint: 约束配置
        industry_col: industry 列名
        target_n: 目标选股数 (None = 用 len(candidates))

    Returns:
        SectorSelectionResult 含 selected/rejected_by_industry/final_industry_counts
    """
    if candidates.empty:
        return SectorSelectionResult()

    # 兜底: 缺失 industry 视为 UNKNOWN
    df = candidates.copy()
    if industry_col not in df.columns:
        df[industry_col] = UNKNOWN
    df[industry_col] = df[industry_col].fillna(UNKNOWN).replace("", UNKNOWN)

    # 必须有 code 列
    if "code" not in df.columns:
        raise ValueError(f"candidates 必须包含 'code' 列, 实际列: {df.columns.tolist()}")

    # 必须有某种排序依据: score > sharpe > return > 默认顺序
    sort_col = None
    for c in ("score", "sharpe", "avg_return", "return_Nd"):
        if c in df.columns:
            sort_col = c
            break
    if sort_col:
        df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
    # 否则保持原顺序

    target = target_n or len(df)
    n_target_industry = max(int(target * constraint.max_pct), 1)

    result = SectorSelectionResult()
    counts: dict[str, int] = {}

    for _, row in df.iterrows():
        code = str(row["code"])
        ind = str(row[industry_col])
        if len(result.selected) >= target:
            break

        current = counts.get(ind, 0)
        # 约束 1: 占比
        if current >= n_target_industry:
            result.rejected_by_industry.setdefault(ind, []).append(code)
            continue
        # 约束 2: 数量
        if constraint.max_count is not None and current >= constraint.max_count:
            result.rejected_by_industry.setdefault(ind, []).append(code)
            continue

        result.selected.append(code)
        counts[ind] = current + 1

    result.final_industry_counts = counts
    return result


def load_industry_map(engine, codes: list[str]) -> dict[str, str]:
    """从 stock_basic.industry 批量加载行业映射

    Args:
        engine: SQLAlchemy engine
        codes: 股票代码列表

    Returns:
        {code: industry} dict, 缺失为 "未知"
    """
    if not codes:
        return {}

    try:
        with engine.connect() as conn:
            from sqlalchemy import bindparam, text
            stmt = text(
                "SELECT code, industry FROM stock_basic "
                "WHERE code IN :codes AND industry IS NOT NULL AND industry != ''"
            ).bindparams(bindparam("codes", expanding=True))
            df = pd.read_sql(stmt, conn, params={"codes": list(codes)})
        return dict(zip(df["code"].astype(str), df["industry"].astype(str)))
    except Exception:
        return {}


def get_engine():
    """获取数据库 engine (懒加载)

    用于 SectorConstraint.apply_sector_constraint 自动加载 industry
    """
    from ..config import get_config, get_db_url
    from sqlalchemy import create_engine
    config = get_config()
    return create_engine(get_db_url(config))