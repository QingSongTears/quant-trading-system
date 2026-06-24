"""
src.engine — 引擎基类 (借鉴 vnpy 4.4 trader.BaseEngine, 2026-06-24)

提供:
  - BaseEngine: 所有功能引擎 (OmsEngine / RiskEngine / RecorderEngine 等) 的根
  - 状态机: NEW → ACTIVE → STOPPED
  - 集成 src.log
  - OmsEngine: 订单管理系统 (全局缓存 + 事件订阅, lazy import)

注意: 2026-06-24 OmsEngine 用 __getattr__ lazy 加载,
      避免 src.engine <-> src.event 循环导入
"""
from .base import BaseEngine

__all__ = ["BaseEngine", "OmsEngine"]


def __getattr__(name: str):
    """Lazy load 子模块, 避免循环导入

    用户 `from src.engine import OmsEngine` 也工作, 但实际 import
    在首次访问时才发生, 此时 src.event 已加载完成
    """
    if name == "OmsEngine":
        from .oms import OmsEngine as _OmsEngine
        return _OmsEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
