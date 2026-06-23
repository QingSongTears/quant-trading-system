"""
MainEngine — 主引擎 (借鉴 vnpy.trader.MainEngine)

功能:
  - 持有 EventEngine (单例, 全项目共享)
  - 管理多个 Gateway (xtp / ptrade / qmt 等)
  - 管理多个 Strategy (V6 / V龙头 等)
  - 提供 gateway/strategy 的增删查 API

设计简化:
  - vnpy 还会管理 App (UI widgets), 我们暂不需要
  - vnpy 还会调 init_engines(), 我们延迟到具体 strategy 调用
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Type

from ..event import EventEngine
from .base_gateway import BaseGateway

logger = logging.getLogger(__name__)


class MainEngine:
    """
    主引擎 — 交易平台核心 (借鉴 vnpy.trader.MainEngine)

    用法:
        engine = MainEngine()
        engine.start()  # 启动 EventEngine 定时器

        # 添加券商网关
        xtp = engine.add_gateway(XtpGateway, "XTP_华泰")
        xtp.connect({
            "account": "xxx",
            "password": "yyy",
            "broker_id": "ZJZT",
        })

        # 查询
        xtp = engine.get_gateway("XTP_华泰")
        xtp.subscribe(SubscribeRequest("000001", "SZ"))
        xtp.query_account()
    """

    def __init__(self, event_engine: EventEngine | None = None) -> None:
        """
        Args:
            event_engine: 外部传入的事件引擎 (用于多 MainEngine 共享)
                          None 时自动创建单例
        """
        if event_engine:
            self.event_engine: EventEngine = event_engine
        else:
            self.event_engine = EventEngine()

        # 注册的网关 (name -> instance)
        self.gateways: Dict[str, BaseGateway] = {}
        # 注册的策略 (name -> instance)
        self.strategies: Dict[str, Any] = {}

        logger.info("MainEngine 初始化完成")

    # ─────────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────────

    def start(self) -> None:
        """启动事件引擎定时器"""
        self.event_engine.start()

    def stop(self) -> None:
        """停止所有网关 + 事件引擎"""
        for gw in self.gateways.values():
            try:
                gw.close()
            except Exception as e:
                logger.warning(f"关闭 {gw.gateway_name} 失败: {e}")
        self.event_engine.stop()

    # ─────────────────────────────────────────
    #  Gateway 管理
    # ─────────────────────────────────────────

    def add_gateway(
        self, gateway_class: Type[BaseGateway], gateway_name: str = "",
    ) -> BaseGateway:
        """
        添加券商网关

        Args:
            gateway_class: BaseGateway 子类
            gateway_name: 实例名, 默认使用 gateway_class.default_name

        Returns:
            网关实例
        """
        name = gateway_name or gateway_class.default_name
        if name in self.gateways:
            logger.warning(f"网关 {name} 已存在, 覆盖")

        gateway = gateway_class(self.event_engine, name)
        self.gateways[name] = gateway
        logger.info(f"添加网关: {name} ({gateway_class.__name__})")
        return gateway

    def get_gateway(self, gateway_name: str = "") -> BaseGateway:
        """
        获取网关

        Args:
            gateway_name: 网关名, 空字符串返回第一个
        """
        if not self.gateways:
            raise RuntimeError("没有注册任何网关, 请先调用 add_gateway()")
        if not gateway_name:
            return next(iter(self.gateways.values()))
        gw = self.gateways.get(gateway_name)
        if not gw:
            raise KeyError(f"网关 {gateway_name} 不存在")
        return gw

    # ─────────────────────────────────────────
    #  Strategy 管理 (简化)
    # ─────────────────────────────────────────

    def add_strategy(self, strategy_class: Type, name: str = "") -> Any:
        """添加策略实例"""
        instance_name = name or strategy_class.__name__
        strategy = strategy_class(self)
        self.strategies[instance_name] = strategy
        logger.info(f"添加策略: {instance_name}")
        return strategy

    def get_strategy(self, name: str = "") -> Any:
        """获取策略实例"""
        if not name:
            return next(iter(self.strategies.values()))
        return self.strategies[name]

    # ─────────────────────────────────────────
    #  调试
    # ─────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "event_engine": self.event_engine.stats(),
            "gateways": list(self.gateways.keys()),
            "strategies": list(self.strategies.keys()),
        }
