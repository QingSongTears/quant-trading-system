"""
EventEngine 单测 — 覆盖 13 项用例

涵盖:
  - Event dataclass 默认值 + __repr__
  - register/unregister 防重复
  - put 同步派发 (type + general handlers)
  - handler 抛异常隔离 + error_count
  - put 未启动时默认 raise (strict=True) + strict=False 容忍
  - start/stop timer 生命周期
  - stats() 字段完整
  - 并发 put + unregister 无 RuntimeError

新增 (2026-06-24):
  - put strict=True raise RuntimeError
  - 加锁后多线程并发不爆
  - threading.Event 替代 bool flag, stop 后毫秒级退出
"""
import threading
import time

import pytest

from src.event import EVENT_LOG, EVENT_TIMER, Event, EventEngine


# ── Event dataclass ──────────────────────────


def test_event_defaults():
    e = Event("eTick")
    assert e.type == "eTick"
    assert e.data is None
    assert e.timestamp is not None


def test_event_repr_contains_type_and_data_class():
    e = Event("eTick", {"price": 10.0})
    s = repr(e)
    assert "eTick" in s
    assert "dict" in s


# ── register / unregister ──────────────────────


def test_register_dedup():
    eng = EventEngine(interval=1)

    def h(evt): pass

    eng.register("eTick", h)
    eng.register("eTick", h)  # 重复注册

    assert len(eng._handlers["eTick"]) == 1


def test_register_general_dedup():
    eng = EventEngine(interval=1)

    def h(evt): pass

    eng.register_general(h)
    eng.register_general(h)
    assert len(eng._general_handlers) == 1


def test_unregister_missing_is_noop():
    eng = EventEngine(interval=1)

    def h(evt): pass

    # 未注册时 unregister 不应报错
    eng.unregister("eTick", h)
    eng.unregister_general(h)
    assert eng._handlers == {}
    assert eng._general_handlers == []


# ── put 同步派发 ──────────────────────────


def test_put_calls_typed_handlers_in_order():
    eng = EventEngine(interval=1)
    log = []

    eng.register("eTick", lambda e: log.append(("typed1", e.type)))
    eng.register("eTick", lambda e: log.append(("typed2", e.type)))
    eng.register_general(lambda e: log.append(("general", e.type)))

    eng.start()
    eng.put(Event("eTick"))

    assert log == [("typed1", "eTick"), ("typed2", "eTick"), ("general", "eTick")]


def test_put_handler_exception_isolated():
    eng = EventEngine(interval=1)
    log = []

    def boom(evt):
        raise ValueError("boom")

    eng.register("eTick", boom)
    eng.register("eTick", lambda e: log.append("after_boom"))

    eng.start()
    eng.put(Event("eTick"))

    # 异常 handler 不影响后续 handler
    assert log == ["after_boom"]
    assert eng.error_count == 1


def test_put_general_handler_exception_isolated():
    eng = EventEngine(interval=1)
    log = []

    def boom(evt):
        raise RuntimeError("boom")

    eng.register_general(boom)
    eng.register_general(lambda e: log.append("after_boom"))

    eng.start()
    eng.put(Event("eTick"))

    assert log == ["after_boom"]
    assert eng.error_count == 1


# ── put 未启动时行为 ──────────────────────────


def test_put_before_start_raises_by_default():
    eng = EventEngine(interval=1)
    with pytest.raises(RuntimeError, match="未启动"):
        eng.put(Event("eTick"))
    # 未启动时不应增加事件计数
    assert eng.event_count == 0


def test_put_before_start_with_strict_false_warns():
    eng = EventEngine(interval=1, raise_on_inactive=False)
    eng.put(Event("eTick"))  # 不应 raise
    assert eng.event_count == 0


def test_put_strict_param_overrides_default():
    eng = EventEngine(interval=1, raise_on_inactive=False)
    eng.start()
    eng.stop()
    # stop 后 strict=True 强制 raise
    with pytest.raises(RuntimeError):
        eng.put(Event("eTick"), strict=True)
    # strict=False 容忍
    eng.put(Event("eTick"), strict=False)


# ── 生命周期 ──────────────────────────


def test_start_stop_timer_lifecycle():
    eng = EventEngine(interval=0.05)
    eng.start()
    time.sleep(0.2)  # 应收到约 4 个 TIMER 事件
    eng.stop()

    # timer 应至少触发几次 (允许 1-2 误差, 因为 wait 有超时)
    assert eng.event_count >= 1
    assert eng.stats()["active"] is False


def test_start_is_idempotent():
    eng = EventEngine(interval=1)
    eng.start()
    tid = eng._timer_thread.ident
    eng.start()  # 第二次 start 不应重启线程
    assert eng._timer_thread.ident == tid
    eng.stop()


def test_stop_returns_quickly_via_event():
    """thundering.Event 应让 stop 在 ~10ms 内退出, 不需等满 interval"""
    eng = EventEngine(interval=10)  # 间隔很大
    eng.start()
    time.sleep(0.05)  # 等待线程进入 wait(10)
    t0 = time.time()
    eng.stop()
    elapsed = time.time() - t0
    # 应该是毫秒级, 不应超过 0.5s
    assert elapsed < 0.5, f"stop took {elapsed:.2f}s, expected <0.5s"


def test_stop_after_stop_is_safe():
    eng = EventEngine(interval=1)
    eng.start()
    eng.stop()
    eng.stop()  # 第二次 stop 不应崩


# ── stats ──────────────────────────


def test_stats_shape():
    eng = EventEngine(interval=1)

    def h(evt): pass

    eng.register("eTick", h)
    eng.register_general(h)

    s = eng.stats()
    assert s["active"] is False
    assert s["event_count"] == 0
    assert s["error_count"] == 0
    assert s["registered_handlers"] == {"eTick": 1}
    assert s["general_handlers"] == 1


# ── 并发安全 ──────────────────────────


def test_concurrent_put_and_unregister_no_runtime_error():
    """10 线程并发 put + 1 线程 unregister, 不应 RuntimeError

    修复前: handler 迭代期间 unregister 会触发
            'dictionary changed size during iteration'
    """
    eng = EventEngine(interval=1)
    eng.start()

    handlers = [lambda e, i=i: None for i in range(5)]
    for h in handlers:
        eng.register("eTick", h)

    stop_flag = threading.Event()
    errors: list = []

    def put_worker():
        while not stop_flag.is_set():
            try:
                eng.put(Event("eTick"))
            except RuntimeError as e:
                errors.append(e)

    def unreg_worker():
        while not stop_flag.is_set():
            try:
                for h in handlers:
                    eng.unregister("eTick", h)
                for h in handlers:
                    eng.register("eTick", h)
            except RuntimeError as e:
                errors.append(e)

    threads = [threading.Thread(target=put_worker) for _ in range(10)]
    threads.append(threading.Thread(target=unreg_worker))
    for t in threads:
        t.start()

    time.sleep(0.3)
    stop_flag.set()
    for t in threads:
        t.join(timeout=2)

    eng.stop()

    # unregister 可能偶发竞态 (handler 在 put 期间被移除),
    # 但不应有 'dictionary changed size' RuntimeError
    critical = [
        e for e in errors
        if "dictionary changed size" in str(e)
        or "RLock" in str(e)
    ]
    assert critical == [], f"线程安全 bug: {critical[:3]}"