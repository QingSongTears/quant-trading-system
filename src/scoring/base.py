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

from typing import Any, Dict, Optional

from sqlalchemy import create_engine

from ..config import get_config, get_db_url


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
        if engine is not None:
            self.engine = engine
        else:
            config = get_config()
            db_url = get_db_url(config)
            self.engine = create_engine(db_url, echo=False)

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

    def score(self, code: str, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        单只股票评分，返回标准 dict 至少包含:
            code, as_of_date, total, weighted, sub_scores, error
        """
        raise NotImplementedError(f"{type(self).__name__}.score() 必须被子类实现")


__all__ = ["BaseScorer"]