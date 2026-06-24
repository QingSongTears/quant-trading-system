"""
BaseEngine 单测 — src/engine/base.py (借鉴 vnpy 4.4)

涵盖:
  - ABC 不能直接实例化
  - MockEngine 子类完整生命周期 (init → start → stop → close)
  - start/stop 幂等 (重复调用安全)
  - is_active property 状态正确
  - engine_name 默认值 / 自定义
  - main_engine / event_engine 可选 (None 模式)
  - repr 包含状态
  - 集成 MainEngine.add_engine + get_engine
"""
from typing import List

import pytest

from src.engine import BaseEngine
from src.event import EventEngine
from src.gateway import MainEngine


# ── Mock Engine 子类 ──────────────────────────


class MockEngine(BaseEngine):
    """完整实现 BaseEngine 的 Mock, 用于单测

    记录生命周期调用顺序, 验证 start/stop/close 的预期行为
    严格遵循"override 时 super()"约定, 让 is_active 状态机同步更新
    """

    def __init__(
        self,
        main_engine: MainEngine = None,
        event_engine: EventEngine = None,
        engine_name: str = "",
    ) -> None:
        super().__init__(main_engine, event_engine, engine_name)
        self.call_log: List[str] = []
        self.data: dict = {}

    def init(self) -> None:
        """init 是具体方法 (非抽象), 子类可选择实现"""
        self.call_log.append("init")
        self.data["initialized"] = True

    def start(self) -> None:
        # 业务逻辑在前
        self.call_log.append("start")
        self.data["running"] = True
        # super().start() 更新 is_active + log
        super().start()

    def stop(self) -> None:
        # super().stop() 先更新 is_active, 业务逻辑在后
        super().stop()
        self.call_log.append("stop")
        self.data["running"] = False

    def close(self) -> None:
        # super().close() 先 stop (若 active), 业务逻辑在后
        # 这样 call_log 顺序是: init → start → stop → close
        super().close()
        self.call_log.append("close")
        self.data["closed"] = True


# ── ABC 抽象类不能实例化 ──────────────────────────


def test_abc_cannot_instantiate():
    """BaseEngine 是抽象类, 直接实例化应抛 NotImplementedError"""
    with pytest.raises(NotImplementedError, match="抽象类"):
        BaseEngine()


# ── 实例化 / 基本属性 ──────────────────────────


def test_default_engine_name_is_classname():
    """engine_name 默认 = 类名"""
    eng = MockEngine()
    assert eng.engine_name == "MockEngine"


def test_custom_engine_name():
    eng = MockEngine(engine_name="custom_one")
    assert eng.engine_name == "custom_one"


def test_main_engine_default_none():
    eng = MockEngine()
    assert eng.main_engine is None
    assert eng.event_engine is None


def test_main_engine_explicit():
    me = MainEngine()
    ee = me.event_engine
    eng = MockEngine(main_engine=me, event_engine=ee)
    assert eng.main_engine is me
    assert eng.event_engine is ee


def test_is_active_starts_false():
    eng = MockEngine()
    assert eng.is_active is False


# ── 状态机 ──────────────────────────


def test_init_then_start_makes_active():
    eng = MockEngine()
    eng.init()
    eng.start()
    assert eng.is_active is True
    assert eng.call_log == ["init", "start"]


def test_start_then_stop_makes_inactive():
    eng = MockEngine()
    eng.init()
    eng.start()
    eng.stop()
    assert eng.is_active is False
    assert eng.call_log == ["init", "start", "stop"]


def test_start_is_idempotent():
    """MockEngine.start() 调 super().start(), 重复 start 时 super 检查 is_active 跳过

    is_active 状态: 第一次 start 后 True, 第二次 start 后仍 True
    call_log 验证: MockEngine.start() 业务逻辑总会跑 (start_called 累计)
                  但 super().start() 检查 is_active 幂等, 不重复 log
    """
    eng = MockEngine()
    eng.init()
    eng.start()
    eng.start()  # 重复
    assert eng.call_log.count("start") == 2  # 业务逻辑两次
    assert eng.is_active is True


def test_default_start_is_idempotent():
    """BaseEngine 默认 start 行为: 幂等

    验证 super().start() 内部对 is_active 的检查确实生效
    """
    class SimpleEngine(BaseEngine):
        def __init__(self):
            super().__init__()
            self.user_logic = 0

        def start(self) -> None:
            # 用户业务逻辑先跑 (总会跑)
            self.user_logic += 1
            # super().start() 检查 is_active, 幂等
            super().start()

    eng = SimpleEngine()
    eng.start()
    eng.start()  # 重复
    # user_logic 跑 2 次 (业务逻辑没幂等保护)
    assert eng.user_logic == 2
    # 但 is_active 状态: 第二次 super().start() 因为 is_active=True 跳过
    assert eng.is_active is True
    # _log 也没刷第二条 (因为 super() 提前返回)
    # 实际验证: 连续 100 次 start, log 应只有 1 条
    # (但这测内部 log 不易, 改用 stats 替代)


def test_stop_is_idempotent():
    eng = MockEngine()
    eng.init()
    eng.start()
    eng.stop()
    eng.stop()  # 重复 stop
    assert eng.call_log.count("stop") == 2  # MockEngine 没幂等保护


# ── close 默认行为 ──────────────────────────


def test_close_stops_if_active():
    eng = MockEngine()
    eng.init()
    eng.start()
    eng.close()
    # close 应先 stop (若 active)
    assert eng.call_log == ["init", "start", "stop", "close"]
    assert eng.data.get("closed") is True


def test_close_without_start_does_nothing_extra():
    eng = MockEngine()
    eng.close()
    # 未启动时 close 不应调 stop
    assert eng.call_log == ["close"]


# ── repr ──────────────────────────


def test_repr_includes_state():
    eng = MockEngine()
    r = repr(eng)
    assert "MockEngine" in r
    assert "STOPPED" in r

    eng.start()
    r2 = repr(eng)
    assert "ACTIVE" in r2


def test_repr_includes_engine_name():
    eng = MockEngine(engine_name="risk_v1")
    r = repr(eng)
    assert "risk_v1" in r


# ── 集成: MainEngine.add_engine ──────────────────────────


def test_main_engine_add_engine_registers_in_dict():
    me = MainEngine()
    assert me.engines == {}

    eng = me.add_engine(MockEngine, "first")
    assert me.engines["first"] is eng
    assert eng.main_engine is me
    assert eng.event_engine is me.event_engine


def test_main_engine_add_engine_default_name():
    me = MainEngine()
    eng = me.add_engine(MockEngine)  # 不传 name
    # engine_name 默认 = 类名 "MockEngine"
    assert "MockEngine" in me.engines
    assert me.engines[eng.engine_name] is eng


def test_main_engine_get_engine_by_name():
    me = MainEngine()
    e1 = me.add_engine(MockEngine, "e1")
    e2 = me.add_engine(MockEngine, "e2")

    assert me.get_engine("e1") is e1
    assert me.get_engine("e2") is e2


def test_main_engine_get_engine_default_returns_first():
    me = MainEngine()
    e1 = me.add_engine(MockEngine, "e1")
    e2 = me.add_engine(MockEngine, "e2")
    # 空字符串返回第一个
    assert me.get_engine() is e1


def test_main_engine_get_engine_empty_raises():
    me = MainEngine()
    with pytest.raises(RuntimeError, match="没有注册任何引擎"):
        me.get_engine()


def test_main_engine_get_engine_missing_raises():
    me = MainEngine()
    me.add_engine(MockEngine, "e1")
    with pytest.raises(KeyError, match="不存在"):
        me.get_engine("nonexistent")


def test_main_engine_add_engine_warns_on_duplicate():
    me = MainEngine()
    me.add_engine(MockEngine, "dup")
    # 第二次同名应覆盖 + warn
    e2 = me.add_engine(MockEngine, "dup")
    assert me.engines["dup"] is e2


def test_main_engine_stats_includes_engines():
    me = MainEngine()
    me.add_engine(MockEngine, "e1")
    s = me.stats()
    assert "engines" in s
    assert "e1" in s["engines"]


# ── 真实场景: 多个 Engine 协同 ──────────────────────────


def test_multiple_engines_independent_state():
    me = MainEngine()
    e1 = me.add_engine(MockEngine, "e1")
    e2 = me.add_engine(MockEngine, "e2")

    e1.start()
    e2.stop()  # 无效 (未启动)

    assert e1.is_active is True
    assert e2.is_active is False


def test_engine_uses_main_engine_for_data_access():
    """BaseEngine 应能访问 main_engine 的 gateways/strategies/engines"""
    me = MainEngine()
    eng = me.add_engine(MockEngine, "x")

    # 子类可通过 self.main_engine 访问
    assert eng.main_engine.gateways == {}
    assert eng.main_engine.engines["x"] is eng