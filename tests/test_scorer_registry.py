"""
BaseScorer + ScorerRegistry 单元测试

覆盖:
  - BaseScorer.score() 基类 raise NotImplementedError
  - 子类必须声明非空 name 才能注册
  - ScorerRegistry.register() 拒绝非 BaseScorer 子类
  - ScorerRegistry.register() 检测 name 冲突
  - 子类通过 __init_subclass__ 自动注册
  - ScorerRegistry.get() 缓存 / use_cache=False
  - ScorerRegistry.list_all() / clear_cache()
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from sqlalchemy import create_engine

from src.scoring import BaseScorer, ScorerRegistry
from src.scoring.base import BaseScorer as BaseScorerDirect


# ============================================================
# 清理 hooks: 每个测试结束后清空 registry, 避免污染其他测试
# ============================================================

@pytest.fixture(autouse=True)
def _isolate_registry():
    """每个测试隔离 ScorerRegistry 状态

    策略: 记录 fixture setup 时的注册名集合, 测试结束后只删除
    新增的名字, 保留 8 个真实 scorer。这样既隔离测试, 又避免
    影响跨测试的真实注册检查。
    """
    # 触发 src.scoring import (保证 8 个真实 scorer 已注册)
    import src.scoring  # noqa: F401

    initial_names = set(ScorerRegistry._registry.keys())
    initial_instances = set(ScorerRegistry._instances.keys())
    yield
    # 删除测试过程中新增的注册
    new_names = set(ScorerRegistry._registry.keys()) - initial_names
    for name in new_names:
        ScorerRegistry._registry.pop(name, None)
    # 删除测试过程中新增的实例
    new_instances = set(ScorerRegistry._instances.keys()) - initial_instances
    for name in new_instances:
        ScorerRegistry._instances.pop(name, None)


# ============================================================
# 1. BaseScorer 抽象接口
# ============================================================

def test_base_scorer_score_raises_not_implemented():
    """基类的 score() 必须 raise NotImplementedError"""
    s = BaseScorer()
    with pytest.raises(NotImplementedError, match=r"BaseScorer.*必须被.*实现"):
        s.score("000001", "2026-06-15")


# ============================================================
# 2. ScorerRegistry.register() 校验
# ============================================================

def test_register_rejects_non_subclass():
    """register() 必须拒绝非 BaseScorer 子类"""
    class NotAScorer:
        name = "x"

    with pytest.raises(TypeError, match="只能注册 BaseScorer 子类"):
        ScorerRegistry.register(NotAScorer)


def test_register_rejects_empty_name():
    """子类必须声明非空 name"""
    class NamelessScorer(BaseScorer):
        pass  # 使用基类 name = "base"

    with pytest.raises(ValueError, match="必须声明非空的 name"):
        ScorerRegistry.register(NamelessScorer)


def test_register_rejects_duplicate_name():
    """重复 name 必须报错"""
    class FirstScorer(BaseScorer):
        name = "first"

    class SecondScorer(BaseScorer):
        name = "first"  # 故意冲突

    ScorerRegistry.register(FirstScorer)
    with pytest.raises(ValueError, match="Scorer name 冲突"):
        ScorerRegistry.register(SecondScorer)


# ============================================================
# 3. __init_subclass__ 自动注册
# ============================================================

def test_subclass_auto_registers():
    """继承 BaseScorer 即自动注册"""
    class AutoScorer(BaseScorer):
        name = "auto"
        label_zh = "自动注册测试"
        weight = 0.01

    assert "auto" in ScorerRegistry._registry
    info = ScorerRegistry._registry["auto"]
    assert info["class"] is AutoScorer
    assert info["label"] == "自动注册测试"
    assert info["weight"] == 0.01


def test_subclass_registration_silent_on_duplicate_import():
    """同一类多次定义 (重复导入) 不应报错"""
    class DupScorer(BaseScorer):
        name = "dup"

    # 第二次"定义"不会再次注册 (因为 cls 引用未变)
    class DupScorer2(BaseScorer):
        name = "dup"

    # 第二次 name 冲突会抛错，但 __init_subclass__ 内的 try/except 应吞掉
    assert "dup" in ScorerRegistry._registry


# ============================================================
# 4. ScorerRegistry.get() 行为
# ============================================================

def test_get_returns_singleton_by_default():
    class CachedScorer(BaseScorer):
        name = "cached"

        def score(self, code, as_of_date=None):
            return {"code": code, "as_of_date": as_of_date, "total": 0,
                    "weighted": 0.0, "sub_scores": {}, "error": None}

    a = ScorerRegistry.get("cached")
    b = ScorerRegistry.get("cached")
    assert a is b, "默认 use_cache=True 应返回同一实例"


def test_get_with_use_cache_false_returns_new_instance():
    class FreshScorer(BaseScorer):
        name = "fresh"

        def score(self, code, as_of_date=None):
            return {}

    a = ScorerRegistry.get("fresh", use_cache=False)
    b = ScorerRegistry.get("fresh", use_cache=False)
    assert a is not b, "use_cache=False 应每次新建实例"


def test_get_unknown_name_raises():
    with pytest.raises(KeyError, match="未知评分器"):
        ScorerRegistry.get("nonexistent_scorer_xyz")


def test_get_enabled_returns_dict():
    class A(BaseScorer):
        name = "a"

    class B(BaseScorer):
        name = "b"

    result = ScorerRegistry.get_enabled(["a", "b"])
    assert "a" in result and "b" in result
    assert isinstance(result["a"], A)
    assert isinstance(result["b"], B)


def test_list_all_returns_summary():
    class X(BaseScorer):
        name = "x"
        label_zh = "X 维度"
        weight = 0.5

    listed = ScorerRegistry.list_all()
    names = [d["name"] for d in listed]
    assert "x" in names
    x_info = next(d for d in listed if d["name"] == "x")
    assert x_info["label"] == "X 维度"
    assert x_info["weight"] == 0.5


def test_clear_cache_resets_instances():
    class T(BaseScorer):
        name = "t"

    ScorerRegistry.get("t")
    assert "t" in ScorerRegistry._instances
    ScorerRegistry.clear_cache()
    assert "t" not in ScorerRegistry._instances


# ============================================================
# 5. 子类 score() 契约
# ============================================================

def test_score_contract_violation_caught_at_call_time():
    """BaseScorer.score() 不重写则 NotImplementedError"""
    class Abstract(BaseScorer):
        name = "abstract"

    s = ScorerRegistry.get("abstract")
    with pytest.raises(NotImplementedError):
        s.score("000001")


def test_subclass_score_can_return_extra_keys():
    """子类可以在标准 dict 上加额外的 key (向后兼容)"""
    class FlexibleScorer(BaseScorer):
        name = "flex"

        def score(self, code, as_of_date=None):
            return {
                "code": code, "as_of_date": as_of_date,
                "total": 10, "weighted": 9.5,
                "sub_scores": {"k": 1}, "error": None,
                "extra_meta": {"lookback": 60},
            }

    s = ScorerRegistry.get("flex")
    r = s.score("000001", "2026-06-15")
    assert r["code"] == "000001"
    assert r["total"] == 10
    assert r["extra_meta"]["lookback"] == 60


# ============================================================
# 6. 所有 8 个真实 scorer 的注册完整性
# ============================================================

EXPECTED_SCORERS = {
    "technical", "fundamental", "fund_flow", "chip",
    "institutional", "sentiment", "news_event",
}


def test_all_eight_scorers_registered():
    """导入 src.scoring 应自动注册全部 7 个真实 scorer (lhb_institutional 2026-06-25 删除)"""
    # 触发 import
    from src.scoring import (
        TechnicalScorer, FundamentalScorer, FundFlowScorer, ChipScorer,
        InstitutionalScorer, SentimentScorer, NewsEventScorer,
    )
    registered = set(ScorerRegistry._registry.keys())
    missing = EXPECTED_SCORERS - registered
    assert not missing, f"未注册: {missing}"


def test_every_scorer_declares_required_metadata():
    """每个 scorer 必须有 name/label_zh/weight/score"""
    from src.scoring import (
        TechnicalScorer, FundamentalScorer, FundFlowScorer, ChipScorer,
        InstitutionalScorer, SentimentScorer, NewsEventScorer,
    )
    for cls in (TechnicalScorer, FundamentalScorer, FundFlowScorer, ChipScorer,
                InstitutionalScorer, SentimentScorer, NewsEventScorer):
        assert cls.name and cls.name != "base", f"{cls.__name__} 缺 name"
        assert cls.label_zh, f"{cls.__name__} 缺 label_zh"
        assert isinstance(cls.weight, (int, float)), f"{cls.__name__} 缺 weight"
        assert callable(getattr(cls, "score", None)), f"{cls.__name__} 缺 score()"
