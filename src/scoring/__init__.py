"""
评分器注册中心 — 独立模型，共享数据库

每个评分器互不耦合，仅通过 quant.db 共享底层数据。
通过 ScorerRegistry 按名获取、按需实例化、按参数控制。

用法:
    from src.scoring import ScorerRegistry

    # 获取单个评分器
    tech = ScorerRegistry.get("technical")
    result = tech.score("000001", "2026-06-15")

    # 批量获取启用的评分器
    scorers = ScorerRegistry.get_enabled(["technical", "fundamental", "fund_flow"])
    for name, scorer in scorers.items():
        r = scorer.score("000001", "2026-06-15")

    # 列出所有可用评分器
    print(ScorerRegistry.list_all())
"""

from typing import Dict, List, Optional, Type, Any
import importlib


class ScorerRegistry:
    """评分器注册中心 — 工厂模式 + 注册表"""

    # 注册表: {name: (module_path, class_name, label_zh)}
    _registry: Dict[str, tuple] = {
        "technical":     ("src.scoring.technical_scorer",     "TechnicalScorer",     "技术面"),
        "fundamental":   ("src.scoring.fundamental_scorer",   "FundamentalScorer",   "基本面"),
        "fund_flow":     ("src.scoring.fund_flow_scorer",     "FundFlowScorer",      "资金面"),
        "news_event":    ("src.scoring.news_event_scorer",    "NewsEventScorer",     "消息面"),
        "sentiment":     ("src.scoring.sentiment_scorer",     "SentimentScorer",     "情绪面"),
        "institutional": ("src.scoring.institutional_scorer", "InstitutionalScorer", "机构持仓"),
    }

    # 单例缓存 (同一 engine 下复用实例)
    _instances: Dict[str, Any] = {}

    @classmethod
    def get(cls, name: str, engine=None, use_cache: bool = True) -> Any:
        """
        获取评分器实例。

        Args:
            name: 评分器名称 (technical/fundamental/fund_flow/news_event/sentiment/institutional)
            engine: SQLAlchemy engine，None 则自动创建默认连接
            use_cache: 是否复用已创建的实例

        Returns:
            评分器实例
        """
        if name not in cls._registry:
            raise KeyError(f"未知评分器: {name}，可用: {list(cls._registry.keys())}")

        cache_key = f"{name}_{id(engine)}" if engine else name

        if use_cache and cache_key in cls._instances:
            return cls._instances[cache_key]

        mod_path, cls_name, _ = cls._registry[name]
        mod = importlib.import_module(mod_path)
        scorer_cls = getattr(mod, cls_name)

        try:
            instance = scorer_cls(engine=engine)
        except TypeError:
            instance = scorer_cls()

        if use_cache:
            cls._instances[cache_key] = instance

        return instance

    @classmethod
    def get_enabled(
        cls,
        dimensions: List[str],
        engine=None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        批量获取评分器。

        Args:
            dimensions: 启用的维度列表，如 ["technical", "fundamental"]
            engine: 共享的数据库引擎
            use_cache: 是否复用实例

        Returns:
            {name: scorer_instance} 字典
        """
        return {name: cls.get(name, engine=engine, use_cache=use_cache) for name in dimensions}

    @classmethod
    def list_all(cls) -> List[Dict[str, str]]:
        """列出所有已注册的评分器"""
        return [
            {"name": name, "label": label}
            for name, (_, _, label) in cls._registry.items()
        ]

    @classmethod
    def clear_cache(cls):
        """清除实例缓存"""
        cls._instances.clear()
