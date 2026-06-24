"""
Engine 基类 — 借鉴 vnpy 4.4 trader.BaseEngine (2026-06-24)

vnpy 设计:
  - BaseEngine 抽象类, 子类 __init__ 必接收 (main_engine, event_engine, engine_name)
  - close() 默认空实现, 子类按需 override
  - 不强制 start/stop (vnpy Engine 通常 __init__ 完事, close 释放资源)

本项目适配:
  - 增加 start/stop 状态机 (is_active 标志), 因为本项目有"实盘 vs 回测"切换需求
  - 集成 src.log (彩色 + source tag)
  - main_engine / event_engine 改为可选 (允许 standalone 模式)
  - 保留 vnpy 风格: __init__ 即可做事, start/stop/close 都是幂等

典型用法:
    class OmsEngine(BaseEngine):
        def __init__(self, main_engine, event_engine, engine_name="oms"):
            super().__init__(main_engine, event_engine, engine_name)
            self.orders = {}
            # ... 加载数据

        def close(self):
            self.orders.clear()
            super().close()
"""
from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Optional

from ..log import get_logger

if TYPE_CHECKING:
    from ..event import EventEngine


class BaseEngine(ABC):
    """
    Engine 基类 — 所有功能引擎的根

    子类必覆盖:
      - __init__: 接收 (main_engine, event_engine, engine_name)
                  存储到 self.main_engine / self.event_engine / self.engine_name

    子类可 override:
      - start(): 启动逻辑, 默认幂等 (重复 start 安全)
      - stop(): 停止逻辑, 默认幂等
      - close(): 释放资源, 默认先 stop 再做清理
    """

    def __init__(
        self,
        main_engine: Optional["object"] = None,
        event_engine: Optional["EventEngine"] = None,
        engine_name: str = "",
    ) -> None:
        """
        Args:
            main_engine: MainEngine 实例 (可 None, 表示 standalone 模式)
            event_engine: EventEngine 实例 (可 None, 表示不订阅事件)
            engine_name: 引擎名, 空则用类名

        Raises:
            NotImplementedError: 直接实例化 BaseEngine (未继承)
        """
        # 防止直接实例化 BaseEngine (借鉴 vnpy 抽象 + 自定义报错)
        if type(self) is BaseEngine:
            raise NotImplementedError(
                "BaseEngine 是抽象类, 请继承并实现具体业务逻辑后再实例化"
            )

        self.main_engine = main_engine
        self.event_engine = event_engine
        self.engine_name: str = engine_name or type(self).__name__

        # 状态机: NEW (创建后) → ACTIVE (start 后) → STOPPED (stop 后)
        self._is_active: bool = False

        # 集成 src.log
        self._log = get_logger(f"src.engine.{self.engine_name}")

    # ─────────────────────────────────────────
    #  生命周期 (子类按需 override)
    # ─────────────────────────────────────────

    def start(self) -> None:
        """
        启动引擎

        默认实现: 幂等 + log. 子类在 init 数据后, 想启用时调 super().start()
        """
        if self._is_active:
            return
        self._is_active = True
        self._log.info(f"启动")

    def stop(self) -> None:
        """
        停止引擎

        默认实现: 幂等 + log. 子类停止时调 super().stop()
        """
        if not self._is_active:
            return
        self._is_active = False
        self._log.info(f"停止")

    def close(self) -> None:
        """
        关闭引擎 — 默认先 stop 再做清理

        子类应 override 此方法释放资源 (如: 关闭连接、清空缓存、停止订阅)
        """
        if self._is_active:
            self.stop()

    # ─────────────────────────────────────────
    #  状态查询
    # ─────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """是否已启动"""
        return self._is_active

    def __repr__(self) -> str:
        state = "ACTIVE" if self._is_active else "STOPPED"
        return f"<{self.__class__.__name__} name={self.engine_name} state={state}>"