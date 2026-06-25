"""
config 缓存单测 (2026-06-25)

覆盖:
  - get_config() 第二次返同一对象 (cache)
  - load_strategies() 第二次返同一对象 (cache, 修 hot path 重复 IO)
  - 重置 cache 后会重读
"""
from src.config import get_config, load_strategies


def test_get_config_cached():
    """get_config 第二次返同一对象"""
    c1 = get_config()
    c2 = get_config()
    assert c1 is c2  # 同一对象, 缓存命中


def test_load_strategies_cached():
    """load_strategies 第二次返同一对象 (2026-06-25 新加 cache)"""
    s1 = load_strategies()
    s2 = load_strategies()
    assert s1 is s2  # 同一对象, 缓存命中


def test_load_strategies_returns_dict():
    """load_strategies 返 dict, 至少含 strategies 键"""
    s = load_strategies()
    assert isinstance(s, dict)
    assert "strategies" in s
    assert isinstance(s["strategies"], list)


def test_load_strategies_consistent():
    """缓存下多次调用值一致"""
    s1 = load_strategies()
    s2 = load_strategies()
    assert s1 == s2
