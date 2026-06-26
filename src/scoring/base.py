"""
BaseScorer — 所有评分器的统一基类
=================================

背景:
  旧实现 (technical / fundamental / fund_flow / chip / institutional /
  sentiment / news_event) 各自在 `__init__` 里写了 ~7 行相同的
  engine 初始化样板，并且 `ScorerRegistry._registry` 用手工字典维护
  注册表，新增/删除 scorer 时容易漂移。

设计:
  - 单一 BaseScorer 基类提供 engine 初始化、weight/label_zh/description
    元信息声明、`__init_subclass__` hook 自动注册到 ScorerRegistry。
  - 子类只需声明类属性 + 重写 `score()`，样板代码全部消除。
  - `score()` 契约: 返回 dict，至少包含
        {"code", "as_of_date", "total", "weighted", "sub_scores", "error"}

用法:
    class MyScorer(BaseScorer):
        name = "my"
        label_zh = "我的维度"
        weight = 0.10
        max_score = 20

        def score(self, code, as_of_date):
            return {"code": code, "as_of_date": as_of_date,
                    "total": 0, "weighted": 0.0,
                    "sub_scores": {}, "error": None}
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from ..db.engine import get_engine as _get_engine
from ..db.sql_utils import read_sql


class BaseScorer:
    """所有评分器的基类 — 封装 engine 初始化、元信息声明和自动注册"""

    # === 子类必须覆盖 ===
    name: str = "base"
    label_zh: str = "基类"
    description: str = ""

    # === 子类可覆盖 ===
    weight: float = 0.0          # 在综合评分中的权重
    max_score: int = 20          # weighted 字段归一化上限
    max_raw: int = 21            # 子分汇总上限 (用于 weighted = total/max_raw*max_score)

    def __init__(self, engine=None):
        # PR3.2: 默认用 src.db.engine.get_engine() 单例 (消除 8 个 scorer 各建 1 个 engine)
        # 显式传入 engine 时优先使用 (测试/DI 友好)
        self.engine = engine if engine is not None else _get_engine()

    def __init_subclass__(cls, **kwargs):
        """子类化时自动注册到 ScorerRegistry"""
        super().__init_subclass__(**kwargs)
        # 抽象基类自身不注册
        if cls.__name__ == "BaseScorer":
            return
        # 跳过抽象占位（name == "base" 的未命名子类）
        if not getattr(cls, "name", None) or cls.name == "base":
            return
        # 避免重复导入引起 ValueError
        try:
            from . import ScorerRegistry
            ScorerRegistry.register(cls)
        except ValueError:
            # 已注册（同一进程内子类重复导入），静默忽略
            pass

    def score(self, code: str, as_of_date: str | None = None) -> dict[str, Any]:
        """
        单只股票评分，返回标准 dict 至少包含:
            code, as_of_date, total, weighted, sub_scores, error
        """
        raise NotImplementedError(f"{type(self).__name__}.score() 必须被子类实现")

    # ─────────────────────────────────────────
    #  公用 helper (PR3.3: 7 个 scorer 共用的样板抽取)
    # ─────────────────────────────────────────

    DEFAULT_LOOKBACK = 150

    def _load_series(
        self,
        table: str,
        columns: list[str],
        code: str,
        as_of: str,
        lookback: int | None = None,
        where: str = "",
        order_col: str = "trade_date",
    ) -> pd.DataFrame:
        """
        通用时序数据加载 — 替代 7 个 scorer 的 _load_*_data 副本

        Args:
            table: 表名
            columns: 需要的列 (不含 order_col)
            code: 股票代码
            as_of: 截止日期
            lookback: 回看天数 (默认 150)
            where: 额外的 WHERE 条件 (不含 AND 前缀, e.g. "AND foo > 0")
            order_col: 排序/筛选列 (默认 "trade_date")

        Returns:
            按 order_col 升序排好的 DataFrame (无索引重置); 空时返空 DataFrame
        """
        lookback = lookback or self.DEFAULT_LOOKBACK
        cols = ", ".join(columns + [order_col])
        sql = f"""
            SELECT {cols} FROM {table}
            WHERE code = :code AND {order_col} <= :as_of {where}
            ORDER BY {order_col} DESC LIMIT :lookback
        """
        df = read_sql(sql, self.engine, {
            "code": code, "as_of": as_of, "lookback": int(lookback),
        })
        if df.empty:
            return df
        df[order_col] = pd.to_datetime(df[order_col])
        return df.sort_values(order_col).reset_index(drop=True)

    def _aggregate_subs(
        self,
        df: pd.DataFrame,
        scorers: dict[str, Callable[[pd.DataFrame], int]],
        max_raw: int | None = None,
        max_score: int = 20,
    ) -> tuple[dict[str, int], int, float]:
        """
        聚合 sub_scores → total + weighted — 替代 7 处复制的样板

        Args:
            df: 数据 DataFrame (传给每个 sub-scorer)
            scorers: {name: fn} 映射, fn(df) → int 0-3
            max_raw: 子分汇总上限 (默认 self.max_raw)
            max_score: weighted 归一化上限 (默认 20)

        Returns:
            (subs_dict, total, weighted)
            - subs_dict: {"ma_trend": 2, "macd": 1, ...}
            - total: sum(subs_dict.values())
            - weighted: round(total / max_raw * max_score, 1)
        """
        if max_raw is None:
            max_raw = self.max_raw
        subs = {name: fn(df) for name, fn in scorers.items()}
        total = sum(subs.values())
        weighted = round(total / max_raw * max_score, 1)
        return subs, total, weighted


__all__ = ["BaseScorer"]