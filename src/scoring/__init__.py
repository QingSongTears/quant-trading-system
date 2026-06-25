"""
评分器注册中心 — 独立模型，共享数据库
====================================

每个评分器互不耦合，仅通过 quant.db 共享底层数据。
通过 ScorerRegistry 按名获取、按需实例化、按参数控制。

自动注册机制:
  所有继承 `BaseScorer` 的子类在定义时通过 `BaseScorer.__init_subclass__`
  自动注册到 `ScorerRegistry._registry`，无需手工维护字典。新增 scorer
  只需写类即可，import 该模块即触发注册。

用法:
    from src.scoring import ScorerRegistry

    # 获取单个评分器
    tech = ScorerRegistry.get("technical")
    result = tech.score("000001", "2026-06-15")

    # 批量获取启用的评分器
    scorers = ScorerRegistry.get_enabled(["technical", "fundamental", "fund_flow"])

    # 列出所有已注册评分器
    print(ScorerRegistry.list_all())
"""
from __future__ import annotations

from typing import Any

__all__ = ["BaseScorer", "ScorerRegistry"]


class ScorerRegistry:
    """评分器注册中心 — 工厂 + 注册表 + 缓存"""

    # name -> {"class", "label", "weight", "max_raw"}
    _registry: dict[str, dict[str, Any]] = {}

    # 单例缓存: name -> 实例
    _instances: dict[str, Any] = {}

    @classmethod
    def register(cls, scorer_cls: type) -> type:
        """注册一个 BaseScorer 子类（通常由 __init_subclass__ 自动调用）"""
        from .base import BaseScorer
        if not isinstance(scorer_cls, type) or not issubclass(scorer_cls, BaseScorer):
            raise TypeError(f"只能注册 BaseScorer 子类，收到 {scorer_cls!r}")
        name = getattr(scorer_cls, "name", None)
        if not name or name == "base":
            raise ValueError(f"{scorer_cls.__name__} 必须声明非空的 name 类属性")
        if name in cls._registry:
            existing = cls._registry[name].get("class")
            if existing is not None and existing is not scorer_cls:
                raise ValueError(f"Scorer name 冲突: {name!r} 已被 {existing.__name__} 占用")
        cls._registry[name] = {
            "class": scorer_cls,
            "label": getattr(scorer_cls, "label_zh", name),
            "weight": getattr(scorer_cls, "weight", 0.0),
            "max_raw": getattr(scorer_cls, "max_raw", 21),
        }
        return scorer_cls

    @classmethod
    def get(cls, name: str, engine=None, use_cache: bool = True) -> Any:
        """获取评分器实例"""
        if name not in cls._registry:
            raise KeyError(f"未知评分器: {name}，可用: {list(cls._registry.keys())}")
        if use_cache and name in cls._instances:
            return cls._instances[name]
        scorer_cls = cls._registry[name]["class"]
        instance = scorer_cls(engine=engine)
        if use_cache:
            cls._instances[name] = instance
        return instance

    @classmethod
    def get_enabled(
        cls,
        dimensions: list[str],
        engine=None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """批量获取评分器"""
        return {name: cls.get(name, engine=engine, use_cache=use_cache) for name in dimensions}

    @classmethod
    def list_all(cls) -> list[dict[str, Any]]:
        """列出所有已注册的评分器"""
        return [
            {"name": name, "label": info["label"], "weight": info["weight"]}
            for name, info in cls._registry.items()
        ]

    @classmethod
    def clear_cache(cls) -> None:
        """清除实例缓存"""
        cls._instances.clear()


# === BaseScorer 必须在子类之前 import (因为 __init_subclass__ 依赖 ScorerRegistry) ===
from .base import BaseScorer  # noqa: E402,F401

# === 触发子模块 import 以触发子类定义与自动注册 ===
from .technical_scorer import TechnicalScorer  # noqa: E402,F401
from .fundamental_scorer import FundamentalScorer  # noqa: E402,F401
from .fund_flow_scorer import FundFlowScorer  # noqa: E402,F401
from .chip_scorer import ChipScorer  # noqa: E402,F401
from .institutional_scorer import InstitutionalScorer  # noqa: E402,F401
from .sentiment_scorer import SentimentScorer  # noqa: E402,F401
from .news_event_scorer import NewsEventScorer  # noqa: E402,F401